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
from datetime import datetime, timezone
from typing import Optional

import requests

from parlay_roller import (
    Action,
    MarketSnap,
    RollerConfig,
    StreakState,
    apply_entry_fill,
    apply_exit_proceeds,
    decide,
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


def snap_market(client, market_id: str) -> MarketSnap:
    market = client.get_market_by_id(market_id)
    if market is None:
        return MarketSnap(mid=None, best_bid=None, best_ask=None, status="missing", resolved_yes=None)

    mid = getattr(market, "current_probability", None)
    status = (getattr(market, "status", "") or "").lower()
    resolved_yes = getattr(market, "resolved_yes", None)
    if resolved_yes is None and status in ("resolved", "settled"):
        if mid is not None and mid >= 0.99:
            resolved_yes = True
        elif mid is not None and mid <= 0.01:
            resolved_yes = False

    return MarketSnap(
        mid=mid,
        best_bid=getattr(market, "best_bid", None),
        best_ask=getattr(market, "best_ask", None),
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
        if filled > 0:
            apply_entry_fill(state, shares_bought=filled, spent=action.amount, now=now)
        else:
            state.entry_order_id = getattr(res, "order_id", None)
            state.entry_placed_at = now
            state.phase = "LEG_OPEN"
            state.log(
                f"entry resting: ${action.amount:.2f} @ {action.price:.3f} "
                f"order={str(state.entry_order_id)[:12]}",
                now,
            )
        return

    if action.kind == "cancel_entry":
        if live:
            try:
                client.cancel_order(action.order_id)
            except Exception as e:
                print(f"[parlay-roller] warn: cancel failed: {e}")
        state.entry_order_id = None
        state.entry_placed_at = None
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
        if sold > 0:
            apply_exit_proceeds(state, cfg, proceeds=round(sold * action.price, 6), now=now)
        else:
            state.exit_order_id = getattr(res, "order_id", None)
            state.log(f"exit resting: {action.shares:.2f} sh @ {action.price:.3f}", now)
        return

    if action.kind == "settle_won":
        apply_exit_proceeds(state, cfg, proceeds=round(state.shares * 1.0, 6), now=now)
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
        state = load_state(state_path) or StreakState.fresh(cfg)
        if state.phase in ("COMPLETE", "BUSTED", "BANKED", "PAUSED"):
            print(f"[parlay-roller] terminal phase {state.phase} - nothing to do")
            return state
        snap = snap_market(client, cfg.legs[state.leg_index].market_id)
        action = decide(state, cfg, snap, now)
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


def abort(client, config_path: str, state_path: str, live: bool) -> StreakState:
    """Cancel working orders, sell held shares at the bid, and mark BANKED."""
    cfg = load_config(config_path, now=None)
    state = load_state(state_path)
    if state is None:
        raise SystemExit("[parlay-roller] nothing to abort - no state file")
    now = now_utc()

    for order_id in (state.entry_order_id, state.exit_order_id):
        if order_id and live:
            try:
                client.cancel_order(order_id)
            except Exception as e:
                print(f"[parlay-roller] warn: cancel {order_id} failed: {e}")
    state.entry_order_id = None
    state.exit_order_id = None

    if state.shares > 0:
        leg = cfg.legs[state.leg_index]
        snap = snap_market(client, leg.market_id)
        bid = snap.best_bid
        if bid and live:
            res = client.trade(
                market_id=leg.market_id,
                side=leg.side,
                action="sell",
                shares=state.shares,
                venue=cfg.venue,
                order_type="GTC",
                price=round(bid, 3),
                source=TRADE_SOURCE,
                skill_slug=SKILL_SLUG,
                reasoning="parlay-roller --abort",
            )
            sold = getattr(res, "shares_sold", 0.0) or 0.0
            state.cash += round(sold * bid, 6)
            state.shares = max(0.0, state.shares - sold)
            state.log(f"abort: sold {sold:.2f} sh @ {bid:.3f}", now)
        elif not live:
            state.log(f"abort DRY-RUN: would sell {state.shares:.2f} sh at bid", now)
        else:
            state.log("abort: no bid available - shares left to resolution", now)

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
        abort(client, args.config, args.state, live=args.live)
        return
    if not args.once:
        print("[parlay-roller] tip: run with --once from cron; looping mode is not needed for v1.")
    tick(client, args.config, args.state, args.lock, live=args.live, venue=args.venue)


if __name__ == "__main__":
    main()
