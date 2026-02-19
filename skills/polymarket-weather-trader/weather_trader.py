#!/usr/bin/env python3
"""
Simmer Weather Trading Skill

Trades Polymarket weather markets using NOAA forecasts.
Inspired by gopfan2's $2M+ weather trading strategy.

Usage:
    python weather_trader.py              # Dry run (show opportunities, no trades)
    python weather_trader.py --live       # Execute real trades
    python weather_trader.py --positions  # Show current positions only
    python weather_trader.py --smart-sizing  # Use portfolio-based position sizing

Requires:
    SIMMER_API_KEY environment variable (get from simmer.markets/dashboard)
"""

import os
import sys
import re
import json
import argparse
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

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
    "entry_threshold": {"env": "SIMMER_WEATHER_ENTRY", "default": 0.15, "type": float},
    "exit_threshold": {"env": "SIMMER_WEATHER_EXIT", "default": 0.45, "type": float},
    "max_position_usd": {"env": "SIMMER_WEATHER_MAX_POSITION", "default": 2.00, "type": float},
    "sizing_pct": {"env": "SIMMER_WEATHER_SIZING_PCT", "default": 0.05, "type": float},
    "max_trades_per_run": {"env": "SIMMER_WEATHER_MAX_TRADES", "default": 5, "type": int},
    "locations": {"env": "SIMMER_WEATHER_LOCATIONS", "default": "NYC", "type": str},
}

# Load configuration
_config = load_config(CONFIG_SCHEMA, __file__)

NOAA_API_BASE = "https://api.weather.gov"

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
        _client = SimmerClient(api_key=api_key, venue="polymarket")
    return _client

# Source tag for tracking
TRADE_SOURCE = "sdk:weather"

# Polymarket constraints
MIN_SHARES_PER_ORDER = 5.0  # Polymarket requires minimum 5 shares
MIN_TICK_SIZE = 0.01        # Minimum tradeable price

# Strategy parameters - from config
ENTRY_THRESHOLD = _config["entry_threshold"]
EXIT_THRESHOLD = _config["exit_threshold"]
MAX_POSITION_USD = _config["max_position_usd"]

# Smart sizing parameters
SMART_SIZING_PCT = _config["sizing_pct"]

# Rate limiting
MAX_TRADES_PER_RUN = _config["max_trades_per_run"]

# Context safeguard thresholds
SLIPPAGE_MAX_PCT = 0.15  # Skip if slippage > 15%
TIME_TO_RESOLUTION_MIN_HOURS = 2  # Skip if resolving in < 2 hours

# Price trend detection
PRICE_DROP_THRESHOLD = 0.10  # 10% drop in last 24h = stronger signal

# Supported locations (matching Polymarket resolution sources)
LOCATIONS = {
    "NYC": {"lat": 40.7769, "lon": -73.8740, "name": "New York City (LaGuardia)"},
    "Chicago": {"lat": 41.9742, "lon": -87.9073, "name": "Chicago (O'Hare)"},
    "Seattle": {"lat": 47.4502, "lon": -122.3088, "name": "Seattle (Sea-Tac)"},
    "Atlanta": {"lat": 33.6407, "lon": -84.4277, "name": "Atlanta (Hartsfield)"},
    "Dallas": {"lat": 32.8998, "lon": -97.0403, "name": "Dallas (DFW)"},
    "Miami": {"lat": 25.7959, "lon": -80.2870, "name": "Miami (MIA)"},
}

# Active locations - from config
_locations_str = _config["locations"]
ACTIVE_LOCATIONS = [loc.strip().upper() for loc in _locations_str.split(",") if loc.strip()]

# =============================================================================
# NOAA Weather API
# =============================================================================

def fetch_json(url, headers=None):
    """Fetch JSON from URL with error handling."""
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


def get_noaa_forecast(location: str) -> dict:
    """Get NOAA forecast for a location. Returns dict with date -> {"high": temp, "low": temp}"""
    if location not in LOCATIONS:
        print(f"  Unknown location: {location}")
        return {}

    loc = LOCATIONS[location]
    headers = {
        "User-Agent": "SimmerWeatherSkill/1.0 (https://simmer.markets)",
        "Accept": "application/geo+json",
    }

    points_url = f"{NOAA_API_BASE}/points/{loc['lat']},{loc['lon']}"
    points_data = fetch_json(points_url, headers)

    if not points_data or "properties" not in points_data:
        print(f"  Failed to get NOAA grid for {location}")
        return {}

    forecast_url = points_data["properties"].get("forecast")
    if not forecast_url:
        print(f"  No forecast URL for {location}")
        return {}

    forecast_data = fetch_json(forecast_url, headers)
    if not forecast_data or "properties" not in forecast_data:
        print(f"  Failed to get NOAA forecast for {location}")
        return {}

    periods = forecast_data["properties"].get("periods", [])
    forecasts = {}

    for period in periods:
        start_time = period.get("startTime", "")
        if not start_time:
            continue

        date_str = start_time[:10]
        temp = period.get("temperature")
        is_daytime = period.get("isDaytime", True)

        if date_str not in forecasts:
            forecasts[date_str] = {"high": None, "low": None}

        if is_daytime:
            forecasts[date_str]["high"] = temp
        else:
            forecasts[date_str]["low"] = temp

    return forecasts


# =============================================================================
# Market Parsing
# =============================================================================

def parse_weather_event(event_name: str) -> dict:
    """Parse weather event name to extract location, date, metric."""
    if not event_name:
        return None

    event_lower = event_name.lower()

    if 'highest' in event_lower or 'high temp' in event_lower:
        metric = 'high'
    elif 'lowest' in event_lower or 'low temp' in event_lower:
        metric = 'low'
    else:
        metric = 'high'

    location = None
    location_aliases = {
        'nyc': 'NYC', 'new york': 'NYC', 'laguardia': 'NYC', 'la guardia': 'NYC',
        'chicago': 'Chicago', "o'hare": 'Chicago', 'ohare': 'Chicago',
        'seattle': 'Seattle', 'sea-tac': 'Seattle',
        'atlanta': 'Atlanta', 'hartsfield': 'Atlanta',
        'dallas': 'Dallas', 'dfw': 'Dallas',
        'miami': 'Miami',
    }

    for alias, loc in location_aliases.items():
        if alias in event_lower:
            location = loc
            break

    if not location:
        return None

    month_day_match = re.search(r'on\s+([a-zA-Z]+)\s+(\d{1,2})', event_name, re.IGNORECASE)
    if not month_day_match:
        return None

    month_name = month_day_match.group(1).lower()
    day = int(month_day_match.group(2))

    month_map = {
        'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3,
        'april': 4, 'apr': 4, 'may': 5, 'june': 6, 'jun': 6, 'july': 7, 'jul': 7,
        'august': 8, 'aug': 8, 'september': 9, 'sep': 9, 'october': 10, 'oct': 10,
        'november': 11, 'nov': 11, 'december': 12, 'dec': 12,
    }

    month = month_map.get(month_name)
    if not month:
        return None

    now = datetime.now(timezone.utc)
    year = now.year
    try:
        target_date = datetime(year, month, day, tzinfo=timezone.utc)
        if target_date < now - timedelta(days=30):
            year += 1
        date_str = f"{year}-{month:02d}-{day:02d}"
    except ValueError:
        return None

    return {"location": location, "date": date_str, "metric": metric}


def parse_temperature_bucket(outcome_name: str) -> tuple:
    """Parse temperature bucket from outcome name."""
    if not outcome_name:
        return None

    below_match = re.search(r'(\d+)\s*°?[fF]?\s*(or below|or less)', outcome_name, re.IGNORECASE)
    if below_match:
        return (-999, int(below_match.group(1)))

    above_match = re.search(r'(\d+)\s*°?[fF]?\s*(or higher|or above|or more)', outcome_name, re.IGNORECASE)
    if above_match:
        return (int(above_match.group(1)), 999)

    range_match = re.search(r'(\d+)\s*[-–to]+\s*(\d+)', outcome_name)
    if range_match:
        low, high = int(range_match.group(1)), int(range_match.group(2))
        return (min(low, high), max(low, high))

    return None


# =============================================================================
# Simmer API - Core
# =============================================================================

# =============================================================================
# Simmer API - Portfolio & Context
# =============================================================================

def get_portfolio() -> dict:
    """Get portfolio summary from SDK."""
    try:
        return get_client().get_portfolio()
    except Exception as e:
        print(f"  ⚠️  Portfolio fetch failed: {e}")
        return None


def get_market_context(market_id: str, my_probability: float = None) -> dict:
    """Get market context with safeguards and optional edge analysis."""
    try:
        if my_probability is not None:
            return get_client()._request("GET", f"/api/sdk/context/{market_id}",
                                         params={"my_probability": my_probability})
        return get_client().get_market_context(market_id)
    except Exception:
        return None


def get_price_history(market_id: str) -> list:
    """Get price history for trend detection."""
    try:
        return get_client().get_price_history(market_id)
    except Exception:
        return []


def check_context_safeguards(context: dict, use_edge: bool = True) -> tuple:
    """
    Check context for safeguards. Returns (should_trade, reasons).
    
    Args:
        context: Context response from SDK
        use_edge: If True, respect edge recommendation (TRADE/HOLD/SKIP)
    """
    if not context:
        return True, []  # No context = proceed (fail open)

    reasons = []
    market = context.get("market", {})
    warnings = context.get("warnings", [])
    discipline = context.get("discipline", {})
    slippage = context.get("slippage", {})
    edge = context.get("edge", {})

    # Check for deal-breakers in warnings
    for warning in warnings:
        if "MARKET RESOLVED" in str(warning).upper():
            return False, ["Market already resolved"]

    # Check flip-flop warning
    warning_level = discipline.get("warning_level", "none")
    if warning_level == "severe":
        return False, [f"Severe flip-flop warning: {discipline.get('flip_flop_warning', '')}"]
    elif warning_level == "mild":
        reasons.append("Mild flip-flop warning (proceed with caution)")

    # Check time to resolution
    time_str = market.get("time_to_resolution", "")
    if time_str:
        try:
            hours = 0
            if "d" in time_str:
                days = int(time_str.split("d")[0].strip())
                hours += days * 24
            if "h" in time_str:
                h_part = time_str.split("h")[0]
                if "d" in h_part:
                    h_part = h_part.split("d")[-1].strip()
                hours += int(h_part)

            if hours < TIME_TO_RESOLUTION_MIN_HOURS:
                return False, [f"Resolves in {hours}h - too soon"]
        except (ValueError, IndexError):
            pass

    # Check slippage
    estimates = slippage.get("estimates", []) if slippage else []
    if estimates:
        slippage_pct = estimates[0].get("slippage_pct", 0)
        if slippage_pct > SLIPPAGE_MAX_PCT:
            return False, [f"Slippage too high: {slippage_pct:.1%}"]

    # Check edge recommendation (if available and use_edge=True)
    if use_edge and edge:
        recommendation = edge.get("recommendation")
        user_edge = edge.get("user_edge")
        threshold = edge.get("suggested_threshold", 0)
        
        if recommendation == "SKIP":
            return False, ["Edge analysis: SKIP (market resolved or invalid)"]
        elif recommendation == "HOLD":
            if user_edge is not None and threshold:
                reasons.append(f"Edge {user_edge:.1%} below threshold {threshold:.1%} - marginal opportunity")
            else:
                reasons.append("Edge analysis recommends HOLD")
        elif recommendation == "TRADE":
            reasons.append(f"Edge {user_edge:.1%} ≥ threshold {threshold:.1%} - good opportunity")

    return True, reasons


def detect_price_trend(history: list) -> dict:
    """
    Analyze price history for trends.
    Returns: {direction: "up"/"down"/"flat", change_24h: float, is_opportunity: bool}
    """
    if not history or len(history) < 2:
        return {"direction": "unknown", "change_24h": 0, "is_opportunity": False}

    # Get recent and older prices
    recent_price = history[-1].get("price_yes", 0.5)
    
    # Find price ~24h ago (assuming 15-min intervals, ~96 points)
    lookback = min(96, len(history) - 1)
    old_price = history[-lookback].get("price_yes", recent_price)

    if old_price == 0:
        return {"direction": "unknown", "change_24h": 0, "is_opportunity": False}

    change = (recent_price - old_price) / old_price

    if change < -PRICE_DROP_THRESHOLD:
        return {"direction": "down", "change_24h": change, "is_opportunity": True}
    elif change > PRICE_DROP_THRESHOLD:
        return {"direction": "up", "change_24h": change, "is_opportunity": False}
    else:
        return {"direction": "flat", "change_24h": change, "is_opportunity": False}


# =============================================================================
# Market Discovery - Auto-import from Polymarket
# =============================================================================

# Search terms per location (matching Polymarket event naming)
LOCATION_SEARCH_TERMS = {
    "NYC": ["temperature new york", "temperature nyc"],
    "Chicago": ["temperature chicago"],
    "Seattle": ["temperature seattle"],
    "Atlanta": ["temperature atlanta"],
    "Dallas": ["temperature dallas"],
    "Miami": ["temperature miami"],
}


def discover_and_import_weather_markets(log=print):
    """Discover weather markets on Polymarket and auto-import to Simmer.

    Searches the importable markets endpoint for weather events matching
    ACTIVE_LOCATIONS, then imports any that aren't already in Simmer.

    Returns count of newly imported markets.
    """
    client = get_client()
    imported_count = 0
    seen_urls = set()

    for location in ACTIVE_LOCATIONS:
        search_terms = LOCATION_SEARCH_TERMS.get(location, [f"temperature {location.lower()}"])

        for term in search_terms:
            try:
                results = client.list_importable_markets(
                    q=term, venue="polymarket", min_volume=0, limit=20
                )
            except Exception as e:
                log(f"  Discovery search failed for '{term}': {e}")
                continue

            for m in results:
                url = m.get("url", "")
                question = (m.get("question") or "").lower()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                # Filter: must be a temperature market on Polymarket
                if "temperature" not in question:
                    continue
                if not url.startswith("https://polymarket.com/"):
                    continue

                # Try to import
                try:
                    result = client.import_market(url)
                    status = result.get("status", "") if result else ""
                    if status == "imported":
                        imported_count += 1
                        log(f"  Imported: {m.get('question', url)[:70]}")
                    elif status == "already_exists":
                        pass  # Expected for most
                except Exception as e:
                    err_str = str(e)
                    if "rate limit" in err_str.lower() or "429" in err_str:
                        log(f"  Import rate limit reached — stopping discovery")
                        return imported_count
                    log(f"  Import failed for {url[:50]}: {e}")

    return imported_count


# =============================================================================
# Simmer API - Trading
# =============================================================================

def fetch_weather_markets():
    """Fetch weather-tagged markets from Simmer API."""
    try:
        result = get_client()._request("GET", "/api/sdk/markets",
                                       params={"tags": "weather", "status": "active", "limit": 100})
        return result.get("markets", [])
    except Exception:
        print("  Failed to fetch markets from Simmer API")
        return []


def execute_trade(market_id: str, side: str, amount: float) -> dict:
    """Execute a buy trade via Simmer SDK with source tagging."""
    try:
        result = get_client().trade(
            market_id=market_id, side=side, amount=amount, source=TRADE_SOURCE,
        )
        return {
            "success": result.success, "trade_id": result.trade_id,
            "shares_bought": result.shares_bought, "shares": result.shares_bought,
            "error": result.error,
        }
    except Exception as e:
        return {"error": str(e)}


def execute_sell(market_id: str, shares: float) -> dict:
    """Execute a sell trade via Simmer SDK with source tagging."""
    try:
        result = get_client().trade(
            market_id=market_id, side="yes", action="sell",
            shares=shares, source=TRADE_SOURCE,
        )
        return {
            "success": result.success, "trade_id": result.trade_id,
            "error": result.error,
        }
    except Exception as e:
        return {"error": str(e)}


def get_positions() -> list:
    """Get current positions as list of dicts."""
    try:
        positions = get_client().get_positions()
        from dataclasses import asdict
        return [asdict(p) for p in positions]
    except Exception as e:
        print(f"  Error fetching positions: {e}")
        return []


def calculate_position_size(default_size: float, smart_sizing: bool) -> float:
    """Calculate position size based on portfolio or fall back to default."""
    if not smart_sizing:
        return default_size

    portfolio = get_portfolio()
    if not portfolio:
        print(f"  ⚠️  Smart sizing failed, using default ${default_size:.2f}")
        return default_size

    balance = portfolio.get("balance_usdc", 0)
    if balance <= 0:
        print(f"  ⚠️  No available balance, using default ${default_size:.2f}")
        return default_size

    smart_size = balance * SMART_SIZING_PCT
    smart_size = min(smart_size, MAX_POSITION_USD)
    smart_size = max(smart_size, 1.0)

    print(f"  💡 Smart sizing: ${smart_size:.2f} ({SMART_SIZING_PCT:.0%} of ${balance:.2f} balance)")
    return smart_size


# =============================================================================
# Exit Strategy
# =============================================================================

def check_exit_opportunities(dry_run: bool = False, use_safeguards: bool = True) -> tuple:
    """Check open positions for exit opportunities. Returns: (exits_found, exits_executed)"""
    positions = get_positions()

    if not positions:
        return 0, 0

    weather_positions = []
    for pos in positions:
        question = pos.get("question", "").lower()
        sources = pos.get("sources", [])
        # Check if from weather skill OR has weather keywords
        if TRADE_SOURCE in sources or any(kw in question for kw in ["temperature", "°f", "highest temp", "lowest temp"]):
            weather_positions.append(pos)

    if not weather_positions:
        return 0, 0

    print(f"\n📈 Checking {len(weather_positions)} weather positions for exit...")

    exits_found = 0
    exits_executed = 0

    for pos in weather_positions:
        market_id = pos.get("market_id")
        current_price = pos.get("current_price") or pos.get("price_yes") or 0
        shares = pos.get("shares_yes") or pos.get("shares") or 0
        question = pos.get("question", "Unknown")[:50]

        if shares < MIN_SHARES_PER_ORDER:
            continue

        if current_price >= EXIT_THRESHOLD:
            exits_found += 1
            print(f"  📤 {question}...")
            print(f"     Price ${current_price:.2f} >= exit threshold ${EXIT_THRESHOLD:.2f}")

            # Check safeguards before selling
            if use_safeguards:
                context = get_market_context(market_id)
                should_trade, reasons = check_context_safeguards(context)
                if not should_trade:
                    print(f"     ⏭️  Skipped: {'; '.join(reasons)}")
                    continue
                if reasons:
                    print(f"     ⚠️  Warnings: {'; '.join(reasons)}")

            if dry_run:
                print(f"     [DRY RUN] Would sell {shares:.1f} shares")
            else:
                print(f"     Selling {shares:.1f} shares...")
                result = execute_sell(market_id, shares)

                if result.get("success"):
                    exits_executed += 1
                    trade_id = result.get("trade_id")
                    print(f"     ✅ Sold {shares:.1f} shares @ ${current_price:.2f}")

                    # Log sell trade context for journal
                    if trade_id and JOURNAL_AVAILABLE:
                        log_trade(
                            trade_id=trade_id,
                            source=TRADE_SOURCE,
                            thesis=f"Exit: price ${current_price:.2f} reached exit threshold ${EXIT_THRESHOLD:.2f}",
                            action="sell",
                        )
                else:
                    error = result.get("error", "Unknown error")
                    print(f"     ❌ Sell failed: {error}")
        else:
            print(f"  📊 {question}...")
            print(f"     Price ${current_price:.2f} < exit threshold ${EXIT_THRESHOLD:.2f} - hold")

    return exits_found, exits_executed


# =============================================================================
# Main Strategy Logic
# =============================================================================

def run_weather_strategy(dry_run: bool = True, positions_only: bool = False,
                         show_config: bool = False, smart_sizing: bool = False,
                         use_safeguards: bool = True, use_trends: bool = True,
                         quiet: bool = False):
    """Run the weather trading strategy."""
    def log(msg, force=False):
        """Print unless quiet mode is on. force=True always prints."""
        if not quiet or force:
            print(msg)

    log("🌤️  Simmer Weather Trading Skill")
    log("=" * 50)

    if dry_run:
        log("\n  [DRY RUN] No trades will be executed. Use --live to enable trading.")

    log(f"\n⚙️  Configuration:")
    log(f"  Entry threshold: {ENTRY_THRESHOLD:.0%} (buy below this)")
    log(f"  Exit threshold:  {EXIT_THRESHOLD:.0%} (sell above this)")
    log(f"  Max position:    ${MAX_POSITION_USD:.2f}")
    log(f"  Max trades/run:  {MAX_TRADES_PER_RUN}")
    log(f"  Locations:       {', '.join(ACTIVE_LOCATIONS)}")
    log(f"  Smart sizing:    {'✓ Enabled' if smart_sizing else '✗ Disabled'}")
    log(f"  Safeguards:      {'✓ Enabled' if use_safeguards else '✗ Disabled'}")
    log(f"  Trend detection: {'✓ Enabled' if use_trends else '✗ Disabled'}")

    if show_config:
        config_path = get_config_path(__file__)
        log(f"\n  Config file: {config_path}")
        log(f"  Config exists: {'Yes' if config_path.exists() else 'No'}")
        log("\n  To change settings, either:")
        log("  1. Create/edit config.json in skill directory:")
        log('     {"entry_threshold": 0.20, "exit_threshold": 0.50, "locations": "NYC,Chicago"}')
        log("  2. Or use --set flag:")
        log("     python weather_trader.py --set entry_threshold=0.20")
        log("  3. Or set environment variables (lowest priority):")
        log("     SIMMER_WEATHER_ENTRY=0.20")
        return

    # Initialize client early to validate API key
    get_client()

    # Show portfolio if smart sizing enabled
    if smart_sizing:
        log("\n💰 Portfolio:")
        portfolio = get_portfolio()
        if portfolio:
            log(f"  Balance: ${portfolio.get('balance_usdc', 0):.2f}")
            log(f"  Exposure: ${portfolio.get('total_exposure', 0):.2f}")
            log(f"  Positions: {portfolio.get('positions_count', 0)}")
            by_source = portfolio.get('by_source', {})
            if by_source:
                log(f"  By source: {json.dumps(by_source, indent=4)}")

    if positions_only:
        log("\n📊 Current Positions:")
        positions = get_positions()
        if not positions:
            log("  No open positions")
        else:
            for pos in positions:
                log(f"  • {pos.get('question', 'Unknown')[:50]}...")
                sources = pos.get('sources', [])
                log(f"    YES: {pos.get('shares_yes', 0):.1f} | NO: {pos.get('shares_no', 0):.1f} | P&L: ${pos.get('pnl', 0):.2f} | Sources: {sources}")
        return

    log("\n🔍 Discovering new weather markets on Polymarket...")
    newly_imported = discover_and_import_weather_markets(log=log)
    if newly_imported:
        log(f"  Auto-imported {newly_imported} new market(s)")
    else:
        log("  No new markets to import")

    log("\n📡 Fetching weather markets...")
    markets = fetch_weather_markets()
    log(f"  Found {len(markets)} weather markets")

    if not markets:
        log("  No weather markets available")
        return

    events = {}
    for market in markets:
        event_id = market.get("event_id") or market.get("event_name", "unknown")
        if event_id not in events:
            events[event_id] = []
        events[event_id].append(market)

    log(f"  Grouped into {len(events)} events")

    forecast_cache = {}
    trades_executed = 0
    opportunities_found = 0

    for event_id, event_markets in events.items():
        event_name = event_markets[0].get("event_name", "") if event_markets else ""
        event_info = parse_weather_event(event_name)

        if not event_info:
            continue

        location = event_info["location"]
        date_str = event_info["date"]
        metric = event_info["metric"]

        if location not in ACTIVE_LOCATIONS:
            continue

        log(f"\n📍 {location} {date_str} ({metric} temp)")

        if location not in forecast_cache:
            log(f"  Fetching NOAA forecast...")
            forecast_cache[location] = get_noaa_forecast(location)

        forecasts = forecast_cache[location]
        day_forecast = forecasts.get(date_str, {})
        forecast_temp = day_forecast.get(metric)

        if forecast_temp is None:
            log(f"  ⚠️  No forecast available for {date_str}")
            continue

        log(f"  NOAA forecast: {forecast_temp}°F")

        matching_market = None
        for market in event_markets:
            outcome_name = market.get("outcome_name", "")
            bucket = parse_temperature_bucket(outcome_name)

            if bucket and bucket[0] <= forecast_temp <= bucket[1]:
                matching_market = market
                break

        if not matching_market:
            log(f"  ⚠️  No bucket found for {forecast_temp}°F")
            continue

        outcome_name = matching_market.get("outcome_name", "")
        price = matching_market.get("external_price_yes") or 0.5
        market_id = matching_market.get("id")

        log(f"  Matching bucket: {outcome_name} @ ${price:.2f}")

        if price < MIN_TICK_SIZE:
            log(f"  ⏸️  Price ${price:.4f} below min tick ${MIN_TICK_SIZE} - skip (market at extreme)")
            continue
        if price > (1 - MIN_TICK_SIZE):
            log(f"  ⏸️  Price ${price:.4f} above max tradeable - skip (market at extreme)")
            continue

        # Check safeguards with edge analysis
        # NOAA forecasts are ~85% accurate for 1-2 day predictions when in-bucket
        noaa_probability = 0.85
        if use_safeguards:
            context = get_market_context(market_id, my_probability=noaa_probability)
            should_trade, reasons = check_context_safeguards(context)
            if not should_trade:
                log(f"  ⏭️  Safeguard blocked: {'; '.join(reasons)}")
                continue
            if reasons:
                log(f"  ⚠️  Warnings: {'; '.join(reasons)}")

        # Check price trend
        trend_bonus = ""
        if use_trends:
            history = get_price_history(market_id)
            trend = detect_price_trend(history)
            if trend["is_opportunity"]:
                trend_bonus = f" 📉 (dropped {abs(trend['change_24h']):.0%} in 24h - stronger signal!)"
            elif trend["direction"] == "up":
                trend_bonus = f" 📈 (up {trend['change_24h']:.0%} in 24h)"

        if price < ENTRY_THRESHOLD:
            position_size = calculate_position_size(MAX_POSITION_USD, smart_sizing)

            min_cost_for_shares = MIN_SHARES_PER_ORDER * price
            if min_cost_for_shares > position_size:
                log(f"  ⚠️  Position size ${position_size:.2f} too small for {MIN_SHARES_PER_ORDER} shares at ${price:.2f}")
                continue

            opportunities_found += 1
            log(f"  ✅ Below threshold (${ENTRY_THRESHOLD:.2f}) - BUY opportunity!{trend_bonus}")

            # Check rate limit
            if trades_executed >= MAX_TRADES_PER_RUN:
                log(f"  ⏸️  Max trades per run ({MAX_TRADES_PER_RUN}) reached - skipping")
                continue

            if dry_run:
                log(f"  [DRY RUN] Would buy ${position_size:.2f} worth (~{position_size/price:.1f} shares)")
            else:
                log(f"  Executing trade...", force=True)
                result = execute_trade(market_id, "yes", position_size)

                if result.get("success"):
                    trades_executed += 1
                    shares = result.get("shares_bought") or result.get("shares") or 0
                    trade_id = result.get("trade_id")
                    log(f"  ✅ Bought {shares:.1f} shares @ ${price:.2f}", force=True)

                    # Log trade context for journal
                    if trade_id and JOURNAL_AVAILABLE:
                        # Confidence based on price gap from threshold (guard against div by zero)
                        if ENTRY_THRESHOLD > 0:
                            confidence = min(0.95, (ENTRY_THRESHOLD - price) / ENTRY_THRESHOLD + 0.5)
                        else:
                            confidence = 0.7  # Default confidence if threshold is zero
                        log_trade(
                            trade_id=trade_id,
                            source=TRADE_SOURCE,
                            thesis=f"NOAA forecasts {forecast_temp}°F for {location} on {date_str}, "
                                   f"bucket '{outcome_name}' underpriced at ${price:.2f}",
                            confidence=round(confidence, 2),
                            location=location,
                            forecast_temp=forecast_temp,
                            target_date=date_str,
                            metric=metric,
                        )
                    # Risk monitors are now auto-set via SDK settings (dashboard)
                else:
                    error = result.get("error", "Unknown error")
                    log(f"  ❌ Trade failed: {error}", force=True)
        else:
            log(f"  ⏸️  Price ${price:.2f} above threshold ${ENTRY_THRESHOLD:.2f} - skip")

    exits_found, exits_executed = check_exit_opportunities(dry_run, use_safeguards)

    log("\n" + "=" * 50)
    total_trades = trades_executed + exits_executed
    show_summary = not quiet or total_trades > 0
    if show_summary:
        print("📊 Summary:")
        print(f"  Events scanned: {len(events)}")
        print(f"  Entry opportunities: {opportunities_found}")
        print(f"  Exit opportunities:  {exits_found}")
        print(f"  Trades executed:     {total_trades}")

    if dry_run and show_summary:
        print("\n  [DRY RUN MODE - no real trades executed]")


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simmer Weather Trading Skill")
    parser.add_argument("--live", action="store_true", help="Execute real trades (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="(Default) Show opportunities without trading")
    parser.add_argument("--positions", action="store_true", help="Show current positions only")
    parser.add_argument("--config", action="store_true", help="Show current config")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="Set config value (e.g., --set entry_threshold=0.20)")
    parser.add_argument("--smart-sizing", action="store_true", help="Use portfolio-based position sizing")
    parser.add_argument("--no-safeguards", action="store_true", help="Disable context safeguards")
    parser.add_argument("--no-trends", action="store_true", help="Disable price trend detection")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only output when trades execute or errors occur (ideal for high-frequency runs)")
    args = parser.parse_args()

    # Handle --set config updates
    if args.set:
        updates = {}
        for item in args.set:
            if "=" in item:
                key, value = item.split("=", 1)
                # Try to convert to appropriate type
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
            # Update module-level vars
            globals()["ENTRY_THRESHOLD"] = _config["entry_threshold"]
            globals()["EXIT_THRESHOLD"] = _config["exit_threshold"]
            globals()["MAX_POSITION_USD"] = _config["max_position_usd"]
            globals()["SMART_SIZING_PCT"] = _config["sizing_pct"]
            globals()["MAX_TRADES_PER_RUN"] = _config["max_trades_per_run"]
            _locations_str = _config["locations"]
            globals()["ACTIVE_LOCATIONS"] = [loc.strip().upper() for loc in _locations_str.split(",") if loc.strip()]

    # Default to dry-run unless --live is explicitly passed
    dry_run = not args.live

    run_weather_strategy(
        dry_run=dry_run,
        positions_only=args.positions,
        show_config=args.config,
        smart_sizing=args.smart_sizing,
        use_safeguards=not args.no_safeguards,
        use_trends=not args.no_trends,
        quiet=args.quiet,
    )
