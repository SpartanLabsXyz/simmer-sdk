#!/usr/bin/env python3
"""
Signal Sniper - Surface news signals from user-configured sources.

Pattern A skill with SDK infrastructure:
- Skill handles: RSS polling, keyword matching, market safeguards
- SDK provides: context endpoint (safeguards)
- Your agent decides: whether the article bears on the market, which side, how much

The skill never places trades. It emits article + market pairs for your agent
to judge. Keyword sentiment cannot tell whether a headline is about a market,
and RSS news is usually priced in before it reaches the feed.

Usage:
    python signal_sniper.py                     # Scan and print signals
    python signal_sniper.py --json              # Print signals as JSON for your agent
    python signal_sniper.py --config            # Show configuration
    python signal_sniper.py --history           # Show processed articles
    python signal_sniper.py --feed URL          # Override feed for one run
    python signal_sniper.py --keywords "a,b,c"  # Override keywords
    python signal_sniper.py --market ID         # Override target market
"""

import os
import sys
import json
import hashlib
import argparse
import contextlib
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# Force line-buffered stdout so output is visible in non-TTY environments (cron, Docker, OpenClaw)
sys.stdout.reconfigure(line_buffering=True)
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

# Try to use defusedxml for secure XML parsing (XXE protection)
try:
    import defusedxml.ElementTree as DefusedET
    _USE_DEFUSEDXML = True
except ImportError:
    _USE_DEFUSEDXML = False

# Source tag your agent should pass to client.trade() if it acts on a signal
TRADE_SOURCE = "sdk:signalsniper"
SKILL_SLUG = "polymarket-signal-sniper"
_automaton_reported = False

from simmer_sdk.skill import load_config, update_config, get_config_path

# Configuration schema
CONFIG_SCHEMA = {
    "feeds": {"env": "SIMMER_SNIPER_FEEDS", "default": "", "type": str},
    "markets": {"env": "SIMMER_SNIPER_MARKETS", "default": "", "type": str},
    "keywords": {"env": "SIMMER_SNIPER_KEYWORDS", "default": "", "type": str},
}

# Load configuration
_config = load_config(CONFIG_SCHEMA, __file__, slug="polymarket-signal-sniper")

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
        venue = os.environ.get("TRADING_VENUE", "polymarket")
        _client = SimmerClient(api_key=api_key, venue=venue)
    return _client

# Sniper configuration - from config
FEEDS = _config["feeds"]
MARKETS = _config["markets"]
KEYWORDS = _config["keywords"]

# State file for deduplication
STATE_DIR = Path(__file__).parent / "state"
PROCESSED_FILE = STATE_DIR / "processed.json"

# Trading safeguard thresholds
SLIPPAGE_HIGH_THRESHOLD = 0.15       # >15% slippage = reduce size warning
SLIPPAGE_MODERATE_THRESHOLD = 0.10   # >10% slippage = moderate warning
SPREAD_MAX_THRESHOLD = 0.10          # >10% spread = illiquid, skip
TIME_CRITICAL_HOURS = 2              # <2h to resolution = very high risk
TIME_ELEVATED_HOURS = 6              # <6h to resolution = elevated risk
REQUEST_TIMEOUT_SECONDS = 30         # HTTP request timeout
HISTORY_DISPLAY_LIMIT = 20           # Number of articles to show in history
SUMMARY_TRUNCATE_LENGTH = 500        # Max chars for article summary


def get_config() -> Dict[str, Any]:
    """Get current configuration."""
    return {
        "feeds": [f.strip() for f in FEEDS.split(",") if f.strip()],
        "markets": [m.strip() for m in MARKETS.split(",") if m.strip()],
        "keywords": [k.strip().lower() for k in KEYWORDS.split(",") if k.strip()],
    }


def show_config():
    """Display current configuration."""
    config = get_config()
    config_path = get_config_path(__file__)
    print("🎯 Signal Sniper Configuration")
    print("=" * 40)
    print(f"API Key: {'✓ Set' if os.environ.get('SIMMER_API_KEY') else '✗ Missing'}")
    print()
    print("RSS Feeds:")
    if config["feeds"]:
        for feed in config["feeds"]:
            print(f"  • {feed[:60]}...")
    else:
        print("  (none configured)")
    print()
    print("Target Markets:")
    if config["markets"]:
        for market in config["markets"]:
            print(f"  • {market}")
    else:
        print("  (none configured)")
    print()
    print("Keywords:")
    if config["keywords"]:
        print(f"  {', '.join(config['keywords'])}")
    else:
        print("  (none - all articles will match)")
    print()
    print(f"Config file: {config_path}")
    print(f"Config exists: {'Yes' if config_path.exists() else 'No'}")
    print("\nTo change settings:")
    print("  --set feeds=https://rss.example.com/feed1,https://rss.example.com/feed2")
    print("  --set keywords=bitcoin,ethereum,crypto")


def load_processed() -> Dict[str, Dict]:
    """Load processed articles from state file."""
    if not PROCESSED_FILE.exists():
        return {}
    try:
        with open(PROCESSED_FILE, "r") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)  # Shared lock for reading
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (json.JSONDecodeError, IOError):
        return {}


PROCESSED_TTL_DAYS = 7  # Prune entries older than this on save


def save_processed(processed: Dict[str, Dict]):
    """Save processed articles to state file with file locking. Prunes entries older than TTL."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Prune stale entries
    cutoff_dt = datetime.now(timezone.utc)
    pruned = {}
    for k, v in processed.items():
        processed_at = v.get("processed_at", "")
        try:
            entry_dt = datetime.fromisoformat(processed_at.replace("Z", "+00:00"))
            if (cutoff_dt - entry_dt).days <= PROCESSED_TTL_DAYS:
                pruned[k] = v
        except (ValueError, TypeError):
            pruned[k] = v  # Keep entries with unparseable timestamps
    processed = pruned

    # Write to temp file then atomic rename to prevent corruption
    temp_file = PROCESSED_FILE.with_suffix(".tmp")
    with open(temp_file, "w") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Exclusive lock for writing
        try:
            json.dump(processed, f, indent=2, default=str)
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    temp_file.rename(PROCESSED_FILE)  # Atomic rename


def article_hash(url: str, title: str) -> str:
    """Generate unique hash for an article."""
    content = f"{url}|{title}".encode("utf-8")
    return hashlib.sha256(content).hexdigest()[:16]


def show_history():
    """Show recently processed articles."""
    processed = load_processed()
    if not processed:
        print("📜 No articles processed yet.")
        return

    print("📜 Processed Articles")
    print("=" * 40)

    # Sort by timestamp, most recent first
    items = sorted(
        processed.items(),
        key=lambda x: x[1].get("processed_at", ""),
        reverse=True
    )[:HISTORY_DISPLAY_LIMIT]

    for _, data in items:
        title = data.get("title", "Unknown")[:50]
        action = data.get("action", "unknown")
        processed_at = data.get("processed_at", "?")
        print(f"  [{action:10}] {title}...")
        print(f"              at {processed_at}")
        print()


def validate_url(url: str) -> bool:
    """Validate URL is safe to fetch (prevent SSRF)."""
    try:
        parsed = urlparse(url)
        # Only allow http/https
        if parsed.scheme not in ['http', 'https']:
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        # Block localhost, private IPs, cloud metadata endpoints
        blocked = ['localhost', '127.0.0.1', '0.0.0.0', '169.254.169.254', '::1']
        if hostname.lower() in blocked:
            return False
        # Block private IP ranges (basic check)
        if hostname.startswith(('10.', '192.168.', '172.16.', '172.17.', '172.18.', '172.19.',
                                '172.20.', '172.21.', '172.22.', '172.23.', '172.24.', '172.25.',
                                '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.')):
            return False
        return True
    except Exception:
        return False


def fetch_rss(url: str) -> List[Dict[str, str]]:
    """Fetch and parse RSS feed."""
    articles = []

    # Validate URL before fetching (SSRF protection)
    if not validate_url(url):
        print(f"  ⚠️ Invalid or blocked URL: {url[:50]}...")
        return articles

    try:
        req = Request(url, headers={"User-Agent": "SimmerSignalSniper/1.0"})
        with urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            content = response.read()

        # Secure XML parsing - use defusedxml if available for XXE protection
        if _USE_DEFUSEDXML:
            root = DefusedET.fromstring(content)
        else:
            # Standard parsing - consider installing defusedxml for XXE protection
            root = ET.fromstring(content)

        # Handle both RSS and Atom feeds
        # RSS: channel/item
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            description = item.findtext("description", "")
            pub_date = item.findtext("pubDate", "")

            if title and link:
                articles.append({
                    "title": title,
                    "url": link,
                    "summary": description[:SUMMARY_TRUNCATE_LENGTH] if description else "",
                    "published": pub_date,
                })

        # Atom: entry
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//atom:entry", ns):
            title = entry.findtext("atom:title", "", ns)
            link_elem = entry.find("atom:link", ns)
            link = link_elem.get("href", "") if link_elem is not None else ""
            summary = entry.findtext("atom:summary", "", ns)
            published = entry.findtext("atom:published", "", ns)

            if title and link:
                articles.append({
                    "title": title,
                    "url": link,
                    "summary": summary[:SUMMARY_TRUNCATE_LENGTH] if summary else "",
                    "published": published,
                })

    except (URLError, HTTPError) as e:
        print(f"  ⚠️ Failed to fetch {url[:50]}...: {e}")
    except ET.ParseError as e:
        print(f"  ⚠️ Failed to parse {url[:50]}...: {e}")

    return articles


def matches_keywords(article: Dict[str, str], keywords: List[str]) -> bool:
    """Check if article matches any keyword."""
    if not keywords:
        return True  # No keywords = match all

    text = f"{article['title']} {article['summary']}".lower()
    return any(kw in text for kw in keywords)


def discover_markets(keywords: List[str], log=print) -> List[str]:
    """Auto-discover markets by matching keywords against active Simmer markets.

    Fetches all active markets from Simmer and returns IDs of markets whose
    question text matches any of the configured keywords.
    """
    try:
        markets = get_client().get_markets(status="active", limit=200)
    except Exception as e:
        log(f"  ⚠️ Market discovery failed: {e}")
        return []

    markets = [m for m in markets if getattr(m, 'is_live_now', True) is not False]  # skip not-yet-open markets (no-op if field absent)

    if not keywords:
        return []

    matched_ids = []
    for market in markets:
        question = (market.question or "").lower()
        if any(kw in question for kw in keywords):
            matched_ids.append(market.id)

    return matched_ids


def get_market_context(market_id: str, my_probability: float = None) -> Optional[Dict]:
    """
    Get SDK context for a market (safeguards + optional edge analysis).

    Args:
        market_id: Market ID
        my_probability: Your probability estimate (0-1) for edge calculation
    """
    try:
        # get_market_context doesn't support my_probability param, use _request
        if my_probability is not None:
            return get_client()._request("GET", f"/api/sdk/context/{market_id}",
                                         params={"my_probability": my_probability})
        return get_client().get_market_context(market_id)
    except Exception:
        return None


def check_safeguards(context: Dict) -> Tuple[bool, List[str]]:
    """
    Check context warnings and return (should_trade, reasons).

    Returns (True, []) if safe to trade.
    Returns (False, [reasons]) if should skip.
    """
    reasons = []

    warnings = context.get("warnings") or []
    discipline = context.get("discipline") or {}
    slippage = context.get("slippage") or {}
    market = context.get("market") or {}

    # Check for deal-breakers
    for warning in warnings:
        if "MARKET RESOLVED" in warning:
            reasons.append("Market already resolved")
            return False, reasons

    # Check flip-flop
    warning_level = discipline.get("warning_level", "none")
    if warning_level == "severe":
        reasons.append(f"Severe flip-flop warning: {discipline.get('flip_flop_warning', '')}")
        return False, reasons
    elif warning_level == "mild":
        reasons.append(f"Mild flip-flop warning (proceed with caution)")

    # Check time decay
    time_to_resolution = market.get("time_to_resolution", "")
    if time_to_resolution:
        # Parse time to hours (handles "Xd Yh" or "Xh" formats)
        try:
            total_hours = 0
            if "d" in time_to_resolution:
                days_part = time_to_resolution.split("d")[0].strip()
                total_hours += int(days_part) * 24
            if "h" in time_to_resolution:
                hours_part = time_to_resolution.split("h")[0]
                if "d" in hours_part:
                    hours_part = hours_part.split("d")[-1].strip()
                total_hours += int(hours_part)

            if total_hours < TIME_CRITICAL_HOURS:
                reasons.append(f"Market resolves in {total_hours}h - very high risk")
                return False, reasons
            elif total_hours < TIME_ELEVATED_HOURS:
                reasons.append(f"Market resolves in {total_hours}h - elevated risk")
        except (ValueError, IndexError):
            pass

    # Check slippage
    estimates = slippage.get("estimates", []) if slippage else []
    if estimates:
        slippage_pct = estimates[0].get("slippage_pct", 0)
        if slippage_pct > SLIPPAGE_HIGH_THRESHOLD:
            reasons.append(f"High slippage ({slippage_pct:.1%}) - reduce size")
        elif slippage_pct > SLIPPAGE_MODERATE_THRESHOLD:
            reasons.append(f"Moderate slippage ({slippage_pct:.1%})")

    # Check spread
    spread_pct = slippage.get("spread_pct", 0) if slippage else 0
    if spread_pct > SPREAD_MAX_THRESHOLD:
        reasons.append(f"Wide spread ({spread_pct:.1%}) - illiquid market")
        return False, reasons

    # Check edge recommendation (if available)
    edge = context.get("edge") or {}
    if edge:
        recommendation = edge.get("recommendation")
        user_edge = edge.get("user_edge")
        threshold = edge.get("suggested_threshold", 0)
        
        if recommendation == "SKIP":
            reasons.append("Edge analysis: SKIP (market resolved or invalid)")
            return False, reasons
        elif recommendation == "HOLD":
            if user_edge is not None and threshold:
                reasons.append(f"Edge {user_edge:.1%} below threshold {threshold:.1%}")
        elif recommendation == "TRADE":
            reasons.append(f"Edge {user_edge:.1%} ≥ threshold {threshold:.1%} - good opportunity")

    return True, reasons


def format_context_summary(context: Dict) -> str:
    """Format context for display."""
    market = context.get("market") or {}
    position = context.get("position") or {}
    discipline = context.get("discipline") or {}

    lines = []
    lines.append(f"  Market: {market.get('question', 'Unknown')[:60]}...")
    lines.append(f"  Price: {market.get('current_price', 0):.1%}")

    if market.get("resolution_criteria"):
        lines.append(f"  Resolution: {market.get('resolution_criteria')[:80]}...")

    if market.get("ai_consensus"):
        lines.append(f"  Simmer AI: {market.get('ai_consensus'):.1%}")
        if market.get("divergence"):
            div = market.get("divergence")
            direction = "bullish" if div > 0 else "bearish"
            lines.append(f"  Divergence: {abs(div):.1%} more {direction}")

    if position.get("has_position"):
        lines.append(f"  Position: {position.get('shares', 0):.1f} {position.get('side', '?').upper()} (P&L: {position.get('pnl_pct', 0):.1%})")

    if discipline.get("warning_level") != "none":
        lines.append(f"  Discipline: {discipline.get('flip_flop_warning', '')}")

    return "\n".join(lines)


def run_scan(
    feeds: List[str],
    markets: List[str],
    keywords: List[str],
) -> Dict[str, Any]:
    """
    Run signal scan across feeds and markets.

    Emits every new article + market pair that passes the safeguards. It does
    not judge relevance or direction and never trades: that is the agent's call.

    Returns summary of results.
    """
    global _automaton_reported

    # Validate API key via client init
    try:
        get_client()
    except SystemExit:
        return {"error": "No API key"}

    if not feeds:
        print("❌ No RSS feeds configured")
        print("")
        print("   This skill surfaces signals from YOUR RSS feeds — you bring the edge.")
        print("")
        print("   To configure, set the SIMMER_SNIPER_FEEDS env var to a comma-separated list of RSS URLs:")
        print("")
        print("   Examples:")
        print("     SIMMER_SNIPER_FEEDS=https://feeds.feedburner.com/CoinDesk")
        print("     SIMMER_SNIPER_FEEDS=https://cointelegraph.com/rss,https://decrypt.co/feed")
        print("")
        print("   Also set keywords and target markets:")
        print("     SIMMER_SNIPER_KEYWORDS=bitcoin,btc,ethereum,crypto")
        print("     SIMMER_SNIPER_MARKETS=<market-id>  (or leave empty to auto-discover from keywords)")
        print("")
        print("   Full docs: https://docs.simmer.markets/skills/signal-sniper")
        return {"error": "No feeds"}

    if not markets:
        if keywords:
            print("🔍 No markets configured — auto-discovering from keywords...")
            markets = discover_markets(keywords)
            if markets:
                print(f"   Found {len(markets)} matching markets")
            else:
                print("❌ No markets found matching keywords")
                print("   Set SIMMER_SNIPER_MARKETS or use --market ID")
                return {"error": "No markets"}
        else:
            print("❌ No target markets configured and no keywords for discovery")
            print("   Set SIMMER_SNIPER_MARKETS or use --market ID")
            return {"error": "No markets"}

    skip_reasons = []
    results = {
        "feeds_scanned": len(feeds),
        "articles_found": 0,
        "articles_matched": 0,
        "articles_new": 0,
        "pairs_skipped": 0,
        "signals": [],
    }

    processed = load_processed()

    print(f"🎯 Signal Sniper Scan")
    print(f"   Feeds: {len(feeds)} | Markets: {len(markets)} | Keywords: {len(keywords) or 'all'}")
    print()

    # 1. Fetch all articles from all feeds
    all_articles = []
    for feed_url in feeds:
        print(f"📡 Fetching: {feed_url[:50]}...")
        articles = fetch_rss(feed_url)
        print(f"   Found {len(articles)} articles")
        all_articles.extend(articles)

    results["articles_found"] = len(all_articles)

    # 2. Filter by keywords
    matched_articles = [a for a in all_articles if matches_keywords(a, keywords)]
    results["articles_matched"] = len(matched_articles)
    print(f"\n📋 Matched {len(matched_articles)}/{len(all_articles)} articles by keywords")

    # 3. Filter already processed
    new_articles = []
    for article in matched_articles:
        h = article_hash(article["url"], article["title"])
        if h not in processed:
            new_articles.append(article)

    results["articles_new"] = len(new_articles)
    print(f"📰 New articles: {len(new_articles)}")

    if not new_articles:
        print("\n✅ No new signals to process")
        return results

    # 4. Pair each new article with each market that passes the safeguards.
    # Context is per market, not per article, so fetch it once.
    print(f"\n🔍 Checking {len(markets)} markets against {len(new_articles)} new articles...")
    tradeable = []
    for market_id in markets:
        print(f"\n   → Checking market: {market_id[:20]}...")
        context = get_market_context(market_id)
        if not context:
            print(f"     ⚠️ Could not fetch context")
            continue

        print(format_context_summary(context))
        passes, reasons = check_safeguards(context)
        if reasons:
            print(f"     ⚠️ Warnings: {'; '.join(reasons)}")
        if not passes:
            print(f"     ⏭️ Skipping: safeguards failed")
            results["pairs_skipped"] += len(new_articles)
            skip_reasons.append(f"safeguard: {reasons[0]}" if reasons else "safeguard")
            continue
        tradeable.append((market_id, context.get("market") or {}, reasons))

    now = datetime.now(timezone.utc).isoformat()
    for article in new_articles:
        h = article_hash(article["url"], article["title"])
        for market_id, market, reasons in tradeable:
            results["signals"].append({
                "article": {
                    "title": article["title"],
                    "url": article["url"],
                    "summary": article["summary"],
                    "published": article["published"],
                },
                "market": {
                    "id": market_id,
                    "question": market.get("question"),
                    "resolution_criteria": market.get("resolution_criteria"),
                    "current_price": market.get("current_price"),
                    "time_to_resolution": market.get("time_to_resolution"),
                },
                "warnings": reasons,
            })
        processed[h] = {
            "title": article["title"],
            "url": article["url"],
            "market_ids": [m for m, _, _ in tradeable],
            "action": "signal" if tradeable else "skipped",
            "processed_at": now,
        }

    # Save processed state
    save_processed(processed)

    # Summary
    print("\n" + "=" * 40)
    print("🎯 Scan Complete")
    print(f"   Articles found: {results['articles_found']}")
    print(f"   Matched keywords: {results['articles_matched']}")
    print(f"   New to process: {results['articles_new']}")
    print(f"   Signals for your agent: {len(results['signals'])}")
    print(f"   Skipped (safeguards): {results['pairs_skipped']}")

    if results["signals"]:
        print("\n📡 Signals for Analysis (no trades placed; your agent decides):")
        for signal in results["signals"]:
            print(f"   • {signal['article']['title'][:50]}...")
            print(f"     Market: {signal['market']['id']} — {(signal['market']['question'] or '')[:60]}")

    # Structured report for automaton
    if os.environ.get("AUTOMATON_MANAGED"):
        signals_count = len(results['signals'])
        report = {"signals": signals_count, "trades_attempted": 0, "trades_executed": 0, "amount_usd": 0.0}
        if signals_count > 0:
            report["skip_reason"] = "signals_only"
        elif skip_reasons:
            report["skip_reason"] = ", ".join(dict.fromkeys(skip_reasons))
        print(json.dumps({"automaton": report}))
        _automaton_reported = True

    return results


def main():
    parser = argparse.ArgumentParser(description="Signal Sniper - Surface news signals for your agent to judge")
    parser.add_argument("--json", action="store_true", help="Print signals as JSON on stdout (logs go to stderr)")
    parser.add_argument("--live", action="store_true", help=argparse.SUPPRESS)  # accepted for old cron lines; no effect
    parser.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)  # accepted for old cron lines; no effect
    parser.add_argument("--scan-only", action="store_true", help=argparse.SUPPRESS)  # accepted for old cron lines; no effect
    parser.add_argument("--config", action="store_true", help="Show configuration")
    parser.add_argument("--history", action="store_true", help="Show processed articles")
    parser.add_argument("--feed", type=str, help="Override RSS feed URL")
    parser.add_argument("--market", type=str, help="Override target market ID")
    parser.add_argument("--keywords", type=str, help="Override keywords (comma-separated)")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="Set config value (e.g., --set feeds=url1,url2 --set keywords=a,b)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only output on signals/errors")

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
            # Reload globals
            global FEEDS, MARKETS, KEYWORDS
            _config = load_config(CONFIG_SCHEMA, __file__, slug="polymarket-signal-sniper")
            FEEDS = _config["feeds"]
            MARKETS = _config["markets"]
            KEYWORDS = _config["keywords"]

    if args.config:
        show_config()
        return

    if args.history:
        show_history()
        return

    if args.live:
        print("ℹ️  --live has no effect: since 2.0.0 this skill never trades. "
              "Your agent reads the signals and places any trade itself.", file=sys.stderr)

    # Build config with overrides
    config = get_config()
    feeds = [args.feed] if args.feed else config["feeds"]
    markets = [args.market] if args.market else config["markets"]
    keywords = [k.strip().lower() for k in args.keywords.split(",")] if args.keywords else config["keywords"]

    if args.json:
        # Keep stdout clean for the agent: human-readable logs go to stderr.
        with contextlib.redirect_stdout(sys.stderr):
            results = run_scan(feeds=feeds, markets=markets, keywords=keywords)
        print(json.dumps({"signals": results.get("signals", []), "error": results.get("error")}))
    else:
        run_scan(feeds=feeds, markets=markets, keywords=keywords)

    # Fallback report for automaton if the strategy returned early (no signal)
    if os.environ.get("AUTOMATON_MANAGED") and not _automaton_reported:
        print(json.dumps({"automaton": {"signals": 0, "trades_attempted": 0, "trades_executed": 0, "skip_reason": "no_signal"}}))


if __name__ == "__main__":
    main()
