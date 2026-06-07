#!/usr/bin/env python3
"""Shock Ladder trader — consume server shock signals, place Roan's limit-buy ladder.

The execution half of Simmer's WC Shock Ladder (Pro). The server detects a price
shock on a WC market, classifies it, sizes it from self-accumulated depth
percentiles, and emits a ``type=="shock_ladder"`` signal on
``/api/sdk/reactor/pending``. This script consumes those signals and places the
4-rung ladder + the ~4¢ exit.

Strategy math lives in ``shock_ladder.py`` (pure, unit-tested). This script is the
I/O layer: poll → plan → place → manage fills/exits → cancel unfilled → delete.

Modeled on the ``polymarket-copytrading`` reactor mode. Differences:
- **Polymarket-only.** The ladder is a CLOB limit-order mechanic; $SIM (LMSR) has no
  order book to rest bids on. ``--venue sim`` is a plumbing smoke test only.
- **GTC + explicit cancel.** The SDK can't set a GTD expiry duration, so rungs are
  placed GTC and the unfilled ones are cancelled at the TTL — precise 60s control.
- **Dry-run by default.** Pass ``--live`` to actually place orders. Start dry.

Usage:
    python shock_ladder_trader.py --once                 # dry-run, single poll (cron)
    python shock_ladder_trader.py --once --live          # live, single poll
    python shock_ladder_trader.py --live                 # live loop
    python shock_ladder_trader.py --once --venue sim     # plumbing smoke test

Spec: simmer/_dev/active/_worldcup-2026/shock-ladder-skill-spec.md
"""

from __future__ import annotations

import argparse
import os
import time
from typing import List, Optional

from shock_ladder import (
    DEFAULT_ALLOWED_FAVORITISM,
    DEFAULT_EXIT_TARGET_CENTS,
    DEFAULT_ORDER_TTL_S,
    PlanDecision,
    Rung,
    exit_price,
    plan_from_signal,
)

SKILL_SLUG = "polymarket-shock-ladder"
TRADE_SOURCE = "sdk:shock-ladder:reactor"

# --- config (env + CLI; CLI wins) ---

# Per-shock stake (total spend split across the 4 rungs). Conservative default for
# the small-size live test with Herman; raise once the strategy proves out.
DEFAULT_STAKE_USD = float(os.getenv("SHOCK_LADDER_STAKE_USD", "15"))
DEFAULT_POLL_INTERVAL_S = float(os.getenv("SHOCK_LADDER_POLL_INTERVAL_S", "2"))


class Config:
    def __init__(self, args):
        self.live: bool = args.live
        self.once: bool = args.once
        self.venue: str = args.venue
        self.stake: float = args.stake
        self.ttl_s: float = args.ttl
        self.exit_cents: float = args.exit_cents
        self.poll_interval_s: float = args.interval
        # Empty --buckets ("") disables the favoritism filter (act on all).
        if args.buckets is None:
            self.allowed_favoritism = DEFAULT_ALLOWED_FAVORITISM
        else:
            self.allowed_favoritism = frozenset(b.strip() for b in args.buckets.split(",") if b.strip())

    @property
    def mode(self) -> str:
        return "LIVE" if self.live else "DRY-RUN"


# --- SDK client ---

def get_client():
    from simmer_sdk import SimmerClient
    api_key = os.getenv("SIMMER_API_KEY")
    if not api_key:
        raise SystemExit("SIMMER_API_KEY not set — get one at simmer.markets/dashboard → SDK tab.")
    return SimmerClient(api_key=api_key)


# --- signal pipe (mirror copytrading reactor) ---

def poll_pending(client) -> List[dict]:
    """GET the Pro user's pending signals; return only shock_ladder ones."""
    resp = client._request("GET", "/api/sdk/reactor/pending")
    signals = (resp or {}).get("reactor_signals") or []
    return [s for s in signals if s.get("type") == "shock_ladder"]


def delete_signal(client, shock_id: str) -> None:
    """Remove a handled signal so it isn't reprocessed (else it TTLs out server-side)."""
    try:
        client._request("DELETE", f"/api/sdk/reactor/pending/{shock_id}")
    except Exception as e:
        print(f"[shock-ladder] warn: delete signal {shock_id[:16]}… failed: {e}")


# --- execution ---

class Placement:
    """A placed (or would-be) rung order."""
    def __init__(self, rung: Rung, order_id: Optional[str], shares: float):
        self.rung = rung
        self.order_id = order_id
        self.shares = shares
        self.exit_placed = False


def place_ladder(client, signal: dict, rungs: List[Rung], cfg: Config) -> List[Placement]:
    """Place the 4 GTC limit buys (or log them in dry-run). Returns placements."""
    market_id = signal["market_id"]
    side = signal["side"].lower()
    placements: List[Placement] = []
    for r in rungs:
        shares_est = round(r.stake / r.price, 2) if r.price > 0 else 0.0
        tag = f"{r.label} {r.stake:.2f} USD @ {r.price:.3f} (~{shares_est:.1f} sh)"
        if not cfg.live:
            print(f"[shock-ladder] DRY-RUN would place BUY {side.upper()} {tag}")
            placements.append(Placement(r, None, shares_est))
            continue
        try:
            res = client.trade(
                market_id=market_id, side=side, amount=r.stake, action="buy",
                venue=cfg.venue, order_type="GTC", price=r.price,
                allow_rebuy=True, source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
                reasoning=f"shock-ladder {r.label} rung on bucket {signal.get('bucket_key')}",
                signal_data={"shock_id": signal.get("shock_id"), "rung": r.label,
                             "bucket_key": signal.get("bucket_key")},
            )
        except Exception as e:
            print(f"[shock-ladder] ❌ rung {r.label} trade error: {e}")
            continue
        if not getattr(res, "success", False):
            print(f"[shock-ladder] ❌ rung {r.label} rejected: {getattr(res, 'error', None)}")
            continue
        oid = getattr(res, "order_id", None)
        print(f"[shock-ladder] ✅ placed BUY {side.upper()} {tag} order_id={str(oid)[:12]}…")
        placements.append(Placement(r, oid, shares_est))
    return placements


def manage_lifecycle(client, signal: dict, placements: List[Placement], cfg: Config) -> None:
    """Within the TTL: detect fills → place +4¢ exits; at the TTL → cancel unfilled.

    Fill heuristic (v1): a rung whose order_id is no longer in get_open_orders (and
    we didn't cancel it) is treated as filled. Exact partial-fill reconciliation is a
    refinement. Dry-run logs the exits it would place and returns.
    """
    side = signal["side"].lower()
    live_rungs = [p for p in placements if p.order_id or not cfg.live]

    if not cfg.live:
        for p in live_rungs:
            ep = exit_price(p.rung.price, cfg.exit_cents)
            print(f"[shock-ladder] DRY-RUN on fill of {p.rung.label} would place SELL "
                  f"{side.upper()} {p.shares:.1f} sh @ {ep:.3f} (+{cfg.exit_cents:.0f}¢)")
        return

    deadline = time.monotonic() + cfg.ttl_s
    while time.monotonic() < deadline:
        try:
            open_resp = client.get_open_orders() or {}
            open_ids = {o.get("order_id") for o in (open_resp.get("orders") or [])}
        except Exception as e:
            print(f"[shock-ladder] warn: open-orders poll failed: {e}")
            open_ids = None
        if open_ids is not None:
            for p in placements:
                if p.exit_placed or not p.order_id:
                    continue
                if p.order_id not in open_ids:  # left the book → presumed filled
                    _place_exit(client, signal, p, side, cfg)
        if all(p.exit_placed for p in placements if p.order_id):
            break
        time.sleep(cfg.poll_interval_s)

    # TTL reached — cancel any rung still resting (unfilled, no exit placed).
    for p in placements:
        if p.order_id and not p.exit_placed:
            try:
                client.cancel_order(p.order_id)
                print(f"[shock-ladder] ⏱ cancelled unfilled rung {p.rung.label} ({str(p.order_id)[:12]}…)")
            except Exception as e:
                print(f"[shock-ladder] warn: cancel {p.rung.label} failed: {e}")


def _place_exit(client, signal: dict, p: Placement, side: str, cfg: Config) -> None:
    ep = exit_price(p.rung.price, cfg.exit_cents)
    try:
        res = client.trade(
            market_id=signal["market_id"], side=side, action="sell", shares=p.shares,
            venue=cfg.venue, order_type="GTC", price=ep, source=TRADE_SOURCE,
            skill_slug=SKILL_SLUG, reasoning=f"shock-ladder exit {p.rung.label} +{cfg.exit_cents:.0f}c",
        )
        if getattr(res, "success", False):
            p.exit_placed = True
            print(f"[shock-ladder] 🎯 exit placed: SELL {side.upper()} {p.shares:.1f} sh @ {ep:.3f}")
        else:
            print(f"[shock-ladder] warn: exit {p.rung.label} rejected: {getattr(res, 'error', None)}")
    except Exception as e:
        print(f"[shock-ladder] warn: exit {p.rung.label} error: {e}")


def process_signal(client, signal: dict, cfg: Config) -> str:
    """Plan → place → manage → delete one shock signal. Returns an outcome string."""
    plan: PlanDecision = plan_from_signal(signal, cfg.stake, cfg.allowed_favoritism)
    sid = signal.get("shock_id", "?")
    if not plan.act:
        print(f"[shock-ladder] skip {sid} ({plan.skip_reason}) bucket={signal.get('bucket_key')}")
        delete_signal(client, sid)
        return f"skipped:{plan.skip_reason}"

    print(f"[shock-ladder] ▶ {cfg.mode} shock {sid} bucket={signal.get('bucket_key')} "
          f"pre={signal.get('pre_price')} side={signal.get('side')} "
          f"depths={signal.get('percentile_depths')} src={signal.get('depth_source')}")
    placements = place_ladder(client, signal, plan.rungs, cfg)
    manage_lifecycle(client, signal, placements, cfg)
    delete_signal(client, sid)
    return f"handled:{len(placements)}rungs"


# --- runners ---

def run_once(client, cfg: Config) -> int:
    signals = poll_pending(client)
    if not signals:
        print(f"[shock-ladder] {cfg.mode}: 0 pending shock signals")
        return 0
    print(f"[shock-ladder] {cfg.mode}: {len(signals)} pending shock signal(s)")
    for s in signals:
        try:
            print("  →", process_signal(client, s, cfg))
        except Exception as e:
            print(f"[shock-ladder] ❌ signal error: {e}")
    return len(signals)


def run_loop(client, cfg: Config) -> None:
    print(f"[shock-ladder] {cfg.mode} loop: polling /api/sdk/reactor/pending every {cfg.poll_interval_s}s")
    while True:
        try:
            run_once(client, cfg)
        except Exception as e:
            print(f"[shock-ladder] loop error: {e}")
        time.sleep(max(cfg.poll_interval_s, 1.0))


def main():
    ap = argparse.ArgumentParser(description="WC Shock Ladder trader (Pro)")
    ap.add_argument("--live", action="store_true", help="Place real orders (default: dry-run).")
    ap.add_argument("--once", action="store_true", help="Single poll then exit (cron-friendly).")
    ap.add_argument("--venue", default=os.getenv("SHOCK_LADDER_VENUE", "polymarket"),
                    help="polymarket (default). sim = plumbing smoke test only (no real ladder).")
    ap.add_argument("--stake", type=float, default=DEFAULT_STAKE_USD,
                    help=f"Per-shock total stake across the 4 rungs (default {DEFAULT_STAKE_USD:.0f}).")
    ap.add_argument("--ttl", type=float, default=float(DEFAULT_ORDER_TTL_S),
                    help=f"Seconds before unfilled rungs are cancelled (default {DEFAULT_ORDER_TTL_S}).")
    ap.add_argument("--exit-cents", type=float, default=DEFAULT_EXIT_TARGET_CENTS, dest="exit_cents",
                    help=f"Recovery target in cents for the exit sell (default {DEFAULT_EXIT_TARGET_CENTS:.0f}).")
    ap.add_argument("--interval", type=float, default=DEFAULT_POLL_INTERVAL_S,
                    help="Poll/fill-check cadence in seconds.")
    ap.add_argument("--buckets", default=None,
                    help="Comma-separated favoritism bands to act on (default: moderate). "
                         "Empty string = act on all buckets.")
    args = ap.parse_args()
    cfg = Config(args)

    if cfg.venue == "sim":
        print("[shock-ladder] ⚠ venue=sim: LMSR has no order book — this is a plumbing smoke "
              "test, NOT the real ladder strategy. Use --venue polymarket for live trading.")

    client = get_client()
    if cfg.once:
        run_once(client, cfg)
    else:
        run_loop(client, cfg)


if __name__ == "__main__":
    main()
