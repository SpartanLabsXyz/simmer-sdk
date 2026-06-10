#!/usr/bin/env python3
"""
World Cup Copytrader — Regular mode (sim-first).

Fetches the auto-curated WC leader set from GET /api/sdk/wc/copy-leaders
(Simmer-server daily curation: PolyNode top-traders → slippage-adjusted
copy-PnL screen → top-10 copyable WC sharps), then copies their aggregate
WC book via the Simmer SDK copytrading engine.

Unlike the base polymarket-copytrading skill the wallet list is NOT manually
configured — it is fetched automatically from the server-side curation endpoint.
Leaders are screened for copyability, not just historical P&L.

> Copyability screening reduces slippage risk; it does not remove market risk.

Usage:
    python copytrader.py              # dry run (default — shows what would trade)
    python copytrader.py --live       # execute trades on current venue
    python copytrader.py --positions  # show current positions
    python copytrader.py --config     # show configuration
    python copytrader.py --leaders    # show current leader set from server
    python copytrader.py --venue sim  # explicit sim venue ($SIM, default)
    python copytrader.py --venue polymarket --live  # real USDC on Polymarket

Spec: simmer/_dev/active/_worldcup-2026/copy-trader-skill-spec.md (Phase 1.3)
"""

import argparse
import json
import os
import sys
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

sys.stdout.reconfigure(line_buffering=True)

SKILL_SLUG = "polymarket-worldcup-copytrader"
TRADE_SOURCE = "sdk:wc-copytrader"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

from simmer_sdk.skill import load_config, get_config_path

CONFIG_SCHEMA = {
    "max_usd":         {"env": "WC_COPYTRADER_MAX_USD",         "default": 30.0,       "type": float},
    "max_trades":      {"env": "WC_COPYTRADER_MAX_TRADES",       "default": 10,         "type": int},
    "venue":           {"env": "TRADING_VENUE",                   "default": "sim",     "type": str},
    "buy_only":        {"env": "WC_COPYTRADER_BUY_ONLY",         "default": "true",     "type": str},
    "detect_exits":    {"env": "WC_COPYTRADER_DETECT_EXITS",     "default": "true",     "type": str},
    "min_leaders":     {"env": "WC_COPYTRADER_MIN_LEADERS",      "default": 5,          "type": int},
    "max_slippage":    {"env": "WC_COPYTRADER_MAX_SLIPPAGE",     "default": 0.02,       "type": float},
}

_config = load_config(CONFIG_SCHEMA, __file__, slug=SKILL_SLUG)

# Honour automaton cap
_automaton_max = os.environ.get("AUTOMATON_MAX_BET")
MAX_USD = _config["max_usd"]
if _automaton_max:
    try:
        MAX_USD = min(MAX_USD, float(_automaton_max))
    except (TypeError, ValueError):
        print(f"⚠️  Ignoring malformed AUTOMATON_MAX_BET={_automaton_max!r}; "
              f"using ${MAX_USD:.2f}")

MAX_TRADES = _config["max_trades"]

# Marketable-limit slippage tolerance for live polymarket FAK orders, as a
# FRACTION of the plan's estimated_price (matches the base
# polymarket-copytrading skill's REACTOR_PRICE_BUFFER convention: buys bid
# est*(1+tol), sells ask est*(1-tol)). Range-clamped so a fat-fingered env
# var can't silently disable the price bound (codex pass-4 P1).
_SLIPPAGE_DEFAULT = 0.02
_SLIPPAGE_FLOOR = 0.005
_SLIPPAGE_CEIL = 0.10


def _resolve_max_slippage(raw) -> float:
    """Parse + clamp WC_COPYTRADER_MAX_SLIPPAGE into [_SLIPPAGE_FLOOR, _SLIPPAGE_CEIL]."""
    try:
        val = float(raw)
    except (TypeError, ValueError):
        print(f"⚠️  Ignoring malformed WC_COPYTRADER_MAX_SLIPPAGE={raw!r}; "
              f"using {_SLIPPAGE_DEFAULT}")
        return _SLIPPAGE_DEFAULT
    if val < _SLIPPAGE_FLOOR or val > _SLIPPAGE_CEIL:
        clamped = min(max(val, _SLIPPAGE_FLOOR), _SLIPPAGE_CEIL)
        print(f"⚠️  WC_COPYTRADER_MAX_SLIPPAGE={val} outside "
              f"[{_SLIPPAGE_FLOOR}, {_SLIPPAGE_CEIL}]; clamping to {clamped}")
        return clamped
    return val


MAX_SLIPPAGE = _resolve_max_slippage(_config.get("max_slippage", _SLIPPAGE_DEFAULT))


# Polymarket tick sizes (canonical set, py-clob-client TickSize enum; all
# four occur in production — see simmer _dev/active/_polymarket-rounding-
# precision, where SDK 0.16.1 regression-tests every one of them). The SDK
# rounds the limit price to the market's NEAREST tick before signing
# (simmer_sdk/signing.py round_price_to_tick), so the cap must be quantized
# directionally to the MARKET'S OWN tick — a 0.01-floored cap of 0.16 on a
# 0.1-tick market would sign at 0.20, outside the slippage bound (codex
# pass-7 P1). Tick comes from the raw /api/sdk/markets/{id} payload (the
# server's canonical minimum_tick_size since simmer PR #400), read through
# the SDK's per-session signing cache so N trades cost at most one HTTP
# call per market — and the subsequent trade() signing reuses the entry.
_VALID_TICKS = (Decimal("0.1"), Decimal("0.01"), Decimal("0.001"), Decimal("0.0001"))


def _market_tick_size(client, market_id: str):
    """Validated Decimal tick for a market, via the SDK's market-data cache.

    Returns None on fetch failure or a tick outside the canonical set — the
    caller must SKIP that trade (error="no_tick_size") rather than guess.
    """
    try:
        cache = getattr(client, "_market_data_cache", None)
        data = cache.get(market_id) if isinstance(cache, dict) else None
        if not isinstance(data, dict):
            data = client._request("GET", f"/api/sdk/markets/{market_id}")
            if isinstance(cache, dict) and isinstance(data, dict):
                cache[market_id] = data
        raw = (data or {}).get("tick_size")
        tick = Decimal(str(raw))
    except Exception:
        return None
    return tick if tick in _VALID_TICKS else None


def _bounded_price(action: str, estimated_price, tick):
    """Marketable-limit price cap for a live polymarket FAK order.

    Without a price, a FAK fills at whatever the book offers at execution
    time — if the market moved between the copytrading plan and this loop,
    the order fills at the worse price while still spending estimated_cost.
    Bounding at the plan's own estimated_price ± MAX_SLIPPAGE turns the FAK
    into a marketable limit: fill up to the cap, kill the rest. A capped FAK
    that can't fill is recorded server-side as a failed trade ("no liquidity
    at this price") — it never rests on the book.

    The cap is rounded DIRECTIONALLY to the market's OWN tick (buys floor,
    sells ceil) so the SDK's nearest-tick rounding at signing time can never
    push the signed price outside the bound — see _VALID_TICKS comment. The
    legacy [0.001, 0.999] clamps apply after rounding, keeping the value
    inside the SDK's accepted price range.

    Returns None when estimated_price is missing/invalid, tick is not a
    validated Decimal from _VALID_TICKS, or a buy cap floors below one tick
    (no expressible tick-safe bound); the caller must SKIP that trade rather
    than send an unbounded/zero-priced live order.
    """
    if tick is None or tick not in _VALID_TICKS:
        return None
    try:
        est = float(estimated_price)
    except (TypeError, ValueError):
        return None
    if not (0.0 < est < 1.0):
        return None
    # No intermediate rounding before the directional quantize: round(x, 4)
    # can hop a tick boundary (0.129999 -> 0.1300) BEFORE the floor/ceil,
    # leaking one tick past the slippage bound.
    if action == "buy":
        cap = Decimal(str(est)) * (Decimal("1") + Decimal(str(MAX_SLIPPAGE)))
        floored = cap.quantize(tick, rounding=ROUND_FLOOR)
        if floored < tick:
            return None  # cap below one tick — caller skips (no_price_bound)
        return float(min(floored, Decimal("0.999")))
    cap = Decimal(str(est)) * (Decimal("1") - Decimal(str(MAX_SLIPPAGE)))
    ceiled = cap.quantize(tick, rounding=ROUND_CEILING)
    return float(max(ceiled, Decimal("0.001")))


_automaton_reported = False

# ---------------------------------------------------------------------------
# SDK client
# ---------------------------------------------------------------------------

_client = None


def get_client(venue: str = None):
    """Return the cached SimmerClient, pinned to the requested venue.

    The CLI --venue flag is authoritative: if the cached singleton was created
    with a different venue (e.g. fetch_leaders ran before run() resolved the
    CLI override), re-pin it so client-default venue paths can't drift.
    """
    global _client
    if _client is None:
        try:
            from simmer_sdk import SimmerClient
        except ImportError:
            print("Error: simmer-sdk not installed.  Run: pip install 'simmer-sdk>=0.17.27'")
            sys.exit(1)
        api_key = os.environ.get("SIMMER_API_KEY")
        if not api_key:
            print("Error: SIMMER_API_KEY not set.  Get yours at simmer.markets/dashboard → SDK tab.")
            sys.exit(1)
        _client = SimmerClient(api_key=api_key, venue=_resolve_venue(venue))
    elif venue and getattr(_client, "venue", None) != venue:
        _client.venue = venue
    return _client


def _resolve_venue(cli_venue: str = None) -> str:
    """Venue priority: CLI flag > config/env > 'sim' (sim-first default)."""
    return cli_venue or _config.get("venue") or "sim"


# ---------------------------------------------------------------------------
# Leader fetch
# ---------------------------------------------------------------------------

def _is_cache_not_ready_error(e: Exception) -> bool:
    """True only for the server's not-yet-computed signal.

    The endpoint raises HTTP 503 ("leader set not yet computed") when the
    daily curation cache isn't populated — it never returns cache_empty:true
    on a 200. Anything else is a real error and must propagate.
    """
    err = str(e)
    return "503" in err or "not yet computed" in err.lower()


def fetch_leaders() -> list:
    """GET /api/sdk/wc/copy-leaders → list of leader wallet strings.

    Returns an empty list if the cache hasn't been populated yet (503 from
    the server).  The daily curation job runs at 02:00 UTC; a 503 on first
    run is expected and handled gracefully.
    """
    client = get_client()
    try:
        resp = client._request("GET", "/api/sdk/wc/copy-leaders")
    except Exception as e:
        if _is_cache_not_ready_error(e):
            return []
        raise

    leaders = resp.get("leaders") or []
    return [str(entry["wallet"]) for entry in leaders if entry.get("wallet")]


def print_leaders():
    """Show the current curated leader set."""
    client = get_client()
    try:
        resp = client._request("GET", "/api/sdk/wc/copy-leaders")
    except Exception as e:
        if _is_cache_not_ready_error(e):
            print("⏳ Leader cache not yet populated (job runs at 02:00 UTC).")
        else:
            print(f"❌ Error fetching leaders: {e}")
        return

    leaders = resp.get("leaders") or []
    refreshed = resp.get("refreshed_at", "unknown")
    print(f"\n🌍 WC Copy-Leaders  (refreshed {refreshed})")
    print("=" * 55)
    for i, entry in enumerate(leaders, 1):
        wallet = entry.get("wallet", "?")
        cpnl = entry.get("backtest_copy_pnl_usdc", 0)
        slip = entry.get("slippage_cost_rate_pct", 0)
        trades = entry.get("trade_count", 0)
        print(f"  {i:2}. {wallet[:10]}…{wallet[-6:]}  "
              f"copy_pnl=${cpnl:.2f}  slippage={slip:.1f}%  trades={trades}")
    print()


# ---------------------------------------------------------------------------
# World Cup market scope
# ---------------------------------------------------------------------------

WC_MARKETS_TAG = "world-cup"


def fetch_wc_market_ids() -> set:
    """Build the WC market-id allowlist from the structured tag surface.

    WC auto-imported markets carry tags=["polymarket", "auto-import",
    "world-cup"] (simmer_v3 scheduler WC sync), so GET /api/sdk/markets
    ?tags=world-cup is the structured discovery surface — no question-regex
    fallback needed.

    Curated WC leaders can hold NON-WC positions, and the copytrading plan
    endpoint (POST /api/sdk/copytrading/execute) has no market/category scope
    param — so this allowlist is the only thing keeping a WC-only skill from
    spending USDC on unrelated markets.

    Fetches the broadest WC set (tradeable_only=false, analytics rows
    included) so a stale orderbook-liveness flag can't cause a false
    non-WC skip. Raises on fetch failure; the caller fails closed on live.
    """
    client = get_client()
    data = client._request("GET", "/api/sdk/markets", params={
        "tags": WC_MARKETS_TAG,
        "status": "active",
        "limit": 1000,
        "tradeable_only": "false",
        "include_analytics_only": "true",
    })
    return {str(m["id"]) for m in (data.get("markets") or []) if m.get("id")}


# ---------------------------------------------------------------------------
# Copytrading execution  (delegates to existing SDK engine)
# ---------------------------------------------------------------------------

def run(dry_run: bool = True, venue: str = None) -> None:
    """Fetch leaders and run the copytrading engine against them."""
    global _automaton_reported
    effective_venue = _resolve_venue(venue)
    buy_only = str(_config.get("buy_only", "true")).lower() not in ("false", "0", "no")
    detect_exits = str(_config.get("detect_exits", "true")).lower() not in ("false", "0", "no")

    # Pin the client singleton to the effective venue before any client use.
    client = get_client(effective_venue)

    print("\n🌍 World Cup Copytrader — Regular mode")
    print("=" * 50)

    # --- fetch leaders ---
    print("\n📡 Fetching auto-curated WC leaders…")
    try:
        wallets = fetch_leaders()
    except Exception as e:
        print(f"❌ Error fetching leaders: {e}")
        if os.environ.get("AUTOMATON_MANAGED"):
            print(json.dumps({"automaton": {
                "signals": 0, "trades_attempted": 0, "trades_executed": 0,
                "skip_reason": f"leaders_fetch_error: {e}",
            }}))
            _automaton_reported = True
        return

    if not wallets:
        print("⏳ Leader cache not yet populated (daily job runs 02:00 UTC). Exiting cleanly.")
        if os.environ.get("AUTOMATON_MANAGED"):
            print(json.dumps({"automaton": {
                "signals": 0, "trades_attempted": 0, "trades_executed": 0,
                "skip_reason": "leaders_cache_empty",
            }}))
            _automaton_reported = True
        return

    # Partial leader set = the curation cache looks degraded (cold-start or a
    # broken curation run). Don't copy a thin set — exit and retry next run.
    min_leaders = int(_config.get("min_leaders") or 5)
    if 0 < len(wallets) < min_leaders:
        print(f"⚠️  Only {len(wallets)} leader(s) in the curated set "
              f"(min {min_leaders}) — the curation cache looks degraded. "
              "Exiting cleanly; will retry on the next scheduled run.")
        if os.environ.get("AUTOMATON_MANAGED"):
            print(json.dumps({"automaton": {
                "signals": 0, "trades_attempted": 0, "trades_executed": 0,
                "skip_reason": "partial_leader_set",
            }}))
            _automaton_reported = True
        return

    print(f"  Leaders: {len(wallets)}")
    for w in wallets:
        print(f"    • {w[:10]}…{w[-6:]}")

    print(f"\n⚙️  Configuration:")
    print(f"  Venue:           {effective_venue}")
    print(f"  Max per trade:   ${MAX_USD:.2f}")
    print(f"  Max trades/run:  {MAX_TRADES}")
    print(f"  Buy-only:        {buy_only}")
    print(f"  Detect exits:    {detect_exits}")
    if dry_run:
        print("\n  [DRY RUN] Showing trade plan only.  Pass --live to execute.")

    # --- balance pre-flight (live polymarket only) ---
    # Low collateral blocks BUYS only: exit/rebalance plans are sell orders
    # and need no USDC — skipping the whole run would strand open positions
    # after leaders exit (codex pass-8 P2). A preflight ERROR still aborts
    # everything (fail-closed, pass-2 P1): no signal means no trading.
    buys_blocked = False
    if not dry_run and effective_venue == "polymarket":
        try:
            preflight = client.ensure_can_trade(min_usd=1.0, venue=effective_venue)
            if not preflight["ok"]:
                if buy_only:
                    print(f"\n  ⏸️  insufficient_balance: ${preflight['balance']:.2f} "
                          f"{preflight['collateral']} (need ≥ $1.00) — skip (buy-only)")
                    if os.environ.get("AUTOMATON_MANAGED"):
                        print(json.dumps({"automaton": {
                            "signals": 0, "trades_attempted": 0, "trades_executed": 0,
                            "skip_reason": preflight["reason"],
                            "balance_usd": round(preflight["balance"], 2),
                        }}))
                        _automaton_reported = True
                    return
                buys_blocked = True
                print(f"\n  ⏸️  insufficient_balance: ${preflight['balance']:.2f} "
                      f"{preflight['collateral']} (need ≥ $1.00) — buys blocked, "
                      f"continuing for sell-only exits")
            else:
                effective_max = min(MAX_USD, preflight["max_safe_size"])
                if effective_max < MAX_USD:
                    print(f"  💰 Capping max per trade ${MAX_USD:.2f} → ${effective_max:.2f}")
        except Exception as e:
            # Fail closed: with no balance/safe-size signal we must not fall
            # back to full MAX_USD on a live polymarket run.
            print(f"\n❌ Balance preflight unavailable ({e}) — aborting live run (fail-closed).")
            print("   Retry on the next scheduled run.")
            if os.environ.get("AUTOMATON_MANAGED"):
                print(json.dumps({"automaton": {
                    "signals": 0, "trades_attempted": 0, "trades_executed": 0,
                    "skip_reason": "preflight_unavailable",
                }}))
                _automaton_reported = True
            return
        if buys_blocked:
            effective_max = MAX_USD  # buys are skipped client-side below
    else:
        effective_max = MAX_USD

    # --- redeem winning positions (LIVE polymarket only) ---
    # auto_redeem() broadcasts real Polymarket redemption transactions, so it
    # must never run in dry-run mode or against the sim venue (sim has no
    # on-chain redemption — winnings settle automatically).
    if effective_venue == "polymarket":
        if dry_run:
            print("\n  💤 DRY-RUN: would auto-redeem resolved positions")
        else:
            try:
                redeemed = client.auto_redeem()
                for r in redeemed:
                    if r.get("success"):
                        print(f"  💰 Redeemed {r['market_id'][:8]}… ({r.get('side', '?')})")
            except Exception:
                pass

    # --- build trade plan (server-side copytrading engine) ---
    print("\n📡 Requesting trade plan…")
    payload = {
        "wallets": wallets,
        "max_usd_per_position": effective_max,
        "dry_run": True,
        "buy_only": buy_only,
        "detect_whale_exits": detect_exits,
        "max_trades": MAX_TRADES,
        "venue": effective_venue,
    }
    try:
        plan = client._request("POST", "/api/sdk/copytrading/execute", json=payload, timeout=60)
    except Exception as e:
        print(f"\n❌ Error building trade plan: {e}")
        if os.environ.get("AUTOMATON_MANAGED"):
            print(json.dumps({"automaton": {
                "signals": 0, "trades_attempted": 0, "trades_executed": 0,
                "skip_reason": f"plan_error: {e}",
            }}))
            _automaton_reported = True
        return

    trades = plan.get("trades") or []
    print(f"\n📊 Analysis:")
    print(f"  Leaders analyzed:  {plan.get('wallets_analyzed', 0)}")
    print(f"  Positions found:   {plan.get('positions_found', 0)}")
    print(f"  Conflicts skipped: {plan.get('conflicts_skipped', 0)}")

    if not trades:
        print("\n✅ No trades needed — positions already aligned with leaders.")
        _emit_automaton(plan, 0)
        return

    print(f"\n📈 Trade plan ({len(trades)} trade(s)):")
    for t in trades:
        action = t.get("action", "?").upper()
        side = t.get("side", "?").upper()
        shares = t.get("shares", 0)
        price = t.get("estimated_price", 0)
        cost = t.get("estimated_cost", 0)
        title = t.get("market_title", "Unknown")[:45]
        print(f"  ⏸️  {action} {shares:.1f} {side} @ ${price:.3f} (${cost:.2f})  {title}…")

    if dry_run:
        print(f"\n💡 Dry-run complete — {len(trades)} trades planned. Pass --live to execute.")
        _emit_automaton(plan, 0)
        return

    # --- WC scope allowlist (live only) ---
    # Leaders are WC-curated but their books are not: a leader can hold
    # non-WC positions and the plan endpoint has no scope param, so a
    # WC-only skill must filter client-side before spending anything.
    try:
        wc_market_ids = fetch_wc_market_ids()
    except Exception as e:
        print(f"\n❌ WC market allowlist unavailable ({e}) — aborting live run (fail-closed).")
        print("   A WC-only skill must not execute unscoped trades. Retry next run.")
        if os.environ.get("AUTOMATON_MANAGED"):
            print(json.dumps({"automaton": {
                "signals": plan.get("positions_found", 0),
                "trades_attempted": 0, "trades_executed": 0,
                "skip_reason": "wc_scope_unavailable",
            }}))
            _automaton_reported = True
        return

    # --- execute trades client-side ---
    # Belt-and-braces: server-side params already request these limits, but
    # enforce them client-side too so a server bug can't oversize the run.
    if len(trades) > MAX_TRADES:
        print(f"\n⚠️  Plan has {len(trades)} trades > max_trades={MAX_TRADES} — truncating.")
        trades = trades[:MAX_TRADES]
        plan["trades"] = trades  # keep automaton trades_attempted accurate
    print(f"\n⚡ Executing {len(trades)} trade(s)…")
    executed = 0
    for t in trades:
        market_id = t.get("market_id")
        action = t.get("action", "buy")
        side = t.get("side", "yes")
        shares = t.get("shares", 0)
        cost = t.get("estimated_cost", 0)
        title = t.get("market_title", market_id[:20] if market_id else "?")
        if str(market_id) not in wc_market_ids:
            print(f"  ⚠️  Skipping {title[:40]} — non-WC market "
                  f"({market_id}); this skill only trades World Cup markets")
            t["success"] = False
            t["error"] = "non_wc_market_skipped"
            continue
        if action == "buy" and buys_blocked:
            print(f"  ⚠️  Skipping {title[:40]} — buys blocked "
                  f"(insufficient collateral); sell-only run")
            t["success"] = False
            t["error"] = "insufficient_balance_buy_skipped"
            continue
        if action == "buy" and cost > effective_max:
            print(f"  ⚠️  Skipping {title[:40]} — cost ${cost:.2f} exceeds "
                  f"per-position cap ${effective_max:.2f}")
            t["success"] = False
            t["error"] = "exceeds_per_position_cap"
            continue
        # Price bound: live polymarket FAK orders must be marketable limits,
        # never unbounded market orders (codex pass-4 P1). The SDK's price
        # param is polymarket-only (sim is LMSR — no book to cap against).
        trade_price = None
        if effective_venue == "polymarket":
            tick = _market_tick_size(client, market_id)
            if tick is None:
                print(f"  ⚠️  Skipping {title[:40]} — market tick_size "
                      f"unavailable/invalid; refusing to guess a price bound")
                t["success"] = False
                t["error"] = "no_tick_size"
                continue
            trade_price = _bounded_price(action, t.get("estimated_price"), tick)
            if trade_price is None:
                print(f"  ⚠️  Skipping {title[:40]} — no usable price bound "
                      f"(missing estimated_price or cap below one tick); "
                      f"refusing unbounded live order")
                t["success"] = False
                t["error"] = "no_price_bound"
                continue
        source_wallet = t.get("whale_wallet") or t.get("source_wallet") or ""
        signal_data = {
            "signal_source": "wc_copytrader",
            "edge": round(t.get("edge", 0.05), 4),
            "confidence": round(t.get("confidence", 0.6), 2),
            "leader_wallet": source_wallet[:10] if source_wallet else "",
            "leader_count": len(wallets),
        }
        try:
            result = client.trade(
                market_id=market_id,
                side=side,
                action=action,
                amount=cost if action == "buy" else 0,
                shares=shares if action == "sell" else 0,
                venue=effective_venue,
                # FAK (fill-and-kill): fill what's available now, cancel the
                # rest. A once-daily fire-and-forget run must never leave
                # resting orders — the next day's plan recomputes from
                # POSITIONS (not open orders), so a stale GTC could
                # double-fill later and bypass MAX_USD / MAX_TRADES.
                order_type="FAK",
                # Marketable limit: fill up to the plan's estimated_price ±
                # MAX_SLIPPAGE, kill the rest. None on sim (SDK rejects price
                # for non-polymarket venues).
                price=trade_price,
                reasoning=(
                    f"WC Copytrader: {action} {shares:.1f} {side} to mirror WC leaders"
                    f" on {title}"
                ),
                source=TRADE_SOURCE,
                skill_slug=SKILL_SLUG,
                signal_data=signal_data,
            )
            t["success"] = result.success
            t["error"] = result.error if not result.success else None
            t["trade_id"] = result.trade_id
            if result.success:
                executed += 1
                print(f"  ✅ {action.upper()} {shares:.1f} {side.upper()} @ ${t.get('estimated_price', 0):.3f}  {title[:40]}…")
            else:
                print(f"  ❌ {action.upper()} failed: {result.error}")
        except Exception as e:
            t["success"] = False
            t["error"] = str(e)
            print(f"  ❌ {action.upper()} error: {e}")

    plan["trades_executed"] = executed
    _emit_automaton(plan, executed)
    print(f"\n{'─' * 50}")
    print(f"✅ WC Copytrader run complete — {executed}/{len(trades)} trades executed.")


def _emit_automaton(plan: dict, trades_executed: int) -> None:
    """Emit the structured automaton JSON block when managed by OpenClaw."""
    global _automaton_reported
    if not os.environ.get("AUTOMATON_MANAGED"):
        return
    trades = plan.get("trades") or []
    positions_found = plan.get("positions_found", 0)
    trades_needed = len(trades)
    total_cost = sum(t.get("estimated_cost", 0) for t in trades if t.get("success"))
    report = {
        "signals": positions_found,
        "trades_attempted": trades_needed,
        "trades_executed": trades_executed,
        "amount_usd": round(total_cost, 2),
    }
    non_wc_skipped = sum(1 for t in trades if t.get("error") == "non_wc_market_skipped")
    if non_wc_skipped:
        report["non_wc_skipped"] = non_wc_skipped
    if positions_found > 0 and trades_executed == 0:
        skip_reasons = []
        conflicts = plan.get("conflicts_skipped", 0)
        if conflicts:
            skip_reasons.append(f"{conflicts} conflicts_skipped")
        for err in (plan.get("errors") or []):
            skip_reasons.append(str(err)[:80])
        if not plan.get("success"):
            skip_reasons.append("copytrading_failed")
        failed_trades = [t for t in trades if not t.get("success") and t.get("error")]
        for t in failed_trades:
            skip_reasons.append(str(t["error"])[:80])
        if skip_reasons:
            report["skip_reason"] = ", ".join(dict.fromkeys(skip_reasons))
    print(json.dumps({"automaton": report}))
    _automaton_reported = True


def show_positions(venue: str = None) -> None:
    """Show current positions."""
    client = get_client()
    effective_venue = _resolve_venue(venue)
    print(f"\n📊 WC Copytrader — Current Positions ({effective_venue})")
    print("=" * 50)
    try:
        data = client._request("GET", f"/api/sdk/positions?venue={effective_venue}")
        positions = data.get("positions") or []
        if not positions:
            print(f"No {effective_venue} positions found.")
            return
        total_value = 0.0
        total_pnl = 0.0
        for pos in positions:
            question = (pos.get("question") or "Unknown")[:50]
            shares_yes = pos.get("shares_yes", 0)
            shares_no = pos.get("shares_no", 0)
            value = pos.get("current_value", 0)
            pnl = pos.get("pnl", 0)
            pnl_pct = (pnl / pos["cost_basis"] * 100) if pos.get("cost_basis") else 0
            total_value += value
            total_pnl += pnl
            side = f"{shares_yes:.1f} YES" if shares_yes >= shares_no else f"{shares_no:.1f} NO"
            sign = "+" if pnl >= 0 else ""
            print(f"\n  {question}…")
            print(f"  Position: {side}  Value: ${value:.2f}  P&L: {sign}${pnl:.2f} ({sign}{pnl_pct:.1f}%)")
        print(f"\n{'─' * 50}")
        sign = "+" if total_pnl >= 0 else ""
        print(f"Total value: ${total_value:.2f}  P&L: {sign}${total_pnl:.2f}  ({len(positions)} positions)")
    except Exception as e:
        print(f"❌ Error: {e}")


def print_config() -> None:
    """Show current configuration."""
    config_path = get_config_path(__file__)
    print("\n🌍 WC Copytrader Configuration")
    print("=" * 40)
    print(f"API key:         {'✅ Set' if os.environ.get('SIMMER_API_KEY') else '❌ Not set'}")
    print(f"Venue:           {_resolve_venue()} (default: sim)")
    print(f"Max per trade:   ${MAX_USD:.2f}")
    print(f"Max trades/run:  {MAX_TRADES}")
    print(f"Config file:     {config_path} ({'exists' if config_path.exists() else 'not yet created'})")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="World Cup Copytrader — copy auto-curated WC leaders via Simmer"
    )
    parser.add_argument("--live",       action="store_true", help="Execute trades (default: dry-run)")
    parser.add_argument("--dry-run",    action="store_true", help="Show trade plan without executing")
    parser.add_argument("--positions",  action="store_true", help="Show current positions")
    parser.add_argument("--leaders",    action="store_true", help="Show current curated leader set")
    parser.add_argument("--config",     action="store_true", help="Show configuration")
    parser.add_argument("--venue",      type=str, choices=["sim", "polymarket"],
                        help="Trading venue: sim (default) for $SIM, polymarket for real USDC")
    parser.add_argument("--rebalance",  action="store_true",
                        help="Full rebalance (buy + sell); default is buy-only")
    parser.add_argument("--no-exits",   action="store_true",
                        help="Disable leader-exit detection (default: sell when leaders exit)")
    args = parser.parse_args()

    # Apply CLI overrides to config
    if args.rebalance:
        _config["buy_only"] = "false"
    if args.no_exits:
        _config["detect_exits"] = "false"

    if args.config:
        print_config()
        return
    if args.positions:
        show_positions(venue=args.venue)
        return
    if args.leaders:
        print_leaders()
        return

    # --dry-run is authoritative: `--live --dry-run` runs DRY.
    dry_run = args.dry_run or not args.live
    run(dry_run=dry_run, venue=args.venue)

    if os.environ.get("AUTOMATON_MANAGED") and not _automaton_reported:
        print(json.dumps({"automaton": {
            "signals": 0, "trades_attempted": 0, "trades_executed": 0,
            "skip_reason": "no_signal",
        }}))


if __name__ == "__main__":
    main()
