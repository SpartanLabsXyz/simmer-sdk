#!/usr/bin/env python3
"""
Mert Sniper - Near-Expiry Conviction Trading

Snipe Polymarket markets about to resolve when odds are heavily skewed.
Strategy by @mert: https://x.com/mert/status/2020216613279060433

Usage:
    python mert_sniper.py              # Dry run (show opportunities, no trades)
    python mert_sniper.py --live       # Execute real trades
    python mert_sniper.py --positions  # Show current positions only
    python mert_sniper.py --filter sol # Only scan SOL-related markets

Requires:
    SIMMER_API_KEY environment variable (get from simmer.markets/dashboard)
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

# Force line-buffered stdout so output is visible in non-TTY environments (cron, Docker, OpenClaw)
sys.stdout.reconfigure(line_buffering=True)

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
    from pathlib import Path
    return Path(skill_file).parent / config_filename

def _update_config(updates, skill_file, config_filename="config.json"):
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

load_config = _load_config
get_config_path = _get_config_path
update_config = _update_config

# Configuration schema
CONFIG_SCHEMA = {
    "market_filter": {"env": "SIMMER_MERT_FILTER", "default": "", "type": str},
    "max_bet_usd": {"env": "SIMMER_MERT_MAX_BET", "default": 10.00, "type": float},
    "expiry_window_mins": {"env": "SIMMER_MERT_EXPIRY_MINS", "default": 2, "type": int},
    "min_split": {"env": "SIMMER_MERT_MIN_SPLIT", "default": 0.60, "type": float},
    "max_trades_per_run": {"env": "SIMMER_MERT_MAX_TRADES", "default": 5, "type": int},
    "sizing_pct": {"env": "SIMMER_MERT_SIZING_PCT", "default": 0.05, "type": float},
}

_config = load_config(CONFIG_SCHEMA, __file__)

SIMMER_API_BASE = "https://api.simmer.markets"
TRADE_SOURCE = "sdk:mertsniper"

# Polymarket constraints
MIN_SHARES_PER_ORDER = 5.0
MIN_TICK_SIZE = 0.01

# Strategy parameters
MARKET_FILTER = _config["market_filter"]
MAX_BET_USD = _config["max_bet_usd"]
EXPIRY_WINDOW_MINS = _config["expiry_window_mins"]
MIN_SPLIT = _config["min_split"]
MAX_TRADES_PER_RUN = _config["max_trades_per_run"]
SMART_SIZING_PCT = _config["sizing_pct"]

# Safeguard thresholds
SLIPPAGE_MAX_PCT = 0.15

# =============================================================================
# API Helpers
# =============================================================================

def fetch_json(url, headers=None):
    try:
        req = Request(url, headers=headers or {})
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except HTTPError as e:
        print(f"  HTTP Error {e.code}: {url}")
        return None
    except URLError as e:
        print(f"  URL Error: {e.reason}")
        return None
    except Exception as e:
        print(f"  Error fetching {url}: {e}")
        return None


def get_api_key():
    key = os.environ.get("SIMMER_API_KEY")
    if not key:
        print("Error: SIMMER_API_KEY environment variable not set")
        print("Get your API key from: simmer.markets/dashboard -> SDK tab")
        sys.exit(1)
    return key


def sdk_request(api_key, method, endpoint, data=None):
    url = f"{SIMMER_API_BASE}{endpoint}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        if method == "GET":
            req = Request(url, headers=headers)
        else:
            body = json.dumps(data).encode() if data else None
            req = Request(url, data=body, headers=headers, method=method)
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        return {"error": f"HTTP {e.code}: {error_body}"}
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# SDK Wrappers
# =============================================================================

def get_portfolio(api_key):
    result = sdk_request(api_key, "GET", "/api/sdk/portfolio")
    if "error" in result:
        print(f"  Portfolio fetch failed: {result['error']}")
        return None
    return result


def get_positions(api_key):
    result = sdk_request(api_key, "GET", "/api/sdk/positions")
    if "error" in result:
        print(f"  Error fetching positions: {result['error']}")
        return []
    return result.get("positions", [])


def get_market_context(api_key, market_id):
    result = sdk_request(api_key, "GET", f"/api/sdk/context/{market_id}")
    if "error" in result:
        return None
    return result


def check_context_safeguards(context):
    """Check context for deal-breakers. Returns (should_trade, reasons)."""
    if not context:
        return True, []

    reasons = []
    warnings = context.get("warnings", [])
    discipline = context.get("discipline", {})
    slippage = context.get("slippage", {})

    for warning in warnings:
        if "MARKET RESOLVED" in str(warning).upper():
            return False, ["Market already resolved"]

    warning_level = discipline.get("warning_level", "none")
    if warning_level == "severe":
        return False, [f"Severe flip-flop warning: {discipline.get('flip_flop_warning', '')}"]
    elif warning_level == "mild":
        reasons.append("Mild flip-flop warning (proceed with caution)")

    estimates = slippage.get("estimates", []) if slippage else []
    if estimates:
        slippage_pct = estimates[0].get("slippage_pct", 0)
        if slippage_pct > SLIPPAGE_MAX_PCT:
            return False, [f"Slippage too high: {slippage_pct:.1%}"]

    return True, reasons


def execute_trade(api_key, market_id, side, amount, reasoning=""):
    return sdk_request(api_key, "POST", "/api/sdk/trade", {
        "market_id": market_id,
        "side": side,
        "amount": amount,
        "venue": "polymarket",
        "source": TRADE_SOURCE,
        "reasoning": reasoning,
    })


def calculate_position_size(api_key, default_size, smart_sizing):
    if not smart_sizing:
        return default_size

    portfolio = get_portfolio(api_key)
    if not portfolio:
        print(f"  Smart sizing failed, using default ${default_size:.2f}")
        return default_size

    balance = portfolio.get("balance_usdc", 0)
    if balance <= 0:
        print(f"  No available balance, using default ${default_size:.2f}")
        return default_size

    smart_size = balance * SMART_SIZING_PCT
    smart_size = min(smart_size, MAX_BET_USD)
    smart_size = max(smart_size, 1.0)
    print(f"  Smart sizing: ${smart_size:.2f} ({SMART_SIZING_PCT:.0%} of ${balance:.2f} balance)")
    return smart_size


# =============================================================================
# Market Fetching
# =============================================================================

def fetch_markets(api_key, market_filter=""):
    """Fetch markets from Simmer API with optional filter."""
    params = {"status": "active", "limit": 200}

    if market_filter:
        # Try tag filter first, fall back to text search
        params["tags"] = market_filter

    endpoint = "/api/sdk/markets?" + urlencode(params)
    result = sdk_request(api_key, "GET", endpoint)

    if "error" in result:
        print(f"  Failed to fetch markets: {result['error']}")
        return []

    markets = result.get("markets", [])

    # If tag filter returned nothing, try text search
    if not markets and market_filter:
        params.pop("tags", None)
        params["q"] = market_filter
        endpoint = "/api/sdk/markets?" + urlencode(params)
        result = sdk_request(api_key, "GET", endpoint)
        if "error" not in result:
            markets = result.get("markets", [])

    return markets


def parse_resolves_at(resolves_at_str):
    """Parse resolves_at string to datetime. Returns None if unparseable."""
    if not resolves_at_str:
        return None
    try:
        # Handle various ISO formats
        s = resolves_at_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def format_duration(minutes):
    """Format minutes into human-readable string."""
    if minutes < 1:
        seconds = int(minutes * 60)
        return f"{seconds}s"
    if minutes < 60:
        m = int(minutes)
        s = int((minutes - m) * 60)
        return f"{m}m {s}s" if s > 0 else f"{m}m"
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    return f"{hours}h {mins}m"


# =============================================================================
# Main Strategy Logic
# =============================================================================

def run_mert_strategy(dry_run=True, positions_only=False, show_config=False,
                      smart_sizing=False, use_safeguards=True,
                      filter_override=None, expiry_override=None):
    """Run the Mert Sniper near-expiry strategy."""
    print("🎯 Mert Sniper - Near-Expiry Conviction Trading")
    print("=" * 50)

    market_filter = filter_override if filter_override is not None else MARKET_FILTER
    expiry_mins = expiry_override if expiry_override is not None else EXPIRY_WINDOW_MINS

    if dry_run:
        print("\n  [DRY RUN] No trades will be executed. Use --live to enable trading.")

    print(f"\n  Configuration:")
    print(f"  Filter:        {market_filter or '(all markets)'}")
    print(f"  Max bet:       ${MAX_BET_USD:.2f}")
    print(f"  Expiry window: {expiry_mins} minute{'s' if expiry_mins != 1 else ''}")
    print(f"  Min split:     {MIN_SPLIT:.0%}/{1-MIN_SPLIT:.0%}")
    print(f"  Max trades:    {MAX_TRADES_PER_RUN}")
    print(f"  Smart sizing:  {'Enabled' if smart_sizing else 'Disabled'}")
    print(f"  Safeguards:    {'Enabled' if use_safeguards else 'Disabled'}")

    if show_config:
        config_path = get_config_path(__file__)
        print(f"\n  Config file: {config_path}")
        print(f"  Config exists: {'Yes' if config_path.exists() else 'No'}")
        print("\n  To change settings, either:")
        print('  1. Edit config.json: {"max_bet_usd": 5.00, "min_split": 0.65}')
        print("  2. Use --set: python mert_sniper.py --set max_bet_usd=5.00")
        print("  3. Set env vars: SIMMER_MERT_MAX_BET=5.00")
        return

    api_key = get_api_key()

    # Show portfolio if smart sizing
    if smart_sizing:
        print("\n  Portfolio:")
        portfolio = get_portfolio(api_key)
        if portfolio:
            print(f"  Balance: ${portfolio.get('balance_usdc', 0):.2f}")
            print(f"  Exposure: ${portfolio.get('total_exposure', 0):.2f}")
            print(f"  Positions: {portfolio.get('positions_count', 0)}")

    if positions_only:
        print("\n  Current Positions:")
        positions = get_positions(api_key)
        if not positions:
            print("  No open positions")
        else:
            for pos in positions:
                question = pos.get("question", "Unknown")[:50]
                sources = pos.get("sources", [])
                print(f"  - {question}...")
                print(f"    YES: {pos.get('shares_yes', 0):.1f} | NO: {pos.get('shares_no', 0):.1f} | P&L: ${pos.get('pnl', 0):.2f} | Sources: {sources}")
        return

    # Fetch markets
    print(f"\n  Fetching markets{' (filter: ' + market_filter + ')' if market_filter else ''}...")
    markets = fetch_markets(api_key, market_filter)
    print(f"  Found {len(markets)} active markets")

    if not markets:
        print("  No markets available")
        return

    # Filter by expiry window
    now = datetime.now(timezone.utc)
    expiring_markets = []

    for market in markets:
        resolves_at = parse_resolves_at(market.get("resolves_at"))
        if not resolves_at:
            continue

        minutes_remaining = (resolves_at - now).total_seconds() / 60

        # Must be within window and not already past
        if 0 < minutes_remaining <= expiry_mins:
            market["_minutes_remaining"] = minutes_remaining
            market["_resolves_at"] = resolves_at
            expiring_markets.append(market)

    print(f"\n  Markets expiring within {expiry_mins} minute{'s' if expiry_mins != 1 else ''}: {len(expiring_markets)}")

    if not expiring_markets:
        print("\n" + "=" * 50)
        print("  Summary:")
        print(f"  Markets scanned: {len(markets)}")
        print(f"  Near expiry:     0")
        if dry_run:
            print("\n  [DRY RUN MODE - no real trades executed]")
        return

    # Sort by soonest expiry
    expiring_markets.sort(key=lambda m: m["_minutes_remaining"])

    # Calculate position size once (avoids repeated portfolio API calls)
    position_size = calculate_position_size(api_key, MAX_BET_USD, smart_sizing)

    trades_executed = 0
    strong_split_count = 0

    for market in expiring_markets:
        market_id = market.get("id")
        question = market.get("question", "Unknown")
        price = market.get("current_probability") or 0.5
        mins_left = market["_minutes_remaining"]

        print(f"\n  {question[:60]}{'...' if len(question) > 60 else ''}")
        print(f"     Resolves in: {format_duration(mins_left)}")
        print(f"     Split: YES {price:.0%} / NO {1-price:.0%}")

        # Check split threshold
        if price < MIN_SPLIT and price > (1 - MIN_SPLIT):
            print(f"     Skip: split too narrow ({price:.0%}/{1-price:.0%}, need {MIN_SPLIT:.0%}+)")
            continue

        strong_split_count += 1

        # Determine side (back the favorite)
        if price >= MIN_SPLIT:
            side = "yes"
            side_price = price
        else:
            side = "no"
            side_price = 1 - price

        print(f"     Side: {side.upper()} ({side_price:.0%} >= {MIN_SPLIT:.0%})")

        # Price sanity check
        if side_price < MIN_TICK_SIZE or side_price > (1 - MIN_TICK_SIZE):
            print(f"     Skip: price at extreme (${side_price:.4f})")
            continue

        # Safeguards
        if use_safeguards:
            context = get_market_context(api_key, market_id)
            should_trade, reasons = check_context_safeguards(context)
            if not should_trade:
                print(f"     Safeguard blocked: {'; '.join(reasons)}")
                continue
            if reasons:
                print(f"     Warnings: {'; '.join(reasons)}")

        # Rate limit
        if trades_executed >= MAX_TRADES_PER_RUN:
            print(f"     Max trades ({MAX_TRADES_PER_RUN}) reached - skipping")
            continue

        # Check minimum order size
        min_cost = MIN_SHARES_PER_ORDER * side_price
        if min_cost > position_size:
            print(f"     Skip: ${position_size:.2f} too small for {MIN_SHARES_PER_ORDER} shares at ${side_price:.2f}")
            continue

        reasoning = f"Near-expiry snipe: {side.upper()} at {side_price:.0%} with {format_duration(mins_left)} to resolution"

        if dry_run:
            est_shares = position_size / side_price
            print(f"     [DRY RUN] Would buy ${position_size:.2f} on {side.upper()} (~{est_shares:.1f} shares)")
        else:
            print(f"     Executing trade...")
            result = execute_trade(api_key, market_id, side, position_size, reasoning=reasoning)

            if result.get("success"):
                trades_executed += 1
                shares = result.get("shares_bought") or result.get("shares") or 0
                print(f"     Bought {shares:.1f} {side.upper()} shares @ ${side_price:.2f}")
            else:
                error = result.get("error", "Unknown error")
                print(f"     Trade failed: {error}")

    # Summary
    print("\n" + "=" * 50)
    print("  Summary:")
    print(f"  Markets scanned: {len(markets)}")
    print(f"  Near expiry:     {len(expiring_markets)}")
    print(f"  Strong split:    {strong_split_count}")
    print(f"  Trades executed: {trades_executed}")

    if dry_run:
        print("\n  [DRY RUN MODE - no real trades executed]")


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mert Sniper - Near-Expiry Conviction Trading")
    parser.add_argument("--live", action="store_true", help="Execute real trades (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="(Default) Show opportunities without trading")
    parser.add_argument("--positions", action="store_true", help="Show current positions only")
    parser.add_argument("--config", action="store_true", help="Show current config")
    parser.add_argument("--filter", type=str, default=None, help="Override market filter (tag or keyword)")
    parser.add_argument("--expiry", type=int, default=None, help="Override expiry window in minutes")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="Set config value (e.g., --set max_bet_usd=5.00)")
    parser.add_argument("--smart-sizing", action="store_true", help="Use portfolio-based position sizing")
    parser.add_argument("--no-safeguards", action="store_true", help="Disable context safeguards")
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
            print(f"  Config updated: {updates}")
            print(f"  Saved to: {get_config_path(__file__)}")
            _config = load_config(CONFIG_SCHEMA, __file__)
            globals()["MARKET_FILTER"] = _config["market_filter"]
            globals()["MAX_BET_USD"] = _config["max_bet_usd"]
            globals()["EXPIRY_WINDOW_MINS"] = _config["expiry_window_mins"]
            globals()["MIN_SPLIT"] = _config["min_split"]
            globals()["MAX_TRADES_PER_RUN"] = _config["max_trades_per_run"]
            globals()["SMART_SIZING_PCT"] = _config["sizing_pct"]

    dry_run = not args.live

    run_mert_strategy(
        dry_run=dry_run,
        positions_only=args.positions,
        show_config=args.config,
        smart_sizing=args.smart_sizing,
        use_safeguards=not args.no_safeguards,
        filter_override=args.filter,
        expiry_override=args.expiry,
    )
