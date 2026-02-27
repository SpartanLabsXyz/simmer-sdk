#!/usr/bin/env python3
"""
Simmer Elon Tweet Trader Skill

Trades Polymarket "Elon Musk # tweets" markets using XTracker post counts.
Inspired by @noovd's $345K strategy: buy adjacent range buckets when their
combined cost is less than $1 (one always resolves YES = $1).

Usage:
    python elon_tweets.py              # Dry run (show opportunities, no trades)
    python elon_tweets.py --live       # Execute real trades
    python elon_tweets.py --positions  # Show current positions only
    python elon_tweets.py --stats      # Show XTracker stats only

Requires:
    pip install simmer-sdk
    SIMMER_API_KEY environment variable (get from simmer.markets/dashboard)
    WALLET_PRIVATE_KEY environment variable (for external wallet trading)
"""

import os
import sys
import re
import json
import argparse
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# Force line-buffered stdout so output is visible in non-TTY environments (cron, Docker, OpenClaw)
sys.stdout.reconfigure(line_buffering=True)

# Optional: Trade Journal integration for tracking
try:
    from tradejournal import log_trade
    JOURNAL_AVAILABLE = True
except ImportError:
    try:
        from skills.tradejournal import log_trade
        JOURNAL_AVAILABLE = True
    except ImportError:
        JOURNAL_AVAILABLE = False
        def log_trade(*args, **kwargs):
            pass  # No-op if tradejournal not installed

# =============================================================================
# Configuration (config.json > env vars > defaults)
# =============================================================================

from simmer_sdk.skill import load_config, update_config, get_config_path

CONFIG_SCHEMA = {
    "max_bucket_sum": {"env": "SIMMER_ELON_MAX_BUCKET_SUM", "default": 0.90, "type": float},
    "max_position_usd": {"env": "SIMMER_ELON_MAX_POSITION", "default": 5.00, "type": float},
    "bucket_spread": {"env": "SIMMER_ELON_BUCKET_SPREAD", "default": 1, "type": int},
    "sizing_pct": {"env": "SIMMER_ELON_SIZING_PCT", "default": 0.05, "type": float},
    "max_trades_per_run": {"env": "SIMMER_ELON_MAX_TRADES", "default": 6, "type": int},
    "exit_threshold": {"env": "SIMMER_ELON_EXIT", "default": 0.65, "type": float},
    "slippage_max_pct": {"env": "SIMMER_ELON_SLIPPAGE_MAX", "default": 0.05, "type": float},
    "min_position_usd": {"env": "SIMMER_ELON_MIN_POSITION", "default": 2.00, "type": float},
    "data_source": {"env": "SIMMER_ELON_DATA_SOURCE", "default": "xtracker", "type": str},
}

_config = load_config(CONFIG_SCHEMA, __file__, slug="polymarket-elon-tweets")


def _reload_config_globals():
    """Reload module-level config globals from disk (used after --set)."""
    global _config, MAX_BUCKET_SUM, MAX_POSITION_USD, BUCKET_SPREAD, SMART_SIZING_PCT
    global MAX_TRADES_PER_RUN, EXIT_THRESHOLD, SLIPPAGE_MAX_PCT, MIN_POSITION_USD, DATA_SOURCE
    _config = load_config(CONFIG_SCHEMA, __file__, slug="polymarket-elon-tweets")
    MAX_BUCKET_SUM = _config["max_bucket_sum"]
    MAX_POSITION_USD = _config["max_position_usd"]
    BUCKET_SPREAD = _config["bucket_spread"]
    SMART_SIZING_PCT = _config["sizing_pct"]
    MAX_TRADES_PER_RUN = _config["max_trades_per_run"]
    EXIT_THRESHOLD = _config["exit_threshold"]
    SLIPPAGE_MAX_PCT = _config["slippage_max_pct"]
    MIN_POSITION_USD = _config["min_position_usd"]
    DATA_SOURCE = _config["data_source"]


# =============================================================================
# Failed trade cooldown (prevent retry loops on the same market)
# =============================================================================

FAILED_TRADE_COOLDOWN_MINS = 60
FAILED_TRADE_TTL_HOURS = 24

def _failed_trades_path():
    from pathlib import Path
    state_dir = Path(__file__).parent / "state"
    state_dir.mkdir(exist_ok=True)
    return state_dir / "failed_trades.json"

def _load_failed_trades():
    """Load failed trades, pruning entries older than TTL."""
    from datetime import datetime, timezone, timedelta
    path = _failed_trades_path()
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}
    # Prune old entries
    cutoff = datetime.now(timezone.utc) - timedelta(hours=FAILED_TRADE_TTL_HOURS)
    pruned = {}
    for k, v in data.items():
        try:
            ts = datetime.fromisoformat(v["failed_at"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts > cutoff:
                pruned[k] = v
        except (KeyError, ValueError):
            pass
    return pruned

def _save_failed_trades(data):
    path = _failed_trades_path()
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(str(tmp), str(path))

def is_on_cooldown(market_id, side):
    """Check if a market+side is on cooldown from a recent failure."""
    from datetime import datetime, timezone, timedelta
    failed = _load_failed_trades()
    key = f"{market_id}:{side}"
    entry = failed.get(key)
    if not entry:
        return False
    try:
        ts = datetime.fromisoformat(entry["failed_at"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() < FAILED_TRADE_COOLDOWN_MINS * 60
    except (KeyError, ValueError):
        return False

def record_failed_trade(market_id, side, error):
    """Record a failed trade for cooldown tracking."""
    from datetime import datetime, timezone
    failed = _load_failed_trades()
    key = f"{market_id}:{side}"
    failed[key] = {
        "market_id": market_id,
        "side": side,
        "error": str(error)[:200],
        "failed_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_failed_trades(failed)


XTRACKER_API_BASE = "https://xtracker.polymarket.com/api"

# Source tag for tracking
TRADE_SOURCE = "sdk:elon-tweets"
SKILL_SLUG = "polymarket-elon-tweets"
_automaton_reported = False

# Polymarket constraints
MIN_SHARES_PER_ORDER = 5.0
MIN_TICK_SIZE = 0.01

# Strategy parameters
MAX_BUCKET_SUM = _config["max_bucket_sum"]
MAX_POSITION_USD = _config["max_position_usd"]
_automaton_max = os.environ.get("AUTOMATON_MAX_BET")
if _automaton_max:
    MAX_POSITION_USD = min(MAX_POSITION_USD, float(_automaton_max))
BUCKET_SPREAD = _config["bucket_spread"]
SMART_SIZING_PCT = _config["sizing_pct"]
MAX_TRADES_PER_RUN = _config["max_trades_per_run"]
EXIT_THRESHOLD = _config["exit_threshold"]
SLIPPAGE_MAX_PCT = _config["slippage_max_pct"]
MIN_POSITION_USD = _config["min_position_usd"]
DATA_SOURCE = _config["data_source"]

# Context safeguard thresholds
TIME_TO_RESOLUTION_MIN_HOURS = 1  # Tweet markets are shorter, allow closer-to-resolution trades

# =============================================================================
# SDK Client (handles wallet linking, signing, CLOB creds automatically)
# =============================================================================

_client = None

def get_client(live=True):
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
            print("Get your API key from: simmer.markets/dashboard → SDK tab")
            sys.exit(1)
        venue = os.environ.get("TRADING_VENUE", "polymarket")
        _client = SimmerClient(api_key=api_key, venue=venue, live=live)
    return _client

# =============================================================================
# HTTP helper (for XTracker — public API, no auth)
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


# =============================================================================
# XTracker API
# =============================================================================

def get_xtracker_trackings():
    """Get active tracking periods for Elon Musk from XTracker."""
    url = f"{XTRACKER_API_BASE}/users/elonmusk/trackings?activeOnly=true"
    data = fetch_json(url)
    if not data or not data.get("success"):
        print("  Failed to fetch XTracker trackings")
        return []
    return data.get("data", [])


def get_xtracker_stats(tracking_id):
    """Get stats for a specific tracking period (includes current count + pace projection)."""
    url = f"{XTRACKER_API_BASE}/trackings/{tracking_id}?includeStats=true"
    data = fetch_json(url)
    if not data or not data.get("success"):
        return None
    return data.get("data", {})


# =============================================================================
# Market parsing
# =============================================================================

def parse_tweet_range(text):
    """Parse a tweet count range from market title/outcome. Returns (low, high) or None."""
    if not text:
        return None

    # "0-19" or "200-219" style
    range_match = re.search(r'(\d+)\s*[-–]\s*(\d+)', text)
    if range_match:
        return (int(range_match.group(1)), int(range_match.group(2)))

    # "300 or more" / "300+" style
    above_match = re.search(r'(\d+)\s*(?:or more|\+|or higher|or above)', text, re.IGNORECASE)
    if above_match:
        return (int(above_match.group(1)), 999999)

    # "less than 20" / "under 20" style
    below_match = re.search(r'(?:less than|under|below)\s*(\d+)', text, re.IGNORECASE)
    if below_match:
        return (0, int(below_match.group(1)) - 1)

    return None


def match_tracking_to_event(tracking_title, event_name):
    """Check if an XTracker tracking title matches a Simmer event name."""
    if not tracking_title or not event_name:
        return False
    # Normalize: lowercase, strip "?" marks, collapse whitespace
    t = re.sub(r'\s+', ' ', tracking_title.lower().strip().rstrip('?'))
    e = re.sub(r'\s+', ' ', event_name.lower().strip().rstrip('?'))
    # Check if one contains the other (event names may be truncated)
    return t in e or e in t


# =============================================================================
# Simmer API (via SDK client)
# =============================================================================

def get_portfolio():
    try:
        return get_client().get_portfolio()
    except Exception as e:
        print(f"  ⚠️  Portfolio fetch failed: {e}")
        return None


def get_market_context(market_id, my_probability=None):
    try:
        endpoint = f"/api/sdk/context/{market_id}"
        if my_probability is not None:
            endpoint += f"?my_probability={my_probability}"
        return get_client()._request("GET", endpoint)
    except Exception:
        return None


def get_positions():
    try:
        return get_client().get_positions()
    except Exception as e:
        print(f"  Error fetching positions: {e}")
        return []


def execute_trade(market_id, side, amount):
    """Execute a buy trade with source tagging."""
    try:
        result = get_client().trade(
            market_id=market_id,
            side=side,
            amount=amount,
            source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
        )
        return {
            "success": result.success,
            "trade_id": result.trade_id,
            "shares_bought": result.shares_bought,
            "shares": result.shares_bought,
            "error": result.error,
            "simulated": result.simulated,
        }
    except Exception as e:
        return {"error": str(e)}


def execute_sell(market_id, shares):
    """Execute a sell trade with source tagging."""
    try:
        result = get_client().trade(
            market_id=market_id,
            side="yes",
            action="sell",
            shares=shares,
            source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
        )
        return {
            "success": result.success,
            "trade_id": result.trade_id,
            "error": result.error,
            "simulated": result.simulated,
        }
    except Exception as e:
        return {"error": str(e)}


def search_markets(query):
    """Search Simmer for markets matching a query."""
    try:
        data = get_client()._request("GET", "/api/sdk/markets", params={
            "q": query, "status": "active", "limit": 100
        })
        return data.get("markets", [])
    except Exception:
        return []


def import_event(polymarket_url):
    """Import a multi-outcome event from Polymarket."""
    try:
        return get_client().import_market(polymarket_url)
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# Safeguards (reused from weather trader pattern)
# =============================================================================

def check_context_safeguards(context):
    """Check context for safeguards. Returns (should_trade, reasons)."""
    if not context:
        return True, []

    reasons = []
    market = context.get("market", {})
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

    time_str = market.get("time_to_resolution", "")
    if time_str:
        try:
            hours = 0
            if "d" in time_str:
                hours += int(time_str.split("d")[0].strip()) * 24
            if "h" in time_str:
                h_part = time_str.split("h")[0]
                if "d" in h_part:
                    h_part = h_part.split("d")[-1].strip()
                hours += int(h_part)
            if hours < TIME_TO_RESOLUTION_MIN_HOURS:
                return False, [f"Resolves in {hours}h - too soon"]
        except (ValueError, IndexError):
            pass

    estimates = slippage.get("estimates", []) if slippage else []
    if estimates:
        slippage_pct = estimates[0].get("slippage_pct", 0)
        if slippage_pct > SLIPPAGE_MAX_PCT:
            return False, [f"Slippage too high: {slippage_pct:.1%}"]

    return True, reasons


def calculate_position_size(default_size, smart_sizing):
    if not smart_sizing:
        return default_size
    portfolio = get_portfolio()
    if not portfolio:
        return default_size
    balance = portfolio.get("balance_usdc", 0)
    if balance <= 0:
        return default_size
    smart_size = min(balance * SMART_SIZING_PCT, MAX_POSITION_USD)
    smart_size = max(smart_size, MIN_POSITION_USD)
    print(f"  💡 Smart sizing: ${smart_size:.2f} ({SMART_SIZING_PCT:.0%} of ${balance:.2f})")
    return smart_size


# =============================================================================
# Core strategy
# =============================================================================

def find_target_buckets(markets, projected_count, spread=None):
    """
    Find the bucket containing the projected count and its neighbors.
    Returns list of (market, range_low, range_high, distance_from_projection).
    """
    if spread is None:
        spread = BUCKET_SPREAD
    buckets = []
    for m in markets:
        # Try outcome_name first, then question
        text = m.get("outcome_name") or m.get("question", "")
        r = parse_tweet_range(text)
        if not r:
            continue
        low, high = r
        price = m.get("external_price_yes") or m.get("current_probability") or 0.5
        buckets.append({
            "market": m,
            "low": low,
            "high": high,
            "price": price,
            "midpoint": (low + high) / 2,
        })

    if not buckets:
        return []

    # Sort by range
    buckets.sort(key=lambda b: b["low"])

    # Find the bucket containing the projection
    center_idx = None
    for i, b in enumerate(buckets):
        if b["low"] <= projected_count <= b["high"]:
            center_idx = i
            break

    # If projection doesn't land in any bucket, find nearest
    if center_idx is None:
        center_idx = min(range(len(buckets)),
                         key=lambda i: abs(buckets[i]["midpoint"] - projected_count))

    # Select center ± spread
    start = max(0, center_idx - spread)
    end = min(len(buckets), center_idx + spread + 1)

    return buckets[start:end]


def evaluate_cluster(target_buckets):
    """
    Evaluate if a cluster of buckets is +EV.
    Returns (is_profitable, total_cost, expected_profit).
    One bucket always resolves YES = $1, so if total cost < $1, it's +EV.
    """
    total_cost = sum(b["price"] for b in target_buckets)
    expected_profit = 1.0 - total_cost
    return total_cost < MAX_BUCKET_SUM, total_cost, expected_profit


# =============================================================================
# Exit strategy
# =============================================================================

def check_exit_opportunities(dry_run=True, use_safeguards=True):
    """Check open tweet positions for exit opportunities."""
    positions = get_positions()
    if not positions:
        return 0, 0

    tweet_positions = []
    for pos in positions:
        sources = pos.sources or []
        question = (pos.question or "").lower()
        if TRADE_SOURCE in sources or ("elon" in question and "tweet" in question):
            tweet_positions.append(pos)

    if not tweet_positions:
        return 0, 0

    print(f"\n📈 Checking {len(tweet_positions)} tweet positions for exit...")

    exits_found = 0
    exits_executed = 0

    for pos in tweet_positions:
        market_id = pos.market_id
        current_price = pos.current_price or 0
        shares = pos.shares_yes or 0
        question = (pos.question or "Unknown")[:50]

        if shares < MIN_SHARES_PER_ORDER:
            continue

        if current_price >= EXIT_THRESHOLD:
            exits_found += 1
            print(f"  📤 {question}...")
            print(f"     Price ${current_price:.2f} >= exit threshold ${EXIT_THRESHOLD:.2f}")

            if use_safeguards:
                context = get_market_context(market_id)
                should_trade, reasons = check_context_safeguards(context)
                if not should_trade:
                    print(f"     ⏭️  Skipped: {'; '.join(reasons)}")
                    continue

            tag = "SIMULATED" if dry_run else "LIVE"
            print(f"     Selling {shares:.1f} shares ({tag})...")
            result = execute_sell(market_id, shares)
            if result.get("success"):
                exits_executed += 1
                trade_id = result.get("trade_id")
                print(f"     ✅ {'[PAPER] ' if result.get('simulated') else ''}Sold {shares:.1f} shares @ ${current_price:.2f}")
                if trade_id and JOURNAL_AVAILABLE and not result.get("simulated"):
                    log_trade(
                        trade_id=trade_id,
                        source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
                        thesis=f"Exit: price ${current_price:.2f} reached exit threshold",
                        action="sell",
                    )
            else:
                print(f"     ❌ Sell failed: {result.get('error', 'Unknown')}")
        else:
            print(f"  📊 {question}...")
            print(f"     Price ${current_price:.2f} < exit ${EXIT_THRESHOLD:.2f} - hold")

    return exits_found, exits_executed


# =============================================================================
# Main strategy
# =============================================================================

def run_strategy(dry_run=True, positions_only=False, show_config=False,
                 show_stats=False, smart_sizing=False, use_safeguards=True,
                 quiet=False):
    """Run the Elon tweet trading strategy."""
    def log(msg, force=False):
        if not quiet or force:
            print(msg)

    log("🐦 Simmer Elon Tweet Trader")
    log("=" * 50)

    # Initialize client with paper mode when not live
    get_client(live=not dry_run)

    if dry_run:
        log("\n  [PAPER MODE] Trades will be simulated with real prices. Use --live for real trades.")

    log(f"\n⚙️  Configuration:")
    log(f"  Max bucket sum:  ${MAX_BUCKET_SUM:.2f} (buy cluster if total < this)")
    log(f"  Max position:    ${MAX_POSITION_USD:.2f} per bucket")
    log(f"  Bucket spread:   ±{BUCKET_SPREAD} (buy {2*BUCKET_SPREAD+1} adjacent buckets)")
    log(f"  Exit threshold:  {EXIT_THRESHOLD:.0%}")
    log(f"  Max trades/run:  {MAX_TRADES_PER_RUN}")
    log(f"  Data source:     {DATA_SOURCE}")
    log(f"  Smart sizing:    {'✓' if smart_sizing else '✗'}")
    log(f"  Safeguards:      {'✓' if use_safeguards else '✗'}")

    if show_config:
        config_path = get_config_path(__file__)
        log(f"\n  Config file: {config_path}")
        log(f"  Exists: {'Yes' if config_path.exists() else 'No'}")
        return

    # Client already initialized above with live=not dry_run

    if positions_only:
        log("\n📊 Current Tweet Positions:")
        positions = get_positions()
        tweet_pos = [p for p in positions if TRADE_SOURCE in (p.sources or [])
                     or ("elon" in (p.question or "").lower() and "tweet" in (p.question or "").lower())]
        if not tweet_pos:
            log("  No tweet positions found")
        else:
            for pos in tweet_pos:
                log(f"  • {(pos.question or 'Unknown')[:55]}...")
                pnl = pos.pnl or 0
                log(f"    YES: {pos.shares_yes or 0:.1f} | Price: {pos.current_price or 0:.2f} | P&L: ${pnl:.2f}")
        return

    # Step 1: Get XTracker data
    log("\n📡 Fetching XTracker data...")
    trackings = get_xtracker_trackings()

    if not trackings:
        log("  ⚠️  No active tracking periods found on XTracker")
        return

    log(f"  Found {len(trackings)} active tracking periods")

    tracking_stats = {}
    for t in trackings:
        stats = get_xtracker_stats(t["id"])
        if stats and stats.get("stats"):
            tracking_stats[t["id"]] = {
                "title": t.get("title", ""),
                "start": t.get("startDate"),
                "end": t.get("endDate"),
                "total": stats["stats"].get("total", 0),
                "pace": stats["stats"].get("pace", 0),
                "days_remaining": stats["stats"].get("daysRemaining", 0),
                "days_elapsed": stats["stats"].get("daysElapsed", 0),
                "percent_complete": stats["stats"].get("percentComplete", 0),
            }
            s = tracking_stats[t["id"]]
            log(f"\n  📊 {s['title'][:60]}")
            log(f"     Posts so far: {s['total']} | Projected: {s['pace']} | {s['percent_complete']}% complete")
            log(f"     Days remaining: {s['days_remaining']}")

    if show_stats:
        return

    if not tracking_stats:
        log("  ⚠️  No tracking stats available")
        return

    # Step 2: Search Simmer for Elon tweet markets
    log("\n📡 Searching for Elon tweet markets on Simmer...")
    markets = search_markets("elon musk tweets")

    # Group by event
    events = {}
    for m in markets:
        event_id = m.get("event_id") or m.get("event_ref")
        event_name = m.get("event_name", "")
        if not event_id:
            continue
        if event_id not in events:
            events[event_id] = {"name": event_name, "markets": []}
        events[event_id]["markets"].append(m)

    log(f"  Found {len(markets)} markets in {len(events)} events")

    # Step 3: If no markets found, try importing from Polymarket
    if not events:
        log("\n  No Elon tweet markets on Simmer yet. Attempting import...")
        # Use XTracker tracking titles to find Polymarket event slugs
        for tid, stats in tracking_stats.items():
            title = stats["title"]
            # Convert title to slug: "Elon Musk # tweets February 13 - February 20, 2026?"
            # → "elon-musk-of-tweets-february-13-february-20"
            # Polymarket uses "of-tweets" not "tweets", and omits the year
            slug = title.lower()
            slug = slug.replace('# tweets', 'of-tweets').replace('#tweets', 'of-tweets')
            slug = re.sub(r',?\s*\d{4}\??$', '', slug)  # Strip trailing year + question mark
            slug = re.sub(r'[?,]', '', slug)
            slug = re.sub(r'\s+', '-', slug.strip())
            slug = re.sub(r'-+', '-', slug).strip('-')

            pm_url = f"https://polymarket.com/event/{slug}"
            log(f"  Importing: {pm_url}")

            result = import_event(pm_url)
            if isinstance(result, dict) and result.get("success"):
                imported_count = result.get("markets_imported", 0)
                log(f"  ✅ Imported {imported_count} markets")
                event_id = result.get("event_id")
                if event_id and result.get("markets"):
                    events[event_id] = {
                        "name": result.get("event_name", title),
                        "markets": result["markets"],
                    }
            elif isinstance(result, dict) and result.get("already_imported"):
                log(f"  Already imported — using existing markets")
                event_id = result.get("event_id")
                if event_id and result.get("markets"):
                    events[event_id] = {
                        "name": result.get("event_name", title),
                        "markets": result["markets"],
                    }
                elif event_id:
                    # Response didn't include markets — fall back to search
                    mkt_list = search_markets(title[:50])
                    if mkt_list:
                        events[event_id] = {
                            "name": result.get("event_name", title),
                            "markets": mkt_list,
                        }
                        log(f"  Found {len(mkt_list)} existing markets via search")
                    else:
                        log(f"  ⚠️  Could not retrieve existing markets")
            else:
                error = result.get("error") if isinstance(result, dict) else str(result)
                log(f"  ⚠️  Import failed: {error}")

    if not events:
        log("\n  No Elon tweet markets available to trade.")
        return

    # Step 4: Match events to tracking periods and evaluate
    trades_executed = 0
    total_usd_spent = 0.0
    opportunities_found = 0
    skip_reasons = []
    execution_errors = []

    if smart_sizing:
        portfolio = get_portfolio()
        if portfolio:
            log(f"\n💰 Balance: ${portfolio.get('balance_usdc', 0):.2f}")

    for event_id, event_data in events.items():
        event_name = event_data["name"]
        event_markets = event_data["markets"]

        # Find matching XTracker tracking
        matched_stats = None
        for tid, stats in tracking_stats.items():
            if match_tracking_to_event(stats["title"], event_name):
                matched_stats = stats
                break

        if not matched_stats:
            log(f"\n  ⚠️  No XTracker data for: {event_name[:50]}")
            continue

        projected_count = matched_stats["pace"]
        current_count = matched_stats["total"]
        pct_complete = matched_stats["percent_complete"]
        days_remaining = matched_stats["days_remaining"]

        log(f"\n🎯 {event_name[:60]}")
        log(f"   Current: {current_count} posts | Projected: {projected_count} | {pct_complete}% done")
        log(f"   Markets: {len(event_markets)}")

        # Skip future events where pace projection is unreliable
        if current_count == 0 and days_remaining > 2:
            log(f"   ⏸️  Event hasn't started yet ({current_count} posts, {days_remaining} days remaining) - projection unreliable, skipping")
            continue

        # Find target buckets around projection
        targets = find_target_buckets(event_markets, projected_count)
        if not targets:
            log(f"   ⚠️  Could not find matching buckets for projection {projected_count}")
            continue

        # Evaluate cluster profitability
        is_profitable, total_cost, expected_profit = evaluate_cluster(targets)

        bucket_names = [f"{b['low']}-{b['high']}" for b in targets]
        log(f"   Target buckets: {', '.join(bucket_names)}")
        log(f"   Cluster cost: ${total_cost:.2f} (threshold: ${MAX_BUCKET_SUM:.2f})")

        if is_profitable:
            log(f"   ✅ +EV opportunity! Expected profit: ${expected_profit:.2f} per $1 resolved")
        else:
            log(f"   ⏸️  Cluster too expensive (${total_cost:.2f} >= ${MAX_BUCKET_SUM:.2f}) - skip")
            continue

        # Trade each bucket in the cluster
        for bucket in targets:
            m = bucket["market"]
            market_id = m.get("market_id") or m.get("id")
            price = bucket["price"]
            bucket_label = f"{bucket['low']}-{bucket['high']}"

            if not market_id:
                continue

            if price < MIN_TICK_SIZE:
                log(f"   ⏸️  {bucket_label}: price ${price:.4f} below min tick - skip")
                skip_reasons.append("price at extreme")
                continue
            if price > (1 - MIN_TICK_SIZE):
                log(f"   ⏸️  {bucket_label}: price ${price:.4f} too high - skip")
                skip_reasons.append("price at extreme")
                continue

            # Check safeguards
            if use_safeguards:
                # Our probability: projected count landing in this bucket
                # Higher confidence for center bucket, lower for edges
                my_prob = 0.50 if bucket["low"] <= projected_count <= bucket["high"] else 0.25
                context = get_market_context(market_id, my_probability=my_prob)
                should_trade, reasons = check_context_safeguards(context)
                if not should_trade:
                    log(f"   ⏭️  {bucket_label}: {'; '.join(reasons)}")
                    skip_reasons.append(f"safeguard: {reasons[0]}")
                    continue
                if reasons:
                    log(f"   ⚠️  {bucket_label}: {'; '.join(reasons)}")

            position_size = calculate_position_size(MAX_POSITION_USD, smart_sizing)

            min_cost = MIN_SHARES_PER_ORDER * price
            if min_cost > position_size:
                log(f"   ⚠️  {bucket_label}: position ${position_size:.2f} too small for min shares at ${price:.2f}")
                skip_reasons.append("position too small")
                continue

            if trades_executed >= MAX_TRADES_PER_RUN:
                log(f"   ⏸️  Max trades per run ({MAX_TRADES_PER_RUN}) reached")
                skip_reasons.append("max trades reached")
                break

            # Check cooldown from previous failures
            if is_on_cooldown(market_id, "yes"):
                log(f"   ⏭️  {bucket_label}: on cooldown (failed recently)")
                skip_reasons.append("cooldown")
                continue

            opportunities_found += 1

            tag = "SIMULATED" if dry_run else "LIVE"
            log(f"   Buying {bucket_label} @ ${price:.2f} ({tag})...", force=True)
            result = execute_trade(market_id, "yes", position_size)

            if result.get("success"):
                trades_executed += 1
                total_usd_spent += position_size
                shares = result.get("shares_bought") or result.get("shares") or 0
                trade_id = result.get("trade_id")
                log(f"   ✅ {'[PAPER] ' if result.get('simulated') else ''}Bought {shares:.1f} shares of {bucket_label} @ ${price:.2f}", force=True)

                if trade_id and JOURNAL_AVAILABLE and not result.get("simulated"):
                    log_trade(
                        trade_id=trade_id,
                        source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
                        thesis=f"XTracker projects {projected_count} posts, bucket {bucket_label} "
                               f"underpriced at ${price:.2f} (cluster cost ${total_cost:.2f})",
                        confidence=round(0.7 if bucket["low"] <= projected_count <= bucket["high"] else 0.4, 2),
                        projected_count=projected_count,
                        current_count=current_count,
                    )
            else:
                error_msg = result.get('error', 'Unknown')
                log(f"   ❌ Trade failed: {error_msg}", force=True)
                execution_errors.append(error_msg[:120])
                record_failed_trade(market_id, "yes", error_msg)

    # Check exits
    exits_found, exits_executed = check_exit_opportunities(dry_run, use_safeguards)

    # Summary
    log("\n" + "=" * 50)
    total_trades = trades_executed + exits_executed
    show_summary = not quiet or total_trades > 0
    if show_summary:
        print("📊 Summary:")
        print(f"  Events analyzed:     {len(events)}")
        print(f"  Entry opportunities: {opportunities_found}")
        print(f"  Exit opportunities:  {exits_found}")
        print(f"  Trades executed:     {total_trades}")

    # Structured report for automaton
    if os.environ.get("AUTOMATON_MANAGED"):
        global _automaton_reported
        report = {"signals": opportunities_found + exits_found, "trades_attempted": opportunities_found + exits_found, "trades_executed": total_trades, "amount_usd": round(total_usd_spent, 2)}
        if (opportunities_found + exits_found) > 0 and total_trades == 0 and skip_reasons:
            report["skip_reason"] = ", ".join(dict.fromkeys(skip_reasons))
        if execution_errors:
            report["execution_errors"] = execution_errors
        print(json.dumps({"automaton": report}))
        _automaton_reported = True

    if dry_run and show_summary:
        print("\n  [PAPER MODE - trades simulated with real prices]")


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simmer Elon Tweet Trader")
    parser.add_argument("--live", action="store_true", help="Execute real trades")
    parser.add_argument("--dry-run", action="store_true", help="(Default) Show opportunities without trading")
    parser.add_argument("--positions", action="store_true", help="Show current tweet positions only")
    parser.add_argument("--stats", action="store_true", help="Show XTracker stats only")
    parser.add_argument("--config", action="store_true", help="Show current config")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="Set config value (e.g., --set max_bucket_sum=0.85)")
    parser.add_argument("--smart-sizing", action="store_true", help="Use portfolio-based position sizing")
    parser.add_argument("--no-safeguards", action="store_true", help="Disable context safeguards")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only output on trades/errors")
    args = parser.parse_args()

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
            update_config(updates, __file__)
            _reload_config_globals()
            print(f"✅ Config updated: {updates}")
            print(f"   Saved to: {get_config_path(__file__)}")

    dry_run = not args.live

    run_strategy(
        dry_run=dry_run,
        positions_only=args.positions,
        show_config=args.config,
        show_stats=args.stats,
        smart_sizing=args.smart_sizing,
        use_safeguards=not args.no_safeguards,
        quiet=args.quiet,
    )

    # Fallback report for automaton if the strategy returned early (no signal)
    if os.environ.get("AUTOMATON_MANAGED") and not _automaton_reported:
        print(json.dumps({"automaton": {"signals": 0, "trades_attempted": 0, "trades_executed": 0, "skip_reason": "no_signal"}}))
