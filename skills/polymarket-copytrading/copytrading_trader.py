#!/usr/bin/env python3
"""
Simmer Copytrading Skill

Mirrors positions from target Polymarket wallets via Simmer SDK.
Uses the existing copytrading_strategy.py logic server-side.

By default, runs in "buy only" mode - only buys to match whale positions,
never sells existing positions. This prevents conflicts with other strategies
(weather, etc.) that may have opened positions.

Exit handling:
- Whale exit detection is ON by default (sell when whales exit)
- --no-whale-exits: Disable whale exit detection (buy-only, never sell)
- SDK Risk Management: Stop-loss/take-profit (server-side, auto-set on every buy)

Usage:
    python copytrading_trader.py              # Dry run (show what would trade)
    python copytrading_trader.py --live       # Execute real trades
    python copytrading_trader.py --positions  # Show current positions
    python copytrading_trader.py --config     # Show configuration
    python copytrading_trader.py --wallets 0x... # Override wallets for this run
    python copytrading_trader.py --no-whale-exits # Disable whale exit detection
    python copytrading_trader.py --rebalance  # Full rebalance mode (buy + sell)
"""

import os
import sys
import json
import argparse
from typing import Optional
from datetime import datetime

# Force line-buffered stdout so output is visible in non-TTY environments (cron, Docker, OpenClaw)
sys.stdout.reconfigure(line_buffering=True)

# Optional: Trade Journal integration for tracking
try:
    from tradejournal import log_trade
    JOURNAL_AVAILABLE = True
except ImportError:
    try:
        # Try relative import within skills package
        from skills.tradejournal import log_trade
        JOURNAL_AVAILABLE = True
    except ImportError:
        JOURNAL_AVAILABLE = False
        def log_trade(*args, **kwargs):
            pass  # No-op if tradejournal not installed

# Source tag for tracking
TRADE_SOURCE = "sdk:copytrading"
SKILL_SLUG = "polymarket-copytrading"
_automaton_reported = False


# =============================================================================
# Configuration (config.json > env vars > defaults)
# =============================================================================

from simmer_sdk.skill import load_config, update_config, get_config_path

# Configuration schema
CONFIG_SCHEMA = {
    "wallets": {"env": "SIMMER_COPYTRADING_WALLETS", "default": "", "type": str},
    "top_n": {"env": "SIMMER_COPYTRADING_TOP_N", "default": "", "type": str},  # Empty = auto
    "max_usd": {"env": "SIMMER_COPYTRADING_MAX_USD", "default": 50.0, "type": float},
    "max_trades_per_run": {"env": "SIMMER_COPYTRADING_MAX_TRADES", "default": 10, "type": int},
    "venue": {"env": "TRADING_VENUE", "default": "", "type": str},  # sim or polymarket
}

# Load configuration
_config = load_config(CONFIG_SCHEMA, __file__, slug="polymarket-copytrading")

# SimmerClient singleton
_client = None

def get_client():
    """Lazy-init SimmerClient singleton."""
    global _client
    if _client is None:
        try:
            from simmer_sdk import SimmerClient
        except ImportError:
            print("Error: simmer-sdk not installed. Run: pip install simmer-sdk")
            sys.exit(1)
        api_key = os.environ.get("SIMMER_API_KEY")
        if not api_key:
            print("Error: SIMMER_API_KEY environment variable not set")
            print("Get your API key from: simmer.markets/dashboard -> SDK tab")
            sys.exit(1)
        venue = _config.get("venue") or os.environ.get("TRADING_VENUE") or "polymarket"
        _client = SimmerClient(api_key=api_key, venue=venue)
    return _client

# Polymarket constraints
MIN_SHARES_PER_ORDER = 5.0  # Polymarket requires minimum 5 shares
MIN_TICK_SIZE = 0.01        # Minimum price increment

# Copytrading settings - from config
COPYTRADING_WALLETS = _config["wallets"]
COPYTRADING_TOP_N = _config["top_n"]
COPYTRADING_MAX_USD = _config["max_usd"]
_automaton_max = os.environ.get("AUTOMATON_MAX_BET")
if _automaton_max:
    COPYTRADING_MAX_USD = min(COPYTRADING_MAX_USD, float(_automaton_max))
MAX_TRADES_PER_RUN = _config["max_trades_per_run"]


def get_config() -> dict:
    """Get current configuration."""
    wallets = [w.strip() for w in COPYTRADING_WALLETS.split(",") if w.strip()]
    top_n = int(COPYTRADING_TOP_N) if COPYTRADING_TOP_N else None

    return {
        "api_key_set": bool(os.environ.get("SIMMER_API_KEY")),
        "wallets": wallets,
        "top_n": top_n,
        "top_n_mode": "auto" if top_n is None else "manual",
        "max_position_usd": COPYTRADING_MAX_USD,
    }


def print_config():
    """Print current configuration."""
    config = get_config()
    config_path = get_config_path(__file__)

    print("\n🐋 Simmer Copytrading Configuration")
    print("=" * 40)
    print(f"API Key: {'✅ Set' if config['api_key_set'] else '❌ Not set'}")
    print(f"\nTarget Wallets ({len(config['wallets'])}):")
    for i, wallet in enumerate(config['wallets'], 1):
        print(f"  {i}. {wallet[:10]}...{wallet[-6:]}")
    if not config['wallets']:
        print("  (none configured)")

    print(f"\nSettings:")
    print(f"  Top N: {config['top_n'] if config['top_n'] else 'auto (based on balance)'}")
    print(f"  Max per position: ${config['max_position_usd']:.2f}")
    print(f"\nConfig file: {config_path}")
    print(f"Config exists: {'Yes' if config_path.exists() else 'No'}")
    print("\nTo change settings:")
    print("  --set wallets=0x123...,0x456...")
    print("  --set max_usd=100")
    print("  --set top_n=10")
    print()


# =============================================================================
# API Helpers
# =============================================================================

def get_positions() -> dict:
    """Get current SDK positions as raw dict (preserves original format for show_positions)."""
    return get_client()._request("GET", "/api/sdk/positions")


def set_risk_monitor(market_id: str, side: str,
                     stop_loss_pct: float = 0.20, take_profit_pct: float = 0.50) -> dict:
    """Set stop-loss and take-profit for a position."""
    try:
        return get_client().set_monitor(market_id, side,
                                        stop_loss_pct=stop_loss_pct,
                                        take_profit_pct=take_profit_pct)
    except Exception as e:
        return {"error": str(e)}


def get_risk_monitors() -> dict:
    """List all active risk monitors."""
    try:
        return get_client().list_monitors()
    except Exception as e:
        return {"error": str(e)}


def remove_risk_monitor(market_id: str, side: str) -> dict:
    """Remove risk monitor for a position."""
    try:
        return get_client().delete_monitor(market_id, side)
    except Exception as e:
        return {"error": str(e)}


def get_markets() -> list:
    """Get available markets."""
    result = get_client()._request("GET", "/api/sdk/markets")
    return result.get("markets", [])


def get_context(market_id: str) -> dict:
    """Get market context (position, trades, slippage)."""
    return get_client().get_market_context(market_id)


def execute_trade(market_id: str, side: str, action: str, amount_usd: float = None, shares: float = None) -> dict:
    """Execute a trade via SDK."""
    try:
        result = get_client().trade(
            market_id=market_id, side=side, action=action,
            amount=amount_usd or 0, shares=shares or 0,
            source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
        )
        return {
            "success": result.success, "trade_id": result.trade_id,
            "shares_bought": result.shares_bought, "error": result.error,
        }
    except Exception as e:
        raise ValueError(str(e))


# =============================================================================
# Copytrading Logic
# =============================================================================



def execute_copytrading(wallets: list, top_n: int = None, max_usd: float = 50.0, dry_run: bool = True, buy_only: bool = True, detect_whale_exits: bool = True, max_trades: int = None, venue: str = None) -> dict:
    """
    Execute copytrading via Simmer SDK.

    Uses dry_run=True to get the trade plan from the server, then executes
    each trade client-side via client.trade(). This ensures signing works
    for both managed (server-side) and external (client-side) wallets.

    The server handles: fetching whale positions, calculating allocations,
    conflict detection, Top N filtering, auto-import, rebalance calculation.
    The client handles: trade execution with proper wallet signing.

    Venue:
    - 'sim': Execute on Simmer LMSR with $SIM (paper trading)
    - 'polymarket': Execute on Polymarket with real USDC
    - None: Fall back to TRADING_VENUE env var, then server auto-detect
    """
    # Default to TRADING_VENUE env var so automaton/cron venue choice propagates
    if venue is None:
        venue = os.environ.get("TRADING_VENUE")

    data = {
        "wallets": wallets,
        "max_usd_per_position": max_usd,
        "dry_run": True,  # Always get trade plan from server
        "buy_only": buy_only,
        "detect_whale_exits": detect_whale_exits,
    }

    if top_n is not None:
        data["top_n"] = top_n

    if max_trades is not None:
        data["max_trades"] = max_trades

    if venue is not None:
        data["venue"] = venue

    result = get_client()._request("POST", "/api/sdk/copytrading/execute", json=data)

    # If caller wants dry_run, return the plan as-is
    if dry_run:
        return result

    # Execute each trade client-side via client.trade() (handles signing for all wallet types)
    trades = result.get("trades", [])
    executed = 0
    for t in trades:
        market_id = t.get("market_id")
        action = t.get("action", "buy")
        side = t.get("side", "yes")
        shares = t.get("shares", 0)
        estimated_cost = t.get("estimated_cost", 0)

        try:
            market_title = t.get("market_title", market_id[:20])
            _whale_wallet = t.get("whale_wallet", t.get("source_wallet", ""))
            _signal_data = {
                "edge": round(t.get("edge", 0.05), 4),
                "confidence": round(t.get("confidence", 0.6), 2),
                "signal_source": "whale_copytrading",
                "whale_wallet": _whale_wallet[:10] if _whale_wallet else "",
                "whale_size": round(t.get("whale_position_usd", t.get("estimated_cost", 0)), 2),
            }
            trade_result = get_client().trade(
                market_id=market_id,
                side=side,
                action=action,
                amount=estimated_cost if action == "buy" else 0,
                shares=shares if action == "sell" else 0,
                reasoning=f"Copytrading: {action} {shares:.1f} {side} to mirror whale positions on {market_title}",
                source=TRADE_SOURCE,
                skill_slug=SKILL_SLUG,
                signal_data=_signal_data,
            )
            t["success"] = trade_result.success
            t["error"] = trade_result.error if not trade_result.success else None
            t["trade_id"] = trade_result.trade_id
            if trade_result.success:
                executed += 1
        except Exception as e:
            t["success"] = False
            t["error"] = str(e)

    result["trades_executed"] = executed
    result["dry_run"] = False
    return result


def run_copytrading(wallets: list, top_n: int = None, max_usd: float = 50.0, dry_run: bool = True, buy_only: bool = True, detect_whale_exits: bool = True, venue: str = None):
    """
    Run copytrading scan and execute trades.

    Calls the Simmer SDK copytrading endpoint which handles:
    - Fetching positions from target wallets via Dome API
    - Size-weighted aggregation (larger wallets = more influence)
    - Conflict detection (skips markets where wallets disagree)
    - Top N concentration (focus on highest-conviction positions)
    - Auto-import of missing markets
    - Rebalance trade calculation and execution
    - Whale exit detection (sells positions whales no longer hold)

    By default, only BUY trades are executed (buy_only=True). This prevents
    copytrading from selling positions opened by other strategies (weather, etc.)

    Venue: 'sim' for $SIM paper trading, 'polymarket' for real USDC, None for auto-detect.
    """
    print("\n🐋 Starting Copytrading Scan...")
    print("=" * 50)

    if not wallets:
        print("❌ No wallets specified.")
        print("   Use --wallets 0x123...,0x456... to specify wallets")
        print("   Or set SIMMER_COPYTRADING_WALLETS env var for recurring scans")
        return

    # Show configuration
    print("\n⚙️ Configuration:")
    print(f"  Wallets: {len(wallets)}")
    for w in wallets:
        print(f"    • {w[:10]}...{w[-6:]}")
    print(f"  Top N: {top_n if top_n else 'auto (based on balance)'}")
    print(f"  Max per position: ${max_usd:.2f}")
    print(f"  Max trades/run:  {MAX_TRADES_PER_RUN}")
    venue_label = venue or "auto-detect"
    print(f"  Venue: {venue_label}")
    print(f"  Mode: {'Buy only (accumulate)' if buy_only else 'Full rebalance (buy + sell)'}")
    print(f"  Whale exits: {'Enabled (sell when whale exits)' if detect_whale_exits else 'Disabled'}")

    if dry_run:
        print("\n  [DRY RUN] Trades will be simulated server-side. Use --live for real trades.")

    # Execute copytrading via SDK
    print("\n📡 Calling Simmer API...")
    try:
        result = execute_copytrading(wallets, top_n, max_usd, dry_run, buy_only, detect_whale_exits, MAX_TRADES_PER_RUN, venue=venue)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return

    # Display results
    print(f"\n📊 Analysis Results:")
    print(f"  Wallets analyzed: {result.get('wallets_analyzed', 0)}")
    print(f"  Positions found: {result.get('positions_found', 0)}")
    print(f"  Conflicts skipped: {result.get('conflicts_skipped', 0)}")
    print(f"  Top N used: {result.get('top_n_used', 0)}")
    whale_exits = result.get('whale_exits_detected', 0)
    if whale_exits > 0:
        print(f"  Whale exits detected: {whale_exits}")

    trades = result.get('trades', [])
    trades_needed = result.get('trades_needed', 0)
    trades_executed = result.get('trades_executed', 0)

    if trades:
        print(f"\n📈 Trades ({trades_executed}/{trades_needed} executed):")
        for t in trades:
            action = t.get('action', '?').upper()
            side = t.get('side', '?').upper()
            shares = t.get('shares', 0)
            price = t.get('estimated_price', 0)
            cost = t.get('estimated_cost', 0)
            title = t.get('market_title', 'Unknown')[:40]
            success = t.get('success', False)
            error = t.get('error')

            status = "✅" if success else "⏸️"
            if error and "dry_run" in error:
                status = "🔒"

            print(f"  {status} {action} {shares:.1f} {side} @ ${price:.3f} (${cost:.2f})")
            print(f"     {title}...")
            if error and "dry_run" not in error:
                print(f"     ⚠️ {error}")

    # Show errors
    errors = result.get('errors', [])
    if errors:
        print(f"\n⚠️ Warnings:")
        for err in errors:
            print(f"  • {err}")

    # Summary
    summary = result.get('summary', 'Complete')
    print(f"\n{'─' * 50}")
    print(f"📋 {summary}")

    if not result.get('success'):
        print("\n❌ Copytrading failed. Check errors above.")
    elif dry_run:
        print("\n💡 Remove --dry-run to execute trades")
    elif trades_executed > 0:
        print(f"\n✅ Successfully mirrored positions!")

        # Log successful trades to journal
        # Risk monitors are now auto-set via SDK settings (dashboard)
        for t in trades:
            if t.get('success'):
                trade_id = t.get('trade_id')
                action = t.get('action', 'buy')
                side = t.get('side', 'yes')
                shares = t.get('shares', 0)
                price = t.get('estimated_price', 0)

                # Log trade context for journal
                if trade_id and JOURNAL_AVAILABLE:
                    log_trade(
                        trade_id=trade_id,
                        source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
                        thesis=f"Copytrading: {action.upper()} {shares:.1f} {side.upper()} "
                               f"@ ${price:.3f} to mirror whale positions",
                        action=action,
                        wallets_count=len(wallets),
                    )
    else:
        print("\n✅ Scan complete")

    # Structured report for automaton
    if os.environ.get("AUTOMATON_MANAGED"):
        global _automaton_reported
        positions_found = result.get('positions_found', 0) if result else 0
        _trades_needed = result.get('trades_needed', 0) if result else 0
        _trades_exec = result.get('trades_executed', 0) if result else 0
        _total_cost = sum(t.get('estimated_cost', 0) for t in (result.get('trades', []) if result else []) if t.get('success'))
        report = {"signals": positions_found, "trades_attempted": _trades_needed, "trades_executed": _trades_exec, "amount_usd": round(_total_cost, 2)}
        if positions_found > 0 and _trades_exec == 0:
            # Derive skip reasons from server response
            skip_reasons = []
            conflicts = result.get('conflicts_skipped', 0) if result else 0
            if conflicts > 0:
                skip_reasons.append(f"{conflicts} conflicts skipped")
            errors = result.get('errors', []) if result else []
            for err in errors:
                skip_reasons.append(str(err)[:80])
            if not result.get('success'):
                skip_reasons.append("copytrading failed")
            if skip_reasons:
                report["skip_reason"] = ", ".join(dict.fromkeys(skip_reasons))
        # Collect execution errors from failed trades
        execution_errors = []
        for t in (result.get('trades', []) if result else []):
            if not t.get('success') and t.get('error') and 'dry_run' not in str(t.get('error', '')):
                execution_errors.append(str(t['error'])[:120])
        if execution_errors:
            report["execution_errors"] = execution_errors
        print(json.dumps({"automaton": report}))
        _automaton_reported = True


def run_reactive(max_usd: float = 25.0, dry_run: bool = True, venue: str = None):
    """
    Reactive mode: act on a single reactor event instead of mirroring a full portfolio.

    Reads REACTOR_EVENT_* env vars set by the reactor plugin:
    - REACTOR_EVENT_WALLET: whale wallet address
    - REACTOR_EVENT_MARKET_SLUG: Polymarket market slug
    - REACTOR_EVENT_CONDITION_ID: Polymarket condition ID (fallback if slug missing)
    - REACTOR_EVENT_OUTCOME: outcome label (Yes/No) from PolyNode tokens map
    - REACTOR_EVENT_SIDE: BUY or SELL
    - REACTOR_EVENT_SIZE: trade size in shares
    - REACTOR_EVENT_PRICE: execution price
    - REACTOR_MAX_USD: max trade size from reactor config
    """
    wallet = os.environ.get("REACTOR_EVENT_WALLET", "")
    market_slug = os.environ.get("REACTOR_EVENT_MARKET_SLUG", "")
    condition_id = os.environ.get("REACTOR_EVENT_CONDITION_ID", "")
    outcome = os.environ.get("REACTOR_EVENT_OUTCOME", "").lower().strip()
    side_raw = os.environ.get("REACTOR_EVENT_SIDE", "").upper()
    size = float(os.environ.get("REACTOR_EVENT_SIZE", "0"))
    price = float(os.environ.get("REACTOR_EVENT_PRICE", "0"))
    reactor_max = os.environ.get("REACTOR_MAX_USD")

    if reactor_max:
        max_usd = min(max_usd, float(reactor_max))

    if not wallet or (not market_slug and not condition_id):
        print("❌ Reactive mode requires REACTOR_EVENT_WALLET and either REACTOR_EVENT_MARKET_SLUG or REACTOR_EVENT_CONDITION_ID")
        return

    # Map BUY/SELL to side for our trade
    # If whale buys, we buy the same side (yes/no is determined by which token they bought)
    # For now, default to "yes" for BUY signals — the market slug tells us which outcome
    if side_raw == "SELL":
        print(f"⏭️ Whale is selling — skipping (buy-only mode)")
        return

    # PolyNode sends outcome label via tokens map + taker_token (e.g., "Yes", "No").
    # Use it if available, default to "yes" if not (buying = bullish on the question).
    side = outcome if outcome in ("yes", "no") else "yes"

    print(f"\n⚡ Reactor: Reactive Copytrading")
    print("=" * 50)
    print(f"  Whale: {wallet[:10]}...{wallet[-6:]}")
    print(f"  Market: {market_slug or condition_id}")
    print(f"  Whale action: {side_raw} ${size:.0f} @ {price:.3f}")
    print(f"  Outcome: {outcome.upper() if outcome else 'unknown (defaulting to YES)'}")
    print(f"  Our action: BUY {side.upper()} up to ${max_usd:.2f}")
    print(f"  Venue: {venue or 'auto-detect'}")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")

    if dry_run:
        print(f"\n🔒 DRY RUN — would import {market_slug or condition_id} and buy {side.upper()} for ${max_usd:.2f}")
        return

    client = get_client()

    # Step 1: Resolve to a Simmer market_id
    market_id = None

    # 1a: Try condition_id resolve (fast DB lookup, no import)
    if condition_id:
        print(f"\n🔍 Resolving condition_id {condition_id[:16]}...")
        try:
            resolve_result = client._request("POST", "/api/sdk/markets/resolve", json={
                "condition_ids": [condition_id],
            })
            results = resolve_result.get("results", [])
            if results and results[0].get("found"):
                market_id = results[0].get("market_id")
                print(f"  ✅ Found in catalog: {market_id}")
        except Exception as e:
            print(f"  ⚠️ Resolve failed: {e}")

    # 1b: If not found and we have a slug, import from Polymarket
    if not market_id and market_slug:
        print(f"\n📡 Importing {market_slug} from Polymarket...")
        try:
            import_result = client.import_market(f"https://polymarket.com/event/{market_slug}")
            market_id = import_result.get("market_id") or import_result.get("id")
            if market_id:
                print(f"  ✅ Imported: {market_id}")
            else:
                print(f"  ⚠️ Import returned no market_id: {import_result}")
        except Exception as e:
            # "already exists" means it's in the catalog — resolve should have found it,
            # but race conditions happen. Try resolve again.
            if "already" in str(e).lower() or "exists" in str(e).lower():
                print(f"  ℹ️ Already exists, looking up...")
                if condition_id:
                    try:
                        resolve_result = client._request("POST", "/api/sdk/markets/resolve", json={
                            "condition_ids": [condition_id],
                        })
                        results = resolve_result.get("results", [])
                        if results and results[0].get("found"):
                            market_id = results[0].get("market_id")
                            print(f"  ✅ Found: {market_id}")
                    except Exception:
                        pass
            if not market_id:
                print(f"  ❌ Import failed: {e}")

    if not market_id:
        print(f"  ❌ Could not resolve or import market (slug={market_slug or 'none'}, cid={condition_id[:16] if condition_id else 'none'})")
        return

    # Step 2: Execute trade
    print(f"\n💰 Executing trade...")
    try:
        result = client.trade(
            market_id=market_id,
            side=side,
            action="buy",
            amount=max_usd,
            reasoning=f"Reactor copytrading: whale {wallet[:10]}... {side_raw} ${size:.0f} @ {price:.3f} on {market_slug or condition_id}",
            source=TRADE_SOURCE,
            skill_slug=SKILL_SLUG,
            signal_data={
                "signal_source": "reactor_copytrading",
                "whale_wallet": wallet[:10],
                "whale_side": side_raw,
                "whale_size": round(size, 2),
                "whale_price": round(price, 4),
            },
        )
        if result.success:
            print(f"  ✅ Trade executed! ID: {result.trade_id}")
            print(f"     Shares: {result.shares_bought:.1f}")
        else:
            print(f"  ❌ Trade failed: {result.error or result.skip_reason or 'unknown'}")
    except Exception as e:
        print(f"  ❌ Error: {e}")


def show_positions():
    """Show current SDK positions."""
    print("\n📊 Your Polymarket Positions")
    print("=" * 50)

    try:
        data = get_positions()
        positions = data.get("positions", [])

        # Filter to active venue positions
        active_venue = os.environ.get("TRADING_VENUE", "polymarket")
        if active_venue == "simmer":
            active_venue = "sim"
        venue_positions = [p for p in positions if p.get("venue") == active_venue]

        if not venue_positions:
            print(f"No {active_venue} positions found.")
            print("\nTo start copytrading:")
            print("1. Configure target wallets in SIMMER_COPYTRADING_WALLETS")
            print("2. Run: python copytrading_trader.py")
            return

        total_value = 0
        total_pnl = 0

        for i, pos in enumerate(venue_positions, 1):
            question = pos.get("question", "Unknown market")[:50]
            shares_yes = pos.get("shares_yes", 0)
            shares_no = pos.get("shares_no", 0)
            value = pos.get("current_value", 0)
            pnl = pos.get("pnl", 0)
            pnl_pct = (pnl / pos.get("cost_basis", 1)) * 100 if pos.get("cost_basis") else 0

            total_value += value
            total_pnl += pnl

            # Determine side
            if shares_yes > shares_no:
                side = f"{shares_yes:.1f} YES"
            else:
                side = f"{shares_no:.1f} NO"

            pnl_color = "+" if pnl >= 0 else ""
            print(f"\n{i}. {question}...")
            print(f"   Position: {side}")
            print(f"   Value: ${value:.2f} | P&L: {pnl_color}${pnl:.2f} ({pnl_color}{pnl_pct:.1f}%)")

        print(f"\n{'─' * 50}")
        pnl_color = "+" if total_pnl >= 0 else ""
        print(f"Total Value: ${total_value:.2f}")
        print(f"Total P&L: {pnl_color}${total_pnl:.2f}")
        print(f"Positions: {len(venue_positions)}")

    except Exception as e:
        print(f"❌ Error fetching positions: {e}")


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Simmer Copytrading - Mirror positions from Polymarket whales"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Execute real trades (default is dry-run)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="(Default) Show what would trade without executing"
    )
    parser.add_argument(
        "--positions",
        action="store_true",
        help="Show current positions only"
    )
    parser.add_argument(
        "--config",
        action="store_true",
        help="Show current configuration"
    )
    parser.add_argument(
        "--wallets",
        type=str,
        help="Comma-separated wallet addresses (overrides env var)"
    )
    parser.add_argument(
        "--top-n",
        type=int,
        help="Number of top positions to mirror (overrides env var)"
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        help="Max USD per position (overrides env var)"
    )
    parser.add_argument(
        "--rebalance",
        action="store_true",
        help="Full rebalance mode: buy AND sell to match targets (default: buy-only)"
    )
    parser.add_argument(
        "--no-whale-exits",
        action="store_true",
        help="Disable whale exit detection (default: whale exits are detected and sold)"
    )
    parser.add_argument(
        "--venue",
        type=str,
        choices=["sim", "polymarket"],
        help="Trading venue: 'sim' for $SIM paper trading, 'polymarket' for real USDC (default: auto-detect)"
    )
    parser.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help="Set config value (e.g., --set wallets=0x123,0x456 --set max_usd=100)"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Only output on trades/errors"
    )
    parser.add_argument(
        "--reactive",
        action="store_true",
        help="Reactive mode: trade on a single reactor event (reads REACTOR_EVENT_* env vars)"
    )

    args = parser.parse_args()

    # Handle --set config updates
    if args.set:
        updates = {}
        for item in args.set:
            if "=" in item:
                key, value = item.split("=", 1)
                if key in CONFIG_SCHEMA:
                    type_fn = CONFIG_SCHEMA[key].get("type", str)
                    try:
                        value = type_fn(value)
                    except (ValueError, TypeError):
                        pass
                updates[key] = value
        if updates:
            updated = update_config(updates, __file__)
            print(f"✅ Config updated: {updates}")
            print(f"   Saved to: {get_config_path(__file__)}")
            # Reload config into module-level _config
            reloaded = load_config(CONFIG_SCHEMA, __file__, slug="polymarket-copytrading")
            globals()["_config"] = reloaded
            globals()["COPYTRADING_WALLETS"] = reloaded["wallets"]
            globals()["COPYTRADING_TOP_N"] = reloaded["top_n"]
            reloaded_max_usd = reloaded["max_usd"]
            _automaton_max = os.environ.get("AUTOMATON_MAX_BET")
            if _automaton_max:
                reloaded_max_usd = min(reloaded_max_usd, float(_automaton_max))
            globals()["COPYTRADING_MAX_USD"] = reloaded_max_usd
            globals()["MAX_TRADES_PER_RUN"] = reloaded["max_trades_per_run"]

    # Show config
    if args.config:
        print_config()
        return

    # Show positions
    if args.positions:
        show_positions()
        return

    # Default to dry-run unless --live is explicitly passed
    dry_run = not args.live

    # Reactive mode: act on a single reactor event
    if args.reactive:
        venue = args.venue or _config.get("venue") or None
        max_usd = args.max_usd if args.max_usd else COPYTRADING_MAX_USD
        run_reactive(max_usd=max_usd, dry_run=dry_run, venue=venue)
        return

    # Validate API key by initializing client
    get_client()

    # Get wallets (from args or env)
    if args.wallets:
        wallets = [w.strip() for w in args.wallets.split(",") if w.strip()]
    else:
        wallets = [w.strip() for w in COPYTRADING_WALLETS.split(",") if w.strip()]

    # Get top_n (from args or env)
    top_n = args.top_n
    if top_n is None and COPYTRADING_TOP_N:
        top_n = int(COPYTRADING_TOP_N)

    # Get max_usd (from args or env)
    max_usd = args.max_usd if args.max_usd else COPYTRADING_MAX_USD

    # Determine venue: CLI flag > config.json > TRADING_VENUE env var > None (server auto-detect)
    venue = args.venue or _config.get("venue") or None

    # Run copytrading
    run_copytrading(
        wallets=wallets,
        top_n=top_n,
        max_usd=max_usd,
        dry_run=dry_run,
        buy_only=not args.rebalance,  # Default buy_only=True, --rebalance sets it to False
        detect_whale_exits=not args.no_whale_exits,  # Default ON, --no-whale-exits disables
        venue=venue
    )

    # Fallback report for automaton if the strategy returned early (no signal)
    if os.environ.get("AUTOMATON_MANAGED") and not _automaton_reported:
        print(json.dumps({"automaton": {"signals": 0, "trades_attempted": 0, "trades_executed": 0, "skip_reason": "no_signal"}}))


if __name__ == "__main__":
    main()
