#!/usr/bin/env python3
"""
Simmer Copytrading Skill

Mirrors positions from target Polymarket wallets via Simmer SDK.
Uses the existing copytrading_strategy.py logic server-side.

By default, runs in "buy only" mode - only buys to match whale positions,
never sells existing positions. This prevents conflicts with other strategies
(weather, etc.) that may have opened positions.

Exit handling:
- --whale-exits: Sell positions when whales exit (strategy-specific exit)
- SDK Risk Management: Stop-loss/take-profit (generic safety net) - coming soon

Usage:
    python copytrading_trader.py              # Dry run (show what would trade)
    python copytrading_trader.py --live       # Execute real trades
    python copytrading_trader.py --positions  # Show current positions
    python copytrading_trader.py --config     # Show configuration
    python copytrading_trader.py --wallets 0x... # Override wallets for this run
    python copytrading_trader.py --whale-exits   # Also sell when whales exit
    python copytrading_trader.py --rebalance  # Full rebalance mode (buy + sell)
"""

import os
import sys
import json
import argparse
from typing import Optional
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

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


# =============================================================================
# Configuration (config.json > env vars > defaults)
# =============================================================================

def _load_config(schema, skill_file, config_filename="config.json"):
    """Load config with priority: config.json > env vars > defaults."""
    from pathlib import Path
    config_path = Path(skill_file).parent / config_filename
    file_cfg = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                file_cfg = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    result = {}
    for key, spec in schema.items():
        if key in file_cfg:
            result[key] = file_cfg[key]
        elif spec.get("env") and os.environ.get(spec["env"]):
            val = os.environ.get(spec["env"])
            type_fn = spec.get("type", str)
            try:
                result[key] = type_fn(val) if type_fn != str else val
            except (ValueError, TypeError):
                result[key] = spec.get("default")
        else:
            result[key] = spec.get("default")
    return result

def _get_config_path(skill_file, config_filename="config.json"):
    """Get path to config file."""
    from pathlib import Path
    return Path(skill_file).parent / config_filename

def _update_config(updates, skill_file, config_filename="config.json"):
    """Update config values and save to file."""
    from pathlib import Path
    config_path = Path(skill_file).parent / config_filename
    existing = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    existing.update(updates)
    with open(config_path, "w") as f:
        json.dump(existing, f, indent=2)
    return existing

# Aliases for compatibility
load_config = _load_config
get_config_path = _get_config_path
update_config = _update_config

# Configuration schema
CONFIG_SCHEMA = {
    "wallets": {"env": "SIMMER_COPYTRADING_WALLETS", "default": "", "type": str},
    "top_n": {"env": "SIMMER_COPYTRADING_TOP_N", "default": "", "type": str},  # Empty = auto
    "max_usd": {"env": "SIMMER_COPYTRADING_MAX_USD", "default": 50.0, "type": float},
    "max_trades_per_run": {"env": "SIMMER_COPYTRADING_MAX_TRADES", "default": 10, "type": int},
}

# Load configuration
_config = load_config(CONFIG_SCHEMA, __file__)

# Environment variables (API key still from env for security)
SIMMER_API_KEY = os.environ.get("SIMMER_API_KEY", "")
SIMMER_API_URL = os.environ.get("SIMMER_API_URL", "https://api.simmer.markets")

# Polymarket constraints
MIN_SHARES_PER_ORDER = 5.0  # Polymarket requires minimum 5 shares
MIN_TICK_SIZE = 0.01        # Minimum price increment

# Copytrading settings - from config
COPYTRADING_WALLETS = _config["wallets"]
COPYTRADING_TOP_N = _config["top_n"]
COPYTRADING_MAX_USD = _config["max_usd"]
MAX_TRADES_PER_RUN = _config["max_trades_per_run"]


def get_config() -> dict:
    """Get current configuration."""
    wallets = [w.strip() for w in COPYTRADING_WALLETS.split(",") if w.strip()]
    top_n = int(COPYTRADING_TOP_N) if COPYTRADING_TOP_N else None

    return {
        "api_key_set": bool(SIMMER_API_KEY),
        "api_url": SIMMER_API_URL,
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
    print(f"API URL: {config['api_url']}")
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

def api_request(method: str, endpoint: str, data: dict = None) -> dict:
    """Make authenticated request to Simmer API."""
    if not SIMMER_API_KEY:
        raise ValueError("SIMMER_API_KEY environment variable not set")

    url = f"{SIMMER_API_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {SIMMER_API_KEY}",
        "Content-Type": "application/json",
    }

    if data:
        body = json.dumps(data).encode("utf-8")
    else:
        body = None

    req = Request(url, data=body, headers=headers, method=method)

    try:
        with urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as e:
        error_body = e.read().decode("utf-8")
        try:
            error_json = json.loads(error_body)
            raise ValueError(f"API error ({e.code}): {error_json.get('detail', error_body)}")
        except json.JSONDecodeError:
            raise ValueError(f"API error ({e.code}): {error_body}")
    except URLError as e:
        raise ValueError(f"Network error: {e.reason}")


def get_positions() -> dict:
    """Get current SDK positions."""
    return api_request("GET", "/api/sdk/positions")


def set_risk_monitor(market_id: str, side: str, 
                     stop_loss_pct: float = 0.20, take_profit_pct: float = 0.50) -> dict:
    """
    Set stop-loss and take-profit for a position.
    The backend monitors every 15 min and auto-exits when thresholds hit.
    """
    try:
        return api_request("POST", f"/api/sdk/positions/{market_id}/monitor", {
            "side": side,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct
        })
    except Exception as e:
        return {"error": str(e)}


def get_risk_monitors() -> dict:
    """List all active risk monitors."""
    try:
        return api_request("GET", "/api/sdk/positions/monitors")
    except Exception as e:
        return {"error": str(e)}


def remove_risk_monitor(market_id: str, side: str) -> dict:
    """Remove risk monitor for a position."""
    try:
        return api_request("DELETE", f"/api/sdk/positions/{market_id}/monitor?side={side}")
    except Exception as e:
        return {"error": str(e)}


def get_markets() -> list:
    """Get available markets."""
    result = api_request("GET", "/api/sdk/markets")
    return result.get("markets", [])


def get_context(market_id: str) -> dict:
    """Get market context (position, trades, slippage)."""
    return api_request("GET", f"/api/sdk/context/{market_id}")


def execute_trade(market_id: str, side: str, action: str, amount_usd: float = None, shares: float = None) -> dict:
    """Execute a trade via SDK."""
    data = {
        "market_id": market_id,
        "side": side,
        "action": action,
        "venue": "polymarket",
    }

    if action == "buy" and amount_usd:
        data["amount"] = amount_usd
    elif action == "sell" and shares:
        data["shares"] = shares

    return api_request("POST", "/api/sdk/trade", data)


# =============================================================================
# Copytrading Logic
# =============================================================================

def fetch_wallet_positions(wallet: str) -> list:
    """
    Fetch positions for a wallet via Simmer API.

    Note: This uses the positions endpoint. For full copytrading logic,
    the actual implementation uses the copytrading_strategy module server-side.
    """
    # This is a simplified version - the full logic runs server-side
    # via the trading agent with strategy_type='copytrading'

    # For now, we use the SDK to trigger a copytrading cycle
    # rather than reimplementing all the wallet fetching logic
    return []


def execute_copytrading(wallets: list, top_n: int = None, max_usd: float = 50.0, dry_run: bool = True, buy_only: bool = True, detect_whale_exits: bool = False, max_trades: int = None) -> dict:
    """
    Execute copytrading via Simmer SDK.

    Calls POST /api/sdk/copytrading/execute which:
    - Fetches positions from all target wallets via Dome API
    - Calculates size-weighted allocations
    - Detects and skips conflicting positions
    - Applies Top N concentration filter
    - Auto-imports missing markets
    - Calculates and executes rebalance trades
    - Filters to buy-only by default (prevents selling positions from other strategies)
    - Detects whale exits (sells positions whales no longer hold)
    - Limits trades per run via max_trades
    """
    data = {
        "wallets": wallets,
        "max_usd_per_position": max_usd,
        "dry_run": dry_run,
        "buy_only": buy_only,
        "detect_whale_exits": detect_whale_exits,
    }

    if top_n is not None:
        data["top_n"] = top_n
    
    if max_trades is not None:
        data["max_trades"] = max_trades

    return api_request("POST", "/api/sdk/copytrading/execute", data)


def run_copytrading(wallets: list, top_n: int = None, max_usd: float = 50.0, dry_run: bool = True, buy_only: bool = True, detect_whale_exits: bool = False):
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
    print(f"  Mode: {'Buy only (accumulate)' if buy_only else 'Full rebalance (buy + sell)'}")
    print(f"  Whale exits: {'Enabled (sell when whale exits)' if detect_whale_exits else 'Disabled'}")

    if dry_run:
        print("\n  [DRY RUN] No trades will be executed. Use --live to enable trading.")

    # Execute copytrading via SDK
    print("\n📡 Calling Simmer API...")
    try:
        result = execute_copytrading(wallets, top_n, max_usd, dry_run, buy_only, detect_whale_exits, MAX_TRADES_PER_RUN)
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
                        source=TRADE_SOURCE,
                        thesis=f"Copytrading: {action.upper()} {shares:.1f} {side.upper()} "
                               f"@ ${price:.3f} to mirror whale positions",
                        action=action,
                        wallets_count=len(wallets),
                    )
    else:
        print("\n✅ Scan complete")


def show_positions():
    """Show current SDK positions."""
    print("\n📊 Your Polymarket Positions")
    print("=" * 50)

    try:
        data = get_positions()
        positions = data.get("positions", [])

        # Filter to Polymarket positions
        poly_positions = [p for p in positions if p.get("venue") == "polymarket"]

        if not poly_positions:
            print("No Polymarket positions found.")
            print("\nTo start copytrading:")
            print("1. Configure target wallets in SIMMER_COPYTRADING_WALLETS")
            print("2. Run: python copytrading_trader.py")
            return

        total_value = 0
        total_pnl = 0

        for i, pos in enumerate(poly_positions, 1):
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
        print(f"Positions: {len(poly_positions)}")

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
        "--whale-exits",
        action="store_true",
        help="Sell positions when whales exit (only affects copytrading-opened positions)"
    )
    parser.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help="Set config value (e.g., --set wallets=0x123,0x456 --set max_usd=100)"
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
            # Reload config
            _config = load_config(CONFIG_SCHEMA, __file__)
            globals()["COPYTRADING_WALLETS"] = _config["wallets"]
            globals()["COPYTRADING_TOP_N"] = _config["top_n"]
            globals()["COPYTRADING_MAX_USD"] = _config["max_usd"]
            globals()["MAX_TRADES_PER_RUN"] = _config["max_trades_per_run"]

    # Show config
    if args.config:
        print_config()
        return

    # Show positions
    if args.positions:
        show_positions()
        return

    # Validate API key
    if not SIMMER_API_KEY:
        print("❌ Error: SIMMER_API_KEY environment variable not set")
        print("\nTo get an API key:")
        print("1. Go to simmer.markets/dashboard")
        print("2. Click on 'SDK' tab")
        print("3. Generate a new API key")
        print("4. Set: export SIMMER_API_KEY=sk_live_...")
        sys.exit(1)

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

    # Default to dry-run unless --live is explicitly passed
    dry_run = not args.live

    # Run copytrading
    run_copytrading(
        wallets=wallets,
        top_n=top_n,
        max_usd=max_usd,
        dry_run=dry_run,
        buy_only=not args.rebalance,  # Default buy_only=True, --rebalance sets it to False
        detect_whale_exits=args.whale_exits
    )


if __name__ == "__main__":
    main()
