"""Worldcup Parlay Roller pure decision module.

This module is deterministic and I/O-free: config parsing, validation, the
streak state machine, and per-tick decisions. The trader script feeds it market
snapshots and executes the returned actions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional

MAX_LEGS = 5
DEFAULT_ENTRY_TOLERANCE = 0.01
DEFAULT_EXIT_BID_THRESHOLD = 0.97
DEFAULT_LOSS_FLOOR = 0.03
DEFAULT_ROLL_BUFFER_MIN = 15
DEFAULT_ENTRY_TTL_S = 120
STALE_RESOLUTION_HOURS = 6
PRICE_CAP = 0.99


def parse_dt(s: str) -> datetime:
    """Parse ISO-8601, tolerating a trailing Z."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass
class Leg:
    market_id: str
    side: str
    label: str
    resolution_note: str
    kickoff: datetime
    expected_end: datetime

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kickoff"] = self.kickoff.isoformat()
        d["expected_end"] = self.expected_end.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Leg":
        kickoff = parse_dt(d["kickoff"])
        expected_end = (
            parse_dt(d["expected_end"])
            if d.get("expected_end")
            else kickoff + timedelta(minutes=135)
        )
        return cls(
            market_id=d["market_id"],
            side=d["side"],
            label=d["label"],
            resolution_note=d["resolution_note"],
            kickoff=kickoff,
            expected_end=expected_end,
        )


@dataclass
class RollerConfig:
    legs: List[Leg]
    stake_usd: float
    entry_tolerance: float = DEFAULT_ENTRY_TOLERANCE
    exit_bid_threshold: float = DEFAULT_EXIT_BID_THRESHOLD
    loss_floor: float = DEFAULT_LOSS_FLOOR
    roll_buffer_min: int = DEFAULT_ROLL_BUFFER_MIN
    entry_ttl_s: int = DEFAULT_ENTRY_TTL_S
    bank_half_after: Optional[int] = None
    venue: str = "polymarket"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["legs"] = [leg.to_dict() for leg in self.legs]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RollerConfig":
        kw = dict(d)
        kw["legs"] = [Leg.from_dict(x) for x in d.get("legs", [])]
        return cls(**kw)


def validate_config(cfg: RollerConfig, now: Optional[datetime]) -> List[str]:
    """Return human-readable config errors. Empty list means valid."""
    errs: List[str] = []
    if not cfg.legs:
        errs.append("config has no legs - at least 1 leg required")
    if len(cfg.legs) > MAX_LEGS:
        errs.append(f"{len(cfg.legs)} legs - max {MAX_LEGS} legs allowed")
    if cfg.stake_usd <= 0:
        errs.append(f"stake must be > 0 (got {cfg.stake_usd})")
    if cfg.entry_tolerance < 0:
        errs.append("entry_tolerance must be >= 0")
    if not 0 < cfg.exit_bid_threshold <= 1:
        errs.append("exit_bid_threshold must be in (0, 1]")
    if not 0 <= cfg.loss_floor < cfg.exit_bid_threshold:
        errs.append("loss_floor must be >= 0 and below exit_bid_threshold")
    if cfg.bank_half_after is not None and cfg.bank_half_after < 1:
        errs.append("bank_half_after must be a 1-based leg number")

    for i, leg in enumerate(cfg.legs):
        if leg.side not in ("yes", "no"):
            errs.append(
                f"leg {i + 1} ({leg.label}): side must be 'yes' or 'no' (got {leg.side!r})"
            )
        if not leg.resolution_note.strip():
            errs.append(f"leg {i + 1} ({leg.label}): resolution_note is required")
        if leg.expected_end <= leg.kickoff:
            errs.append(f"leg {i + 1} ({leg.label}): expected_end must be after kickoff")

    if now is not None and cfg.legs and cfg.legs[0].kickoff <= now:
        errs.append(f"leg 1 ({cfg.legs[0].label}) already kicked off - never enter mid-match")

    for i in range(1, len(cfg.legs)):
        prev, cur = cfg.legs[i - 1], cfg.legs[i]
        if cur.kickoff < prev.kickoff:
            errs.append(f"legs not in kickoff order: leg {i + 1} kicks off before leg {i}")
        elif cur.kickoff < prev.expected_end:
            errs.append(
                f"legs {i} and {i + 1} overlap - leg {i + 1} kicks off before leg {i} ends "
                "(simultaneous matches cannot roll proceeds)"
            )
    return errs


PHASES = ("CONFIGURED", "LEG_OPEN", "SETTLING", "COMPLETE", "BUSTED", "BANKED", "PAUSED")


@dataclass
class MarketSnap:
    """What the trader observed about the current leg's market this tick.

    mid/best_bid/best_ask are SIDE-RELATIVE: the trader converts YES-outcome
    SDK quotes to the configured leg side's prices before building the snap
    (for a "no" leg: no_mid = 1 - yes_mid, no_bid = 1 - yes_ask,
    no_ask = 1 - yes_bid). resolved_yes stays ABSOLUTE; decide() maps it
    through leg.side.
    """

    mid: Optional[float]
    best_bid: Optional[float]
    best_ask: Optional[float]
    status: str
    resolved_yes: Optional[bool]


@dataclass
class Action:
    kind: str
    price: Optional[float] = None
    amount: Optional[float] = None
    shares: Optional[float] = None
    order_id: Optional[str] = None
    reason: str = ""


@dataclass
class StreakState:
    phase: str
    leg_index: int
    cash: float
    shares: float
    entry_order_id: Optional[str] = None
    entry_placed_at: Optional[datetime] = None
    entry_price: Optional[float] = None
    entry_amount: Optional[float] = None
    exit_order_id: Optional[str] = None
    exit_price: Optional[float] = None
    cancel_failures: int = 0
    banked: float = 0.0
    # True once a LIVE tick has created or mutated this streak. A dry-run tick
    # on a live streak must be read-only: it may decide and print, but never
    # mutate order-tracking state (order ids, shares, cash, phase) or save.
    # Defaults False for fresh states and for state files predating the field.
    live_streak: bool = False
    history: List[dict] = field(default_factory=list)

    @classmethod
    def fresh(cls, cfg: RollerConfig) -> "StreakState":
        st = cls(phase="CONFIGURED", leg_index=0, cash=cfg.stake_usd, shares=0.0)
        st.log(f"configured: {len(cfg.legs)} legs, stake ${cfg.stake_usd:.2f}")
        return st

    def log(self, msg: str, at: Optional[datetime] = None) -> None:
        at = at or datetime.now(timezone.utc)
        self.history.append({"at": at.isoformat(), "msg": msg})

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_placed_at"] = self.entry_placed_at.isoformat() if self.entry_placed_at else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StreakState":
        kw = dict(d)
        kw["entry_placed_at"] = parse_dt(d["entry_placed_at"]) if d.get("entry_placed_at") else None
        return cls(**kw)


def entry_price(mid: float, tolerance: float) -> float:
    """Limit entry at mid + tolerance, capped. No market-order chasing."""
    return round(min(mid + tolerance, PRICE_CAP), 3)


def decide(state: StreakState, cfg: RollerConfig, snap: MarketSnap, now: datetime) -> Action:
    """Return one decision for the current tick and current leg."""
    if state.phase in ("COMPLETE", "BUSTED", "BANKED", "PAUSED"):
        return Action("wait", reason=f"terminal phase {state.phase}")

    if state.leg_index >= len(cfg.legs):
        return Action("wait", reason="state leg_index outside config")

    leg = cfg.legs[state.leg_index]

    if state.shares <= 0:
        entry_deadline = leg.kickoff - timedelta(minutes=cfg.roll_buffer_min)
        if state.entry_order_id:
            age = (now - state.entry_placed_at).total_seconds() if state.entry_placed_at else 0.0
            if age > cfg.entry_ttl_s or now >= entry_deadline:
                reason = "entry TTL expired" if age > cfg.entry_ttl_s else "entry window closing"
                return Action("cancel_entry", order_id=state.entry_order_id, reason=reason)
            return Action("wait", reason="entry order working")
        if now >= entry_deadline:
            return Action(
                "bank_and_stop",
                reason=(
                    f"leg {state.leg_index + 1} not entered by kickoff-{cfg.roll_buffer_min}min; "
                    "banking cash"
                ),
            )
        if snap.mid is None:
            return Action("wait", reason="no mid price available; will not guess")
        return Action(
            "place_entry",
            price=entry_price(snap.mid, cfg.entry_tolerance),
            amount=round(state.cash, 2),
            reason=f"enter leg {state.leg_index + 1}: {leg.label}",
        )

    if now < leg.expected_end:
        return Action("wait", reason="match in progress; never act mid-match")

    return _decide_settle(state, cfg, leg, snap, now)


def _decide_settle(state: StreakState, cfg: RollerConfig, leg: Leg, snap: MarketSnap, now: datetime) -> Action:
    """Post-match decision for a held leg."""
    if state.exit_order_id:
        return Action("wait", reason="exit order working")

    if snap.status in ("voided", "cancelled"):
        return Action("pause", reason=f"market {leg.market_id} status={snap.status}; manual review")

    if snap.resolved_yes is not None:
        we_won = (snap.resolved_yes and leg.side == "yes") or (
            not snap.resolved_yes and leg.side == "no"
        )
        if we_won:
            return Action(
                "settle_won",
                shares=state.shares,
                reason=f"leg {state.leg_index + 1} resolved in our favor; redeem proceeds",
            )
        return Action("mark_busted", reason=f"leg {state.leg_index + 1} resolved against us")

    bid = snap.best_bid
    if bid is not None and bid >= cfg.exit_bid_threshold:
        return Action(
            "place_exit",
            price=round(bid, 3),
            shares=state.shares,
            reason=f"bid {bid:.3f} >= {cfg.exit_bid_threshold}; sell winner, roll fast",
        )
    if bid is not None and bid < cfg.loss_floor:
        return Action(
            "mark_busted",
            reason=f"post-match bid {bid:.3f} < floor {cfg.loss_floor}; leg lost",
        )
    if now > leg.expected_end + timedelta(hours=STALE_RESOLUTION_HOURS):
        return Action(
            "pause",
            reason=f"unresolved {STALE_RESOLUTION_HOURS}h past expected end; manual review",
        )
    return Action("wait", reason="post-match, mid-priced; holding to resolution")


def apply_entry_fill(state: StreakState, shares_bought: float, spent: float, now: datetime) -> None:
    state.shares = shares_bought
    state.cash = max(0.0, round(state.cash - spent, 6))
    state.entry_order_id = None
    state.entry_placed_at = None
    state.entry_price = None
    state.entry_amount = None
    state.cancel_failures = 0  # reconciled fill: the order is no longer live
    state.phase = "LEG_OPEN"
    state.log(f"leg {state.leg_index + 1} entry filled: {shares_bought:.2f} sh for ${spent:.2f}", now)


def apply_exit_proceeds(state: StreakState, cfg: RollerConfig, proceeds: float, now: datetime) -> None:
    """A won leg's (final) proceeds landed. Roll to the next leg or complete.

    Proceeds ADD to existing cash: a partial exit fill banks its proceeds into
    state.cash while the remainder keeps settling, so the closing fill must
    accumulate rather than overwrite. Full-fill behavior is unchanged (cash is
    0 while holding).
    """
    leg_no = state.leg_index + 1
    state.shares = 0.0
    state.exit_order_id = None
    state.exit_price = None
    cash = round(state.cash + proceeds, 6)
    if cfg.bank_half_after is not None and leg_no >= cfg.bank_half_after and state.banked == 0.0:
        half = round(cash / 2, 6)
        state.banked = half
        cash = round(cash - half, 6)
        state.log(f"take-profit: banked ${half:.2f} after leg {leg_no}", now)
    state.cash = cash
    if state.leg_index + 1 >= len(cfg.legs):
        state.phase = "COMPLETE"
        state.log(f"streak COMPLETE: final cash ${state.cash:.2f} + banked ${state.banked:.2f}", now)
    else:
        state.leg_index += 1
        state.phase = "LEG_OPEN"
        state.log(f"rolled ${state.cash:.2f} into leg {state.leg_index + 1}", now)


def streak_implied_price(leg_prices: List[Optional[float]]) -> Optional[float]:
    """Naive independent-leg product for display-only combo comparison."""
    if not leg_prices or any(p is None for p in leg_prices):
        return None
    out = 1.0
    for p in leg_prices:
        out *= p
    return round(out, 6)
