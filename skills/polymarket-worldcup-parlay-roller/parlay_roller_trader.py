#!/usr/bin/env python3
"""Worldcup Parlay Roller trader.

The agent writes roller_config.json once. This script then runs the streak:
enter leg 1, sell the winner post-match once the bid clears the threshold, roll
proceeds into leg 2, and continue until COMPLETE, BUSTED, BANKED, or PAUSED.
Trading decisions live in parlay_roller.py; this file handles I/O.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from parlay_roller import (
    STALE_RESOLUTION_HOURS,
    Action,
    MarketSnap,
    RollerConfig,
    StreakState,
    apply_entry_fill,
    apply_exit_proceeds,
    apply_partial_entry_fill,
    decide,
    streak_implied_price,
    validate_config,
)

SKILL_SLUG = "polymarket-worldcup-parlay-roller"
TRADE_SOURCE = "sdk:worldcup-parlay-roller"
COMBO_API_BASE = "https://combos-rfq-api.polymarket.sh"

DEFAULT_CONFIG_PATH = os.getenv("PARLAY_ROLLER_CONFIG", "roller_config.json")
DEFAULT_STATE_PATH = os.getenv("PARLAY_ROLLER_STATE", "roller_state.json")
DEFAULT_LOCK_PATH = os.getenv("PARLAY_ROLLER_LOCK", "roller.lock")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_config(path: str, now: Optional[datetime] = None) -> RollerConfig:
    try:
        with open(path) as f:
            cfg = RollerConfig.from_dict(json.load(f))
    except FileNotFoundError:
        raise SystemExit(f"[parlay-roller] config not found: {path} - see SKILL.md")
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise SystemExit(f"[parlay-roller] config invalid: {e}")

    errs = validate_config(cfg, now)
    if errs:
        for err in errs:
            print(f"[parlay-roller] config: {err}")
        raise SystemExit(2)
    return cfg


def load_state(path: str) -> Optional[StreakState]:
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return StreakState.from_dict(json.load(f))


def save_state(state: StreakState, path: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state.to_dict(), f, indent=2)
    os.replace(tmp, path)


@contextlib.contextmanager
def tick_lock(path: str):
    """Use an O_EXCL lock file so concurrent ticks cannot double-trade."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise SystemExit(f"[parlay-roller] lock {path} held - another tick is running")
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(path)


def get_client():
    from simmer_sdk import SimmerClient

    api_key = os.getenv("SIMMER_API_KEY")
    if not api_key:
        raise SystemExit("SIMMER_API_KEY not set - get one at simmer.markets/dashboard SDK tab.")
    return SimmerClient(api_key=api_key)


def _invert_price(p: Optional[float]) -> Optional[float]:
    """YES-side price -> NO-side price. None propagates."""
    if p is None:
        return None
    return max(0.0, min(1.0, round(1.0 - p, 6)))


def snap_market(client, market_id: str, side: str) -> MarketSnap:
    """Snapshot a market in the configured side's terms.

    SDK quotes (current_probability / best_bid / best_ask) are YES-outcome
    values. For a "no" leg the returned mid/best_bid/best_ask are converted to
    NO-side prices (no_mid = 1 - yes_mid, no_bid = 1 - yes_ask,
    no_ask = 1 - yes_bid). resolved_yes stays ABSOLUTE - decide() maps it
    through leg.side.
    """
    market = client.get_market_by_id(market_id)
    if market is None:
        return MarketSnap(mid=None, best_bid=None, best_ask=None, status="missing", resolved_yes=None)

    yes_mid = getattr(market, "current_probability", None)
    yes_bid = getattr(market, "best_bid", None)
    yes_ask = getattr(market, "best_ask", None)
    status = (getattr(market, "status", "") or "").lower()
    resolved_yes = getattr(market, "resolved_yes", None)
    if resolved_yes is None and status in ("resolved", "settled"):
        if yes_mid is not None and yes_mid >= 0.99:
            resolved_yes = True
        elif yes_mid is not None and yes_mid <= 0.01:
            resolved_yes = False

    if side == "no":
        mid, best_bid, best_ask = _invert_price(yes_mid), _invert_price(yes_ask), _invert_price(yes_bid)
    else:
        mid, best_bid, best_ask = yes_mid, yes_bid, yes_ask

    return MarketSnap(
        mid=mid,
        best_bid=best_bid,
        best_ask=best_ask,
        status=status,
        resolved_yes=resolved_yes,
    )


def fetch_combo_comparison(market_ids) -> Optional[float]:
    """Best-effort public combo-catalog lookup. Any failure returns None."""
    try:
        resp = requests.get(
            f"{COMBO_API_BASE}/v1/rfq/combo-markets",
            params={"limit": 200},
            timeout=5,
        )
        resp.raise_for_status()
        combos = resp.json() or []
        if isinstance(combos, dict):
            combos = combos.get("data") or combos.get("markets") or []
        wanted = {str(market_id) for market_id in market_ids}
        for combo in combos:
            legs = {
                str(leg.get("market_id") or leg.get("condition_id") or "")
                for leg in (combo.get("legs") or [])
            }
            if legs == wanted:
                return combo.get("implied_price") or combo.get("price")
    except Exception:
        return None
    return None


def print_combo_comparison(client, cfg: RollerConfig) -> None:
    """One-line combo-vs-streak price comparison at streak start. Never raises."""
    try:
        mids = [snap_market(client, leg.market_id, leg.side).mid for leg in cfg.legs]
        product = streak_implied_price(mids)
        combo = fetch_combo_comparison([leg.market_id for leg in cfg.legs])
        if combo is not None:
            print(
                f"[parlay-roller] combo-implied price {combo} vs streak leg-product "
                f"{product if product is not None else 'n/a'}"
            )
        else:
            print(
                f"[parlay-roller] streak leg-product price "
                f"{product if product is not None else 'n/a'} (combo comparison unavailable)"
            )
    except Exception as e:
        print(f"[parlay-roller] combo comparison skipped: {e}")


MAX_CANCEL_FAILURES = 3


def cancel_confirmed(client, order_id: str):
    """Attempt a cancel and report whether the venue CONFIRMED it.

    The SDK's cancel_order() reports failure two ways: raising, or returning a
    dict with "success": False (e.g. the CLOB answered not_canceled because the
    order was already matched). Only "no exception AND not success=False" counts
    as confirmed. Returns (confirmed: bool, detail: str).
    """
    try:
        res = client.cancel_order(order_id)
    except Exception as e:
        return False, str(e)
    if isinstance(res, dict) and res.get("success") is False:
        return False, str(res.get("error") or res.get("warning") or res)
    return True, ""


def held_position_shares(positions, market_id: str, side: str) -> float:
    """Sum the held side's shares for market_id across a get_positions() result."""
    total = 0.0
    for pos in positions or []:
        if isinstance(pos, dict):
            pid = pos.get("market_id")
            shares_yes = pos.get("shares_yes", 0.0) or 0.0
            shares_no = pos.get("shares_no", 0.0) or 0.0
        else:
            pid = getattr(pos, "market_id", None)
            shares_yes = getattr(pos, "shares_yes", 0.0) or 0.0
            shares_no = getattr(pos, "shares_no", 0.0) or 0.0
        if str(pid) == str(market_id):
            total += shares_no if side == "no" else shares_yes
    return total


def _verify_cancelled_entry_fills(client, state: StreakState, leg, venue: str, now: datetime):
    """Credit any shares that filled while a now-cancelled ENTRY order rested.

    A venue-confirmed cancel kills only the RESIDUAL of a resting order: the
    order can partially fill while it sits in the open book (where reconcile
    skips it), so clearing tracking blind would discard the filled shares -
    re-entering with already-spent funds, or abort BANKing while still holding.
    Compare venue-held shares against what state already credited and credit
    the excess at the order's resting limit price (limit fills cannot be worse
    than the limit). Works on sim too - get_positions is venue-parameterized.

    Returns (verified, detail). verified=False (get_positions failed, or a
    fill was detected but the resting price is missing from state) means
    holdings are UNKNOWN and the caller must NOT clear order tracking.
    """
    try:
        positions = client.get_positions(venue=venue)
    except Exception as e:
        return False, str(e)
    held = held_position_shares(positions, leg.market_id, leg.side)
    delta = round(held - state.shares, 6)
    if delta > 1e-6:
        if state.entry_price is None:
            return False, f"{delta:.2f} sh filled while resting but entry_price missing from state"
        spent = round(delta * state.entry_price, 6)
        state.shares = round(held, 6)
        state.cash = max(0.0, round(state.cash - spent, 6))
        state.log(
            f"entry filled {delta:.2f} sh while resting (venue-verified at cancel): "
            f"debited ${spent:.2f} @ {state.entry_price:.3f}",
            now,
        )
    return True, ""


def _verify_cancelled_exit_fills(client, state: StreakState, leg, venue: str, now: datetime):
    """Mirror of _verify_cancelled_entry_fills for a cancelled EXIT order.

    A partial sell while the exit rested leaves venue-held shares BELOW
    state.shares: reduce shares to the venue's count and credit the proceeds
    at the resting exit price. Same (verified, detail) contract.
    """
    try:
        positions = client.get_positions(venue=venue)
    except Exception as e:
        return False, str(e)
    held = held_position_shares(positions, leg.market_id, leg.side)
    delta = round(state.shares - held, 6)
    if delta > 1e-6:
        if state.exit_price is None:
            return False, f"{delta:.2f} sh sold while resting but exit_price missing from state"
        proceeds = round(delta * state.exit_price, 6)
        state.shares = max(0.0, round(held, 6))
        state.cash = round(state.cash + proceeds, 6)
        state.log(
            f"exit sold {delta:.2f} sh while resting (venue-verified at cancel): "
            f"credited ${proceeds:.2f} @ {state.exit_price:.3f}",
            now,
        )
    return True, ""


def _open_order_ids(open_orders) -> set:
    """Normalize a get_open_orders() response into a set of order id strings."""
    if isinstance(open_orders, dict):
        open_orders = open_orders.get("orders") or open_orders.get("data") or []
    ids = set()
    for order in open_orders or []:
        if isinstance(order, dict):
            order_id = order.get("order_id") or order.get("id")
        else:
            order_id = getattr(order, "order_id", None) or getattr(order, "id", None)
        if order_id:
            ids.add(str(order_id))
    return ids


def reconcile(client, state: StreakState, cfg: RollerConfig, now: datetime) -> None:
    """Resolve resting orders against the open-order book before deciding.

    Shock-ladder heuristic: a resting order whose id is no longer in the open
    list is treated as filled at its limit price. If the open-orders call
    fails, skip reconciliation this tick - decide() will just wait.
    """
    if not state.entry_order_id and not state.exit_order_id:
        return
    try:
        open_ids = _open_order_ids(client.get_open_orders())
    except Exception as e:
        print(f"[parlay-roller] warn: get_open_orders failed ({e}); skipping reconciliation this tick")
        return

    if state.entry_order_id and str(state.entry_order_id) not in open_ids:
        if state.entry_price and state.entry_amount:
            apply_entry_fill(
                state,
                shares_bought=round(state.entry_amount / state.entry_price, 2),
                spent=state.entry_amount,
                now=now,
            )
        else:
            print(
                "[parlay-roller] warn: entry order left the book but entry_price/entry_amount "
                "missing from state; cannot infer fill - manual review"
            )

    if state.exit_order_id and str(state.exit_order_id) not in open_ids:
        if state.exit_price is not None:
            apply_exit_proceeds(
                state, cfg, proceeds=round(state.shares * state.exit_price, 6), now=now
            )
        else:
            print(
                "[parlay-roller] warn: exit order left the book but exit_price missing "
                "from state; cannot infer proceeds - manual review"
            )


def execute(
    client,
    action: Action,
    state: StreakState,
    cfg: RollerConfig,
    leg_index: int,
    live: bool,
    venue: str,
    now: datetime,
) -> None:
    leg = cfg.legs[leg_index]
    mode = "LIVE" if live else "DRY-RUN"
    print(
        f"[parlay-roller] {mode} leg {leg_index + 1}/{len(cfg.legs)} "
        f"({leg.label}): {action.kind} - {action.reason}"
    )

    if action.kind == "wait":
        return

    if action.kind == "place_entry":
        if not live:
            state.log(f"DRY-RUN would BUY {leg.side.upper()} ${action.amount:.2f} @ {action.price:.3f}", now)
            return
        res = client.trade(
            market_id=leg.market_id,
            side=leg.side,
            action="buy",
            amount=action.amount,
            venue=venue,
            order_type="GTC",
            price=action.price,
            source=TRADE_SOURCE,
            skill_slug=SKILL_SLUG,
            reasoning=f"parlay-roller leg {leg_index + 1}: {leg.label}",
        )
        if not getattr(res, "success", False):
            print(f"[parlay-roller] entry rejected: {getattr(res, 'error', None)}")
            return
        filled = getattr(res, "shares_bought", 0.0) or 0.0
        order_id = getattr(res, "order_id", None)
        expected_shares = action.amount / action.price
        if filled >= expected_shares - 1e-6 and filled > 0:
            apply_entry_fill(state, shares_bought=filled, spent=action.amount, now=now)
        elif filled > 0:
            # Partial fill: a GTC buy can fill some shares AND leave the
            # remainder resting. Credit only what the venue confirmed;
            # entry_amount/entry_price become the residual so reconcile +
            # the TTL cancel-path manage the resting remainder.
            apply_partial_entry_fill(
                state,
                shares_bought=filled,
                amount=action.amount,
                price=action.price,
                order_id=order_id,
                now=now,
            )
        else:
            if not order_id:
                # Accepted, zero fill, no order id: the order may be live
                # on-venue but cannot be tracked. Re-placing next tick could
                # double-buy - PAUSE for manual review.
                state.phase = "PAUSED"
                state.log(
                    "PAUSED: entry accepted with no fill and no order id - "
                    "untrackable; manual review",
                    now,
                )
                return
            state.entry_order_id = order_id
            state.entry_placed_at = now
            state.entry_price = action.price
            state.entry_amount = action.amount
            state.phase = "LEG_OPEN"
            state.log(
                f"entry resting: ${action.amount:.2f} @ {action.price:.3f} "
                f"order={str(state.entry_order_id)[:12]}",
                now,
            )
        return

    if action.kind == "cancel_entry":
        if not live:
            # Order-tracking state may only change when the venue confirms the
            # outcome. A dry-run never talks to the venue, so it must not clear
            # the order id - the order may still be live (or filled) on-venue.
            print(
                f"[parlay-roller] DRY-RUN would cancel entry order "
                f"{action.order_id} ({action.reason}); state unchanged"
            )
            return
        confirmed, detail = cancel_confirmed(client, action.order_id)
        if not confirmed:
            # Keep the order id: the next tick's reconcile decides
            # filled-vs-still-open. Clearing here could double-spend.
            state.cancel_failures += 1
            print(
                f"[parlay-roller] warn: cancel unconfirmed ({detail}); keeping order "
                f"for reconciliation ({state.cancel_failures}/{MAX_CANCEL_FAILURES})"
            )
            if state.cancel_failures >= MAX_CANCEL_FAILURES:
                state.phase = "PAUSED"
                state.log(
                    f"PAUSED: cancel unconfirmed {MAX_CANCEL_FAILURES}x - order may "
                    "still be live, manual review",
                    now,
                )
            return
        # A confirmed cancel kills only the RESIDUAL: the order may have
        # partially filled while it rested (its id stayed in the open book,
        # so reconcile never saw a fill). Verify holdings against the venue
        # before clearing tracking - clearing blind would lose the filled
        # shares and re-enter with already-spent funds.
        verified, vdetail = _verify_cancelled_entry_fills(client, state, leg, venue, now)
        if not verified:
            print(
                f"[parlay-roller] warn: cancel confirmed but fill verification "
                f"unavailable ({vdetail}); keeping order for next tick"
            )
            return
        state.cancel_failures = 0  # confirmed cancel
        state.entry_order_id = None
        state.entry_placed_at = None
        state.entry_price = None
        state.entry_amount = None
        state.log(f"entry cancelled ({action.reason})", now)
        return

    if action.kind == "place_exit":
        if not live:
            state.log(f"DRY-RUN would SELL {action.shares:.2f} sh @ {action.price:.3f}", now)
            return
        res = client.trade(
            market_id=leg.market_id,
            side=leg.side,
            action="sell",
            shares=action.shares,
            venue=venue,
            order_type="GTC",
            price=action.price,
            source=TRADE_SOURCE,
            skill_slug=SKILL_SLUG,
            reasoning=f"parlay-roller exit leg {leg_index + 1} @ bid",
        )
        if not getattr(res, "success", False):
            print(f"[parlay-roller] exit rejected: {getattr(res, 'error', None)}")
            return
        sold = getattr(res, "shares_sold", 0.0) or 0.0
        order_id = getattr(res, "order_id", None)
        if sold >= action.shares - 1e-6 and sold > 0:
            apply_exit_proceeds(state, cfg, proceeds=round(sold * action.price, 6), now=now)
        elif sold > 1e-6:
            # Partial fill: bank the sold portion, keep settling the remainder.
            # The leg only advances when the LAST fill lands (apply_exit_proceeds
            # accumulates into cash). With an order id the resting remainder is
            # reconcile's to own - re-placing here could double-sell. Without
            # one the remainder is untrackable -> PAUSED (mirror of the
            # partial-entry path).
            state.shares = round(state.shares - sold, 6)
            state.cash = round(state.cash + sold * action.price, 6)
            if order_id:
                state.exit_order_id = order_id
                state.exit_price = action.price
                state.log(
                    f"exit partial fill: {sold:.2f} sh @ {action.price:.3f}; "
                    f"{state.shares:.2f} sh remaining",
                    now,
                )
            else:
                state.exit_order_id = None
                state.exit_price = None
                state.phase = "PAUSED"
                state.log(
                    f"PAUSED: exit partial fill ({sold:.2f} sh @ {action.price:.3f}) "
                    f"with untrackable remainder ({state.shares:.2f} sh, no order id) "
                    "- manual review",
                    now,
                )
        else:
            if not order_id:
                # Same hazard as the entry side: an accepted sell with no fill
                # and no order id cannot be tracked; re-selling next tick could
                # double-sell. PAUSE for manual review.
                state.phase = "PAUSED"
                state.log(
                    "PAUSED: exit accepted with no fill and no order id - "
                    "untrackable; manual review",
                    now,
                )
                return
            state.exit_order_id = order_id
            state.exit_price = action.price
            state.log(f"exit resting: {action.shares:.2f} sh @ {action.price:.3f}", now)
        return

    if action.kind == "settle_won":
        # Verify-or-pause: resolution alone is not cash. Only credit once the
        # venue no longer shows the position (i.e. redemption actually landed).
        prev_shares = state.shares
        try:
            positions = client.get_positions(venue=venue)
        except Exception as e:
            print(f"[parlay-roller] warn: get_positions failed ({e}); cannot verify redemption - waiting")
            return
        still_held = held_position_shares(positions, leg.market_id, leg.side)
        if still_held > 1e-6:
            if now > leg.expected_end + timedelta(hours=STALE_RESOLUTION_HOURS):
                state.phase = "PAUSED"
                state.log(
                    f"PAUSED: resolved in our favor but unredeemed "
                    f"{STALE_RESOLUTION_HOURS}h past expected end; manual review",
                    now,
                )
                return
            print(
                f"[parlay-roller] leg {leg_index + 1} resolved in our favor - "
                f"awaiting redemption ({still_held:.2f} sh still on venue)"
            )
            return
        apply_exit_proceeds(state, cfg, proceeds=round(prev_shares * 1.0, 6), now=now)
        return

    if action.kind == "mark_busted":
        state.phase = "BUSTED"
        state.log(f"BUSTED on leg {leg_index + 1}: {action.reason}", now)
        return

    if action.kind == "bank_and_stop":
        state.phase = "BANKED"
        state.log(f"BANKED ${state.cash:.2f}: {action.reason}", now)
        return

    if action.kind == "pause":
        state.phase = "PAUSED"
        state.log(f"PAUSED: {action.reason}; no automatic action", now)
        return


def tick(
    client,
    config_path: str,
    state_path: str,
    lock_path: str,
    live: bool,
    venue: str = "polymarket",
    now: Optional[datetime] = None,
) -> StreakState:
    now = now or now_utc()
    state_missing = not os.path.exists(state_path)
    cfg = load_config(config_path, now=now if state_missing else None)
    with tick_lock(lock_path):
        state = load_state(state_path)
        if state is None:
            state = StreakState.fresh(cfg)
            print_combo_comparison(client, cfg)
        # A non-live tick must NEVER mutate state belonging to a live streak:
        # decide-and-print only, no reconcile, no execute, no save.
        read_only = (not live) and state.live_streak
        if live:
            state.live_streak = True
        if state.phase in ("COMPLETE", "BUSTED", "BANKED", "PAUSED"):
            print(f"[parlay-roller] terminal phase {state.phase} - nothing to do")
            return state
        if read_only:
            print(
                "[parlay-roller] !! live streak detected - dry-run tick is "
                "read-only (decide-only; no state changes, no orders)"
            )
        else:
            reconcile(client, state, cfg, now)
        if state.leg_index >= len(cfg.legs):
            print(
                f"[parlay-roller] state leg_index {state.leg_index} outside config "
                f"({len(cfg.legs)} legs) - config/state mismatch, nothing to do"
            )
            if not read_only:
                save_state(state, state_path)
            return state
        leg = cfg.legs[state.leg_index]
        snap = snap_market(client, leg.market_id, leg.side)
        action = decide(state, cfg, snap, now)
        if read_only:
            print(
                f"[parlay-roller] DRY-RUN (read-only) leg {state.leg_index + 1}/"
                f"{len(cfg.legs)} ({leg.label}): would {action.kind} - {action.reason}"
            )
            return state
        execute(client, action, state, cfg, state.leg_index, live, venue, now)
        if action.kind == "cancel_entry":
            action2 = decide(state, cfg, snap, now)
            if action2.kind in ("place_entry", "bank_and_stop"):
                execute(client, action2, state, cfg, state.leg_index, live, venue, now)
        save_state(state, state_path)
        return state


def print_status(state_path: str) -> None:
    state = load_state(state_path)
    if state is None:
        print("[parlay-roller] no state file - streak not started")
        return
    print(
        f"[parlay-roller] phase={state.phase} leg={state.leg_index + 1} "
        f"cash=${state.cash:.2f} shares={state.shares:.2f} banked=${state.banked:.2f}"
    )
    for item in state.history:
        print(f"  {item['at']}  {item['msg']}")


def abort(client, config_path: str, state_path: str, live: bool, venue: str) -> StreakState:
    """Cancel working orders, sell held shares at the bid, and mark BANKED.

    Only the all-cancels-confirmed path proceeds to the sell. BANKED requires
    venue-confirmed FULL disposal: no shares held and no working orders. If
    any cancel is unconfirmed (exception OR success=False result), or the sell
    is rejected / rests / partially fills / can't be priced (no bid), the
    streak goes PAUSED with the order id(s) kept - terminalizing while shares
    or an unknown order may be live would lie about the streak's exposure.

    A CONFIRMED cancel only kills the order's residual: fills that landed
    while it rested are detected by verifying holdings against the venue
    (entry: held > state.shares -> credit the delta at the resting limit;
    exit: held < state.shares -> reduce shares, credit proceeds at the
    resting exit price) BEFORE clearing tracking. If that verification is
    unavailable, the streak goes PAUSED with tracking kept - selling or
    BANKing on unverified holdings could mis-dispose.

    Dry-run (live=False) only PRINTS what would happen - it never mutates or
    saves state, so a later live abort (or tick) still sees the real picture.
    """
    cfg = load_config(config_path, now=None)
    state = load_state(state_path)
    if state is None:
        raise SystemExit("[parlay-roller] nothing to abort - no state file")
    now = now_utc()

    if not live:
        for kind, order_id in (("entry", state.entry_order_id), ("exit", state.exit_order_id)):
            if order_id:
                print(f"[parlay-roller] abort DRY-RUN: would cancel {kind} order {order_id}")
        if state.shares > 0:
            print(f"[parlay-roller] abort DRY-RUN: would sell {state.shares:.2f} sh at bid")
        print("[parlay-roller] abort DRY-RUN: state unchanged; re-run with --live to abort")
        return state

    state.live_streak = True  # live abort mutates the streak

    failed_cancels = []
    verify_failures = []

    def _verify(kind: str, order_id: str, verify_fn) -> bool:
        """Run post-cancel fill verification for the current leg. False means
        holdings are unknown and the order's tracking must be kept."""
        if state.leg_index >= len(cfg.legs):
            verify_failures.append((kind, order_id, "state leg_index outside config"))
            return False
        ok, vdetail = verify_fn(client, state, cfg.legs[state.leg_index], venue, now)
        if not ok:
            verify_failures.append((kind, order_id, vdetail))
        return ok

    if state.entry_order_id:
        confirmed, detail = cancel_confirmed(client, state.entry_order_id)
        if not confirmed:
            failed_cancels.append(("entry", state.entry_order_id, detail))
        elif _verify("entry", state.entry_order_id, _verify_cancelled_entry_fills):
            state.entry_order_id = None
            state.entry_placed_at = None
            state.entry_price = None
            state.entry_amount = None
    if state.exit_order_id:
        confirmed, detail = cancel_confirmed(client, state.exit_order_id)
        if not confirmed:
            failed_cancels.append(("exit", state.exit_order_id, detail))
        elif _verify("exit", state.exit_order_id, _verify_cancelled_exit_fills):
            state.exit_order_id = None
            state.exit_price = None

    if failed_cancels or verify_failures:
        # Verify-or-pause: an order we could not confirm cancelled may still
        # be live, and a confirmed cancel with unverifiable fills leaves the
        # real holding unknown. Selling now could mis-dispose, and BANKED
        # would be a lie. Pause and hand it to the operator.
        for kind, order_id, detail in failed_cancels:
            print(f"[parlay-roller] !! abort: {kind} order {order_id} cancel UNCONFIRMED: {detail}")
        for kind, order_id, detail in verify_failures:
            print(
                f"[parlay-roller] !! abort: {kind} order {order_id} cancel confirmed "
                f"but fill verification unavailable: {detail}"
            )
        if failed_cancels:
            reason = (
                "abort incomplete: working order(s) could not be cancelled - "
                "verify on-venue before re-running --abort"
            )
        else:
            reason = (
                "abort incomplete: cancel confirmed but fill verification "
                "unavailable - verify holdings on-venue before re-running --abort"
            )
        state.phase = "PAUSED"
        state.log(f"PAUSED: {reason}", now)
        print(f"[parlay-roller] !! {reason}")
        save_state(state, state_path)
        print_status(state_path)
        return state

    # BANKED is reserved for: no shares held AND no working orders. The sell
    # must CONFIRM full disposal before terminalizing - anything less pauses
    # with state still tracking whatever the venue says we hold.
    if state.shares > 0:
        leg = cfg.legs[state.leg_index]
        snap = snap_market(client, leg.market_id, leg.side)
        bid = snap.best_bid
        if not bid:
            state.phase = "PAUSED"
            state.log(
                "PAUSED: abort: no bid available - shares still held; "
                "re-run --abort --live later or manage manually",
                now,
            )
            save_state(state, state_path)
            print_status(state_path)
            return state
        res = client.trade(
            market_id=leg.market_id,
            side=leg.side,
            action="sell",
            shares=state.shares,
            venue=venue,
            order_type="GTC",
            price=round(bid, 3),
            source=TRADE_SOURCE,
            skill_slug=SKILL_SLUG,
            reasoning="parlay-roller --abort",
        )
        if not getattr(res, "success", False):
            state.phase = "PAUSED"
            state.log(
                f"PAUSED: abort sell rejected ({getattr(res, 'error', None)}) - "
                "shares still held",
                now,
            )
            save_state(state, state_path)
            print_status(state_path)
            return state
        sold = getattr(res, "shares_sold", 0.0) or 0.0
        order_id = getattr(res, "order_id", None)
        if sold > 1e-6:
            state.cash = round(state.cash + sold * bid, 6)
            state.shares = max(0.0, round(state.shares - sold, 6))
            state.log(f"abort: sold {sold:.2f} sh @ {bid:.3f}", now)
        if state.shares > 1e-6:
            # Zero or partial fill: the remainder is NOT disposed. Keep
            # tracking the resting order (if any) so a future tick/--abort
            # can reconcile, but the user wants OUT - do not auto-manage.
            if order_id:
                state.exit_order_id = order_id
                state.exit_price = round(bid, 3)
            state.phase = "PAUSED"
            state.log(
                "PAUSED: abort sell working - re-run --abort --live after it "
                "fills, or manage manually",
                now,
            )
            save_state(state, state_path)
            print_status(state_path)
            return state
        state.shares = 0.0  # clear sub-tolerance dust before terminalizing

    state.phase = "BANKED"
    state.log("ABORTED by user", now)
    save_state(state, state_path)
    print_status(state_path)
    return state


def main() -> None:
    ap = argparse.ArgumentParser(description="Worldcup Parlay Roller (free)")
    ap.add_argument("--once", action="store_true", help="Single tick then exit.")
    ap.add_argument("--live", action="store_true", help="Place real orders. Default is dry-run.")
    ap.add_argument("--status", action="store_true", help="Print streak state and history.")
    ap.add_argument("--abort", action="store_true", help="Cancel orders, sell holdings at bid, stop.")
    ap.add_argument(
        "--venue",
        default=os.getenv("PARLAY_ROLLER_VENUE", "polymarket"),
        help="polymarket (default). sim is a plumbing smoke test only.",
    )
    ap.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    ap.add_argument("--state", default=DEFAULT_STATE_PATH)
    ap.add_argument("--lock", default=DEFAULT_LOCK_PATH)
    args = ap.parse_args()

    if args.status:
        print_status(args.state)
        return
    if args.venue == "sim":
        print("[parlay-roller] venue=sim is a plumbing smoke test; no real WC markets on SIM.")

    client = get_client()
    if args.abort:
        abort(client, args.config, args.state, live=args.live, venue=args.venue)
        return
    if not args.once:
        print("[parlay-roller] tip: run with --once from cron; looping mode is not needed for v1.")
    tick(client, args.config, args.state, args.lock, live=args.live, venue=args.venue)


if __name__ == "__main__":
    main()
