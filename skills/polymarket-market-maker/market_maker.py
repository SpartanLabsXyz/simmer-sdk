#!/usr/bin/env python3
"""
Polymarket Market Maker Skill

Posts two-sided GTC limit orders on Polymarket CLOB, managing inventory
and cancel/replace on price moves. Based on Akey et al. (2026) finding that
market-making reduces loss probability by 35.9 pp — the strongest edge on
Polymarket by a factor of 10x over any other behavioral predictor.

Quote structure (synthetic sell on the ask side):
  BID: client.trade(market_id, "yes",  amount=Q, price=mid-spread/2, order_type="GTC")
  ASK: client.trade(market_id, "no",   amount=Q, price=1-(mid+spread/2), order_type="GTC")
  → Posting the ask as a NO buy avoids taker fees and earns maker rebates
    on both sides (synthetic sell pattern from @rn1 quant bot analysis).

Usage:
    python market_maker.py                  # Dry run — show quotes, no execution
    python market_maker.py --live           # Post real GTC orders on Polymarket
    python market_maker.py --cancel-all     # Cancel all open market-maker orders
    python market_maker.py --status         # Show current quotes and inventory
    python market_maker.py --config         # Print active configuration
"""

import os
import sys
import json
import argparse
from datetime import datetime
from typing import Optional

sys.stdout.reconfigure(line_buffering=True)

# =============================================================================
# Constants
# =============================================================================

TRADE_SOURCE = "sdk:market-maker"
SKILL_SLUG = "polymarket-market-maker"
_automaton_reported = False

TICK_SIZE = 0.01          # Minimum price increment (Polymarket default)
MIN_PRICE = 0.01          # Minimum valid quote price
MAX_PRICE = 0.99          # Maximum valid quote price
MIN_SHARES = 5.0          # Polymarket minimum order size in shares

STATE_FILE_DEFAULT = os.path.join(
    os.path.expanduser("~"), ".simmer", "market_maker_state.json"
)

# =============================================================================
# Configuration
# =============================================================================

from simmer_sdk.skill import load_config

CONFIG_SCHEMA = {
    "markets":              {"env": "MM_MARKETS",              "default": "",    "type": str},
    "spread_pct":           {"env": "MM_SPREAD_PCT",           "default": 0.04,  "type": float},
    "quote_usdc":           {"env": "MM_QUOTE_USDC",           "default": 10.0,  "type": float},
    "max_skew_pct":         {"env": "MM_MAX_SKEW_PCT",         "default": 0.30,  "type": float},
    "drift_threshold":      {"env": "MM_DRIFT_THRESHOLD",      "default": 0.02,  "type": float},
    "taker_fee_rate":       {"env": "MM_TAKER_FEE_RATE",       "default": 0.02,  "type": float},
    "state_file":           {"env": "MM_STATE_FILE",           "default": STATE_FILE_DEFAULT, "type": str},
    "max_markets":          {"env": "MM_MAX_MARKETS",          "default": 5,     "type": int},
}

_config = load_config(CONFIG_SCHEMA, __file__, slug=SKILL_SLUG)

# Env overrides for automaton
_automaton_max = os.environ.get("AUTOMATON_MAX_BET")
if _automaton_max:
    _config["quote_usdc"] = min(_config["quote_usdc"], float(_automaton_max))

# =============================================================================
# Client
# =============================================================================

_client = None

def get_client():
    global _client
    if _client is None:
        try:
            from simmer_sdk import SimmerClient
        except ImportError:
            print("Error: simmer-sdk not installed. Run: pip install simmer-sdk")
            sys.exit(1)
        api_key = os.environ.get("SIMMER_API_KEY")
        if not api_key:
            print("Error: SIMMER_API_KEY not set — get from simmer.markets/dashboard")
            sys.exit(1)
        _client = SimmerClient(api_key=api_key, venue="polymarket")
    return _client

# =============================================================================
# State persistence
# =============================================================================

def load_state(state_file: str) -> dict:
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"markets": {}, "rebate_log": {}}

def save_state(state: dict, state_file: str) -> None:
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)

# =============================================================================
# Quote math
# =============================================================================

def round_to_tick(price: float, tick: float = TICK_SIZE) -> float:
    return round(round(price / tick) * tick, 6)

def clamp_price(price: float) -> float:
    return max(MIN_PRICE, min(MAX_PRICE, price))

def compute_quotes(mid: float, spread_pct: float):
    """
    Compute bid/ask prices from mid and spread.

    Returns (bid_yes, ask_yes, bid_no_synthetic)
      bid_yes         — price to post a GTC BUY on YES
      ask_yes         — conceptual YES ask (for display only)
      bid_no_synthetic — price to post a GTC BUY on NO (synthetic ask on YES)

    In a binary prediction market: YES + NO = 1.
    Posting a NO buy at price (1 - ask_yes) is economically equivalent to
    selling YES at ask_yes but earns maker rebates instead of paying taker fees.
    """
    half = spread_pct / 2
    bid_yes = clamp_price(round_to_tick(mid - half))
    ask_yes = clamp_price(round_to_tick(mid + half))

    # Guard: post-rounding collapse onto the same tick creates a crossed/locked
    # self-quote pair → wash trades + fee bleed. Widen is not safe; skip instead.
    if ask_yes < bid_yes + TICK_SIZE:
        return None, None, None  # caller: skip_reason='spread_too_narrow'

    # Synthetic ask: BUY NO at the complementary price
    bid_no_synthetic = clamp_price(round_to_tick(1.0 - ask_yes))
    return bid_yes, ask_yes, bid_no_synthetic

def fee_equivalent(notional_usdc: float, price: float, taker_fee_rate: float) -> float:
    """
    Rebate-eligible volume metric from Polymarket Maker Rebates formula.
    fee_equivalent = C × taker_fee_rate × p × (1-p)
    """
    return notional_usdc * taker_fee_rate * price * (1.0 - price)

# =============================================================================
# Inventory helpers
# =============================================================================

def get_inventory(market_id: str) -> tuple[float, float]:
    """Return (shares_yes, shares_no) from current positions for a market."""
    try:
        positions = get_client().get_positions(venue="polymarket")
        for pos in positions:
            if pos.market_id == market_id:
                return float(pos.shares_yes or 0), float(pos.shares_no or 0)
    except Exception as e:
        print(f"  [warn] Could not fetch inventory for {market_id[:8]}: {e}")
    return 0.0, 0.0

def net_skew(shares_yes: float, shares_no: float) -> float:
    """Net YES inventory (positive = long YES, negative = long NO)."""
    return shares_yes - shares_no

# =============================================================================
# Market data
# =============================================================================

def get_market_mid(market_id: str) -> Optional[float]:
    """Fetch current mid price (YES probability) for a market."""
    try:
        resp = get_client()._request("GET", f"/api/sdk/markets/{market_id}")
        market = resp.get("market", resp)
        price = market.get("external_price_yes") or market.get("current_probability")
        if price is not None:
            return float(price)
    except Exception as e:
        print(f"  [warn] Could not fetch price for {market_id[:8]}: {e}")
    return None

def get_market_info(market_id: str) -> dict:
    """Fetch full market dict for a market ID."""
    try:
        resp = get_client()._request("GET", f"/api/sdk/markets/{market_id}")
        return resp.get("market", resp)
    except Exception as e:
        print(f"  [warn] Could not fetch market info for {market_id[:8]}: {e}")
    return {}

# =============================================================================
# Open order lookup
# =============================================================================

def get_open_order_ids() -> set:
    """Return set of order IDs Simmer considers open (GTC/GTD on CLOB)."""
    try:
        resp = get_client().get_open_orders()
        return {o["order_id"] for o in resp.get("orders", []) if o.get("order_id")}
    except Exception as e:
        print(f"  [warn] Could not fetch open orders: {e}")
    return set()

# =============================================================================
# Core market-making logic for a single market
# =============================================================================

def run_market(
    market_id: str,
    market_state: dict,
    live: bool,
    spread_pct: float,
    quote_usdc: float,
    max_skew_pct: float,
    drift_threshold: float,
    taker_fee_rate: float,
    open_order_ids: set,
) -> dict:
    """
    Process one market:
      1. Fetch current mid price
      2. Check inventory skew
      3. Detect quote drift on existing orders
      4. Cancel stale orders
      5. Post fresh GTC quotes (BID on YES + synthetic ask via NO BUY)
      6. Return updated market state + stats

    Returns dict with keys: bid_placed, ask_placed, bid_cancelled, ask_cancelled,
      fee_equiv_usd, skip_reason, mid, bid_price, ask_price
    """
    stats = {
        "bid_placed": False, "ask_placed": False,
        "bid_cancelled": False, "ask_cancelled": False,
        "fee_equiv_usd": 0.0,
        "skip_reason": None,
        "mid": None, "bid_price": None, "ask_price": None,
    }

    # 1. Current price
    mid = get_market_mid(market_id)
    if mid is None:
        stats["skip_reason"] = "price_unavailable"
        return stats
    if mid <= 0.02 or mid >= 0.98:
        stats["skip_reason"] = f"near_resolution (mid={mid:.3f})"
        return stats
    stats["mid"] = mid

    # Multi-outcome (neg_risk) market guard. The synthetic-ask formula
    # `1 - ask_yes` is only valid for binary markets where YES + NO = 1.
    # For neg_risk markets the token prices are non-complementary, so the
    # ask leg would misprice and bleed via adverse selection. Skip them.
    market_info = get_market_info(market_id)
    if market_info.get("is_neg_risk") or market_info.get("neg_risk"):
        stats["skip_reason"] = "neg_risk_unsupported (multi-outcome market — synthetic-ask math invalid)"
        return stats

    # 2. Quotes
    bid_yes, ask_yes, bid_no_synth = compute_quotes(mid, spread_pct)
    if bid_yes is None:
        stats["skip_reason"] = f"spread_too_narrow (spread_pct={spread_pct:.3f} collapsed post-rounding)"
        return stats
    stats["bid_price"] = bid_yes
    stats["ask_price"] = ask_yes

    # 3. Inventory check
    shares_yes, shares_no = get_inventory(market_id)
    skew = net_skew(shares_yes, shares_no)
    max_skew_shares = (quote_usdc / mid) * max_skew_pct
    post_bid = True
    post_ask = True

    if skew > max_skew_shares:
        post_bid = False
        print(f"  → Skip BID: net_yes_skew={skew:.1f} > max={max_skew_shares:.1f}")
    if skew < -max_skew_shares:
        post_ask = False
        print(f"  → Skip ASK: net_no_skew={-skew:.1f} > max={max_skew_shares:.1f}")

    # 4. Drift check — cancel orders whose prices are stale
    existing_bid_id   = market_state.get("bid_order_id")
    existing_ask_id   = market_state.get("ask_order_id")
    existing_bid_price = market_state.get("bid_price", 0.0)
    existing_ask_price = market_state.get("ask_price", 0.0)

    def _should_cancel(existing_id, existing_price, new_price) -> bool:
        if not existing_id:
            return False
        if existing_id not in open_order_ids:
            return False  # Already gone (filled or expired)
        return abs(existing_price - new_price) > drift_threshold

    cancel_bid = _should_cancel(existing_bid_id, existing_bid_price, bid_yes)
    cancel_ask = _should_cancel(existing_ask_id, existing_ask_price, bid_no_synth)

    # 5. Cancellations — must happen before bid_needs_place/ask_needs_place is evaluated.
    # If cancel_order() raises, market_state["bid_order_id"] stays set → bid_needs_place
    # must read the post-cancel state, not a pre-computed flag, to avoid placing a second
    # GTC order while the stale one is still live on CLOB.
    if cancel_bid and existing_bid_id:
        print(f"  → Cancel stale BID {existing_bid_id[:8]}... "
              f"(was {existing_bid_price:.3f}, new {bid_yes:.3f})")
        if live:
            try:
                get_client().cancel_order(existing_bid_id)
                stats["bid_cancelled"] = True
                market_state["bid_order_id"] = None
                market_state["bid_price"] = None
            except Exception as e:
                print(f"  [warn] Cancel BID failed: {e}")

    if cancel_ask and existing_ask_id:
        print(f"  → Cancel stale ASK {existing_ask_id[:8]}... "
              f"(was {existing_ask_price:.3f} NO, new {bid_no_synth:.3f} NO)")
        if live:
            try:
                get_client().cancel_order(existing_ask_id)
                stats["ask_cancelled"] = True
                market_state["ask_order_id"] = None
                market_state["ask_price"] = None
            except Exception as e:
                print(f"  [warn] Cancel ASK failed: {e}")

    # Determine if we need to (re)place — evaluated AFTER cancel attempts so that
    # market_state["bid/ask_order_id"] reflects the cancel outcome (None = gone, set = still live).
    bid_needs_place = post_bid and (
        not market_state.get("bid_order_id")
        or market_state.get("bid_order_id") not in open_order_ids
    )
    ask_needs_place = post_ask and (
        not market_state.get("ask_order_id")
        or market_state.get("ask_order_id") not in open_order_ids
    )

    # 6. Place orders
    min_amount = round(MIN_SHARES * bid_yes, 2)
    usdc_bid = max(min_amount, round(quote_usdc, 2))
    min_amount_ask = round(MIN_SHARES * bid_no_synth, 2)
    usdc_ask = max(min_amount_ask, round(quote_usdc, 2))

    if bid_needs_place:
        print(f"  → POST BID: BUY YES @ {bid_yes:.3f} for ${usdc_bid:.2f} USDC")
        if live:
            try:
                result = get_client().trade(
                    market_id=market_id,
                    side="yes",
                    amount=usdc_bid,
                    order_type="GTC",
                    price=bid_yes,
                    source=TRADE_SOURCE,
                    skill_slug=SKILL_SLUG,
                )
                if result.success and result.trade_id:
                    market_state["bid_order_id"] = result.trade_id
                    market_state["bid_price"] = bid_yes
                    stats["bid_placed"] = True
                    fe = fee_equivalent(usdc_bid, bid_yes, taker_fee_rate)
                    stats["fee_equiv_usd"] += fe
                    print(f"     ✅ Order {result.trade_id[:8]}... (fee_equiv=${fe:.4f})")
                else:
                    print(f"     ⚠️ BID failed: {result.error}")
            except Exception as e:
                print(f"     ⚠️ BID exception: {e}")
        else:
            stats["bid_placed"] = True  # Count as "would place" in dry-run
            fe = fee_equivalent(usdc_bid, bid_yes, taker_fee_rate)
            stats["fee_equiv_usd"] += fe

    if ask_needs_place:
        print(f"  → POST ASK: BUY NO @ {bid_no_synth:.3f} for ${usdc_ask:.2f} USDC "
              f"(synthetic YES ask @ {ask_yes:.3f})")
        if live:
            try:
                result = get_client().trade(
                    market_id=market_id,
                    side="no",
                    amount=usdc_ask,
                    order_type="GTC",
                    price=bid_no_synth,
                    source=TRADE_SOURCE,
                    skill_slug=SKILL_SLUG,
                )
                if result.success and result.trade_id:
                    market_state["ask_order_id"] = result.trade_id
                    market_state["ask_price"] = bid_no_synth
                    stats["ask_placed"] = True
                    fe = fee_equivalent(usdc_ask, bid_no_synth, taker_fee_rate)
                    stats["fee_equiv_usd"] += fe
                    print(f"     ✅ Order {result.trade_id[:8]}... (fee_equiv=${fe:.4f})")
                else:
                    print(f"     ⚠️ ASK failed: {result.error}")
            except Exception as e:
                print(f"     ⚠️ ASK exception: {e}")
        else:
            stats["ask_placed"] = True
            fe = fee_equivalent(usdc_ask, bid_no_synth, taker_fee_rate)
            stats["fee_equiv_usd"] += fe

    return stats

# =============================================================================
# Commands
# =============================================================================

def cmd_status(state: dict) -> None:
    print("\n📊 Active Quotes")
    print("─" * 60)
    markets = state.get("markets", {})
    if not markets:
        print("  No active quotes.")
        return
    open_ids = get_open_order_ids()
    for market_id, ms in markets.items():
        bid_id = ms.get("bid_order_id", "—")
        ask_id = ms.get("ask_order_id", "—")
        bid_price = ms.get("bid_price")
        ask_price = ms.get("ask_price")
        bid_live = bid_id in open_ids if bid_id else False
        ask_live = ask_id in open_ids if ask_id else False
        bid_str = f"{bid_price:.3f}" if bid_price is not None else "n/a"
        ask_str = f"{ask_price:.3f}" if ask_price is not None else "n/a"  # NO-buy price
        bid_order = f"{bid_id[:8]}..." if bid_id else "—"
        ask_order = f"{ask_id[:8]}..." if ask_id else "—"
        print(f"\n  {market_id[:16]}...")
        print(f"    BID YES @ {bid_str} — order {bid_order} {'[live]' if bid_live else '[filled/expired]'}")
        print(f"    ASK (NO buy @ {ask_str}) — order {ask_order} {'[live]' if ask_live else '[filled/expired]'}")

        rebate = state.get("rebate_log", {}).get(market_id, {})
        if rebate:
            print(f"    Cumulative fee-equiv: ${rebate.get('total_fee_equiv_usd', 0):.4f}")

    # Inventory summary
    print("\n📦 Inventory")
    print("─" * 60)
    try:
        positions = get_client().get_positions(venue="polymarket", source="market-maker")
        for pos in positions:
            if pos.market_id in markets:
                skew = net_skew(pos.shares_yes or 0, pos.shares_no or 0)
                print(f"  {pos.market_id[:16]}... YES={pos.shares_yes:.2f}  NO={pos.shares_no:.2f}  "
                      f"skew={skew:+.2f}  pnl={pos.pnl:+.2f}")
    except Exception as e:
        print(f"  [warn] {e}")


def cmd_cancel_all(state: dict, live: bool) -> None:
    print("\n🚫 Cancelling all market-maker orders...")
    open_ids = get_open_order_ids()
    cancelled = 0
    for market_id, ms in state.get("markets", {}).items():
        for key in ("bid_order_id", "ask_order_id"):
            oid = ms.get(key)
            if oid and oid in open_ids:
                print(f"  Cancel {oid[:8]}...")
                if live:
                    try:
                        get_client().cancel_order(oid)
                        ms[key] = None
                        cancelled += 1
                    except Exception as e:
                        print(f"  [warn] {e}")
                else:
                    print(f"  (dry-run: would cancel {oid[:8]}...)")
                    cancelled += 1
    print(f"\n  {'Cancelled' if live else 'Would cancel'}: {cancelled} order(s)")


def cmd_run(args, live: bool) -> None:
    spread_pct      = _config["spread_pct"]
    quote_usdc      = _config["quote_usdc"]
    max_skew_pct    = _config["max_skew_pct"]
    drift_threshold = _config["drift_threshold"]
    taker_fee_rate  = _config["taker_fee_rate"]
    max_markets     = _config["max_markets"]
    state_file      = _config["state_file"]

    # Resolve market IDs from config or CLI
    market_ids_raw = (args.markets or _config.get("markets") or "").strip()
    market_ids = [m.strip() for m in market_ids_raw.split(",") if m.strip()]
    if not market_ids:
        print("Error: No markets configured. Set MM_MARKETS or pass --markets <id1,id2,...>")
        print("Markets must already be imported to Simmer. Use client.import_market(url) first.")
        sys.exit(1)

    market_ids = market_ids[:max_markets]

    print(f"\n🏦 Polymarket Market Maker")
    print(f"   Spread: {spread_pct*100:.1f}%  Quote: ${quote_usdc:.2f}  "
          f"Max skew: {max_skew_pct*100:.0f}%  Drift: {drift_threshold:.3f}")
    print(f"   Markets: {len(market_ids)}  Mode: {'LIVE' if live else 'DRY RUN'}")

    if not live:
        print("\n  [DRY RUN] No orders will be placed. Use --live to execute.\n")

    # Balance preflight (live only)
    global _automaton_reported
    if live:
        preflight = get_client().ensure_can_trade(min_usd=quote_usdc)
        if not preflight["ok"]:
            print(f"\n⏸️  Insufficient balance: ${preflight['balance']:.2f} {preflight['collateral']} "
                  f"(need ≥ ${quote_usdc:.2f})")
            if os.environ.get("AUTOMATON_MANAGED"):
                print(json.dumps({"automaton": {
                    "markets": 0, "bids_placed": 0, "asks_placed": 0,
                    "skip_reason": preflight["reason"],
                    "balance_usd": round(preflight["balance"], 2),
                }}))
                _automaton_reported = True
            return
        safe_quote = min(quote_usdc, round(preflight["max_safe_size"] / max(len(market_ids), 1), 2))
        if safe_quote < quote_usdc:
            print(f"  💰 Capping quote ${quote_usdc:.2f} → ${safe_quote:.2f} per market "
                  f"(balance ${preflight['balance']:.2f})")
            quote_usdc = safe_quote

    # Load state
    state = load_state(state_file)
    if "markets" not in state:
        state["markets"] = {}
    if "rebate_log" not in state:
        state["rebate_log"] = {}

    # Fetch open orders once (avoids per-market API call)
    open_order_ids = get_open_order_ids()

    total_bids = 0
    total_asks = 0
    total_cancels = 0
    total_fee_equiv = 0.0
    skipped = []

    for market_id in market_ids:
        print(f"\n📈 {market_id[:24]}...")
        ms = state["markets"].setdefault(market_id, {})

        stats = run_market(
            market_id=market_id,
            market_state=ms,
            live=live,
            spread_pct=spread_pct,
            quote_usdc=quote_usdc,
            max_skew_pct=max_skew_pct,
            drift_threshold=drift_threshold,
            taker_fee_rate=taker_fee_rate,
            open_order_ids=open_order_ids,
        )

        if stats["skip_reason"]:
            print(f"  ⏭️  Skipped: {stats['skip_reason']}")
            skipped.append(market_id)
            continue

        mid = stats["mid"]
        print(f"  Mid={mid:.3f}  BID={stats['bid_price']:.3f}  ASK={stats['ask_price']:.3f}  "
              f"Spread={((stats['ask_price'] or 0) - (stats['bid_price'] or 0)):.3f}")

        if stats["bid_placed"]:
            total_bids += 1
        if stats["ask_placed"]:
            total_asks += 1
        total_cancels += stats["bid_cancelled"] + stats["ask_cancelled"]
        total_fee_equiv += stats["fee_equiv_usd"]

        # Update rebate log
        rl = state["rebate_log"].setdefault(market_id, {"total_fee_equiv_usd": 0.0, "runs": 0})
        rl["total_fee_equiv_usd"] = round(rl["total_fee_equiv_usd"] + stats["fee_equiv_usd"], 6)
        rl["runs"] += 1
        rl["last_run"] = datetime.utcnow().isoformat()

    # Save state
    if live:
        save_state(state, state_file)

    # Summary
    print(f"\n{'─' * 50}")
    print(f"✅ Run complete: {total_bids} bid(s) | {total_asks} ask(s) posted | "
          f"{total_cancels} cancelled | {len(skipped)} skipped")
    print(f"   Estimated fee-equiv this run: ${total_fee_equiv:.4f}")

    cumulative_fe = sum(
        state["rebate_log"].get(m, {}).get("total_fee_equiv_usd", 0.0)
        for m in market_ids
    )
    print(f"   Cumulative fee-equiv (all runs): ${cumulative_fe:.4f}")

    if not live:
        print("\n💡 Add --live to place real orders on Polymarket CLOB")

    # Automaton report
    if os.environ.get("AUTOMATON_MANAGED"):
        report = {
            "markets": len(market_ids),
            "bids_placed": total_bids,
            "asks_placed": total_asks,
            "orders_cancelled": total_cancels,
            "fee_equiv_usd": round(total_fee_equiv, 4),
            "cumulative_fee_equiv_usd": round(cumulative_fe, 4),
        }
        if skipped:
            report["skipped_markets"] = len(skipped)
        print(json.dumps({"automaton": report}))
        _automaton_reported = True


# =============================================================================
# Entry point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Polymarket Market Maker")
    parser.add_argument("--live",       action="store_true", help="Place real GTC orders (default: dry run)")
    parser.add_argument("--cancel-all", action="store_true", help="Cancel all open market-maker orders")
    parser.add_argument("--status",     action="store_true", help="Show current quotes and inventory")
    parser.add_argument("--config",     action="store_true", help="Show active configuration")
    parser.add_argument("--markets",    type=str, default=None, help="Comma-separated Simmer market IDs to quote")
    args = parser.parse_args()

    if args.config:
        print("\n🔧 Market Maker Configuration")
        print("─" * 50)
        for k, v in _config.items():
            print(f"  {k}: {v}")
        print(f"\n  State file: {_config['state_file']}")
        return

    state_file = _config["state_file"]

    if args.status:
        state = load_state(state_file)
        cmd_status(state)
        return

    if args.cancel_all:
        state = load_state(state_file)
        cmd_cancel_all(state, live=args.live)
        if args.live:
            save_state(state, state_file)
        return

    cmd_run(args, live=args.live)


if __name__ == "__main__":
    main()

    # Fallback automaton report if we exited early (error path)
    if os.environ.get("AUTOMATON_MANAGED") and not _automaton_reported:
        print(json.dumps({"automaton": {
            "markets": 0, "bids_placed": 0, "asks_placed": 0,
            "skip_reason": "early_exit",
        }}))
