"""Shock Ladder — pure strategy logic (no SDK, no network).

Roan's (@RohOnChain) FIFA shock-fade ladder, the execution half of Simmer's WC
Shock Ladder. The server detects a price shock, classifies it into a 5-dim bucket,
and emits a signal with the bucket's depth percentiles; this module turns that
signal into the concrete ladder (rung prices + sizes) and exit prices.

Kept pure so the strategy math is unit-tested in isolation — the trader script
(`shock_ladder_trader.py`) handles polling, SDK order placement, fill detection,
and the exit loop.

Strategy (canonical, from Roan): a shock is a price DROP on `side`. Place 4 limit
BUYs at ``pre_price - percentile_depth`` (p50/p75/p90/p95), GTD ~60s, weighted
10/20/30/40% of the per-shock stake (deeper rungs get more — bigger drops have more
recovery upside). Exit each fill at ``fill + ~4¢``. Filter to moderate-favoritism
buckets (pre-shock price 0.75-0.85) by default — Roan's tuning finding.

Spec: ``simmer/_dev/active/_worldcup-2026/shock-ladder-skill-spec.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

# Polymarket CLOB price bounds.
_PRICE_MIN = 0.001
_PRICE_MAX = 0.999

# The 4 ladder rungs, shallow→deep. Order is load-bearing for weight mapping.
RUNG_LABELS = ("p50", "p75", "p90", "p95")

# Default per-rung weights of the per-shock stake (Roan). Sum = 1.0.
DEFAULT_LADDER_WEIGHTS: Dict[str, float] = {"p50": 0.10, "p75": 0.20, "p90": 0.30, "p95": 0.40}

# Fixed recovery target (cents) for the exit leg.
DEFAULT_EXIT_TARGET_CENTS: float = 4.0

# GTD order lifetime (seconds) for each rung.
DEFAULT_ORDER_TTL_S: int = 60

# Favoritism bands (dim 2 of the bucket key) the skill acts on. Roan: moderate_fav
# (pre-shock 0.75-0.85) was significantly more profitable. Skill-side + remixable.
DEFAULT_ALLOWED_FAVORITISM = frozenset({"moderate"})


def _clamp_price(p: float) -> float:
    return max(_PRICE_MIN, min(_PRICE_MAX, p))


def favoritism_of(bucket_key: str) -> str:
    """Dim 2 (favoritism) out of a ``league|favoritism|depth|time|goal`` key."""
    parts = (bucket_key or "").split("|")
    return parts[1] if len(parts) > 1 else "unknown"


def passes_bucket_filter(bucket_key: str, allowed_favoritism=DEFAULT_ALLOWED_FAVORITISM) -> bool:
    """True if the signal's favoritism band is one we act on.

    ``allowed_favoritism`` empty/None ⇒ act on everything (no filter).
    """
    if not allowed_favoritism:
        return True
    return favoritism_of(bucket_key) in allowed_favoritism


@dataclass(frozen=True)
class Rung:
    """One ladder leg. ``price`` is the limit BUY price for ``side``; ``stake`` is
    the spend allocated to this rung."""

    label: str          # "p50" / "p75" / "p90" / "p95"
    depth_cents: float
    price: float
    stake: float


def compute_ladder(
    pre_price: float,
    percentile_depths: Dict[str, float],
    total_stake: float,
    weights: Dict[str, float] = DEFAULT_LADDER_WEIGHTS,
) -> List[Rung]:
    """Build the rung list from a shock signal.

    ``percentile_depths`` is ``{"p50":6.0,"p75":9.0,...}`` in cents (the bucket's
    depth distribution). Each rung's limit price = ``pre_price - depth/100``,
    clamped to CLOB bounds; stake = ``total_stake * weight``. Rungs whose depth is
    missing or whose price clamps below the floor (≤ _PRICE_MIN) are dropped — a
    bid at the price floor can't catch a bounce.

    Pure: no rounding to "nice" numbers beyond 4dp (CLOB tick precision); raw
    percentiles are never pre-rounded upstream.
    """
    if pre_price is None or total_stake is None or total_stake <= 0:
        return []
    rungs: List[Rung] = []
    for label in RUNG_LABELS:
        depth = percentile_depths.get(label)
        if depth is None:
            continue
        raw_price = pre_price - (depth / 100.0)
        price = round(_clamp_price(raw_price), 4)
        # Drop a rung that clamps to the floor — it would never fill meaningfully
        # and a $0.001 bid is noise, not a ladder leg.
        if price <= _PRICE_MIN:
            continue
        weight = weights.get(label, 0.0)
        stake = round(total_stake * weight, 4)
        if stake <= 0:
            continue
        rungs.append(Rung(label=label, depth_cents=float(depth), price=price, stake=stake))
    return rungs


def exit_price(fill_price: float, target_cents: float = DEFAULT_EXIT_TARGET_CENTS) -> float:
    """Limit SELL price for a filled rung: ``fill + target``, clamped to CLOB max."""
    return round(_clamp_price(fill_price + (target_cents / 100.0)), 4)


@dataclass(frozen=True)
class PlanDecision:
    """Outcome of evaluating a signal: either a ladder to place, or a skip reason."""

    act: bool
    skip_reason: Optional[str]
    rungs: List[Rung]


def plan_from_signal(
    signal: dict,
    total_stake: float,
    allowed_favoritism=DEFAULT_ALLOWED_FAVORITISM,
    weights: Dict[str, float] = DEFAULT_LADDER_WEIGHTS,
) -> PlanDecision:
    """Top-level: validate a ``type=="shock_ladder"`` signal, apply the bucket
    filter, and compute the ladder. Returns a PlanDecision the trader acts on.

    Skip reasons (the trader DELETEs the signal + logs, never retries these):
    ``wrong_type`` · ``malformed`` · ``filtered_bucket`` · ``no_rungs``.
    """
    if signal.get("type") != "shock_ladder":
        return PlanDecision(False, "wrong_type", [])

    pre_price = signal.get("pre_price")
    side = signal.get("side")
    depths = signal.get("percentile_depths") or {}
    if pre_price is None or not side or not signal.get("market_id") or not depths:
        return PlanDecision(False, "malformed", [])

    if not passes_bucket_filter(signal.get("bucket_key", ""), allowed_favoritism):
        return PlanDecision(False, "filtered_bucket", [])

    rungs = compute_ladder(float(pre_price), depths, total_stake, weights)
    if not rungs:
        return PlanDecision(False, "no_rungs", [])

    return PlanDecision(True, None, rungs)
