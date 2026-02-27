#!/usr/bin/env python3
"""
Signal Sniper - Trade on signals from user-configured sources.

Pattern A skill with SDK infrastructure:
- Skill handles: RSS polling, keyword matching, decision logic
- SDK provides: context endpoint (safeguards), trade endpoint

Usage:
    python signal_sniper.py                     # Dry run (show signals, no trades)
    python signal_sniper.py --live              # Execute real trades
    python signal_sniper.py --scan-only         # Just show matches
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
import fcntl
import re
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
TRADE_SOURCE = "sdk:signalsniper"
SKILL_SLUG = "polymarket-signal-sniper"
_automaton_reported = False

from simmer_sdk.skill import load_config, update_config, get_config_path

# Configuration schema
CONFIG_SCHEMA = {
    "feeds": {"env": "SIMMER_SNIPER_FEEDS", "default": "", "type": str},
    "markets": {"env": "SIMMER_SNIPER_MARKETS", "default": "", "type": str},
    "keywords": {"env": "SIMMER_SNIPER_KEYWORDS", "default": "", "type": str},
    "confidence_threshold": {"env": "SIMMER_SNIPER_CONFIDENCE", "default": 0.7, "type": float},
    "max_usd": {"env": "SIMMER_SNIPER_MAX_USD", "default": 25.0, "type": float},
    "max_trades_per_run": {"env": "SIMMER_SNIPER_MAX_TRADES", "default": 5, "type": int},
}

# Load configuration
_config = load_config(CONFIG_SCHEMA, __file__, slug="polymarket-signal-sniper")

# SimmerClient singleton
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
            print("Get your API key from: simmer.markets/dashboard -> SDK tab")
            sys.exit(1)
        venue = os.environ.get("TRADING_VENUE", "polymarket")
        _client = SimmerClient(api_key=api_key, venue=venue, live=live)
    return _client

# Sniper configuration - from config
FEEDS = _config["feeds"]
MARKETS = _config["markets"]
KEYWORDS = _config["keywords"]
CONFIDENCE_THRESHOLD = _config["confidence_threshold"]
MAX_USD = _config["max_usd"]
_automaton_max = os.environ.get("AUTOMATON_MAX_BET")
if _automaton_max:
    MAX_USD = min(MAX_USD, float(_automaton_max))
MAX_TRADES_PER_RUN = _config["max_trades_per_run"]

# Polymarket constraints
MIN_SHARES_PER_ORDER = 5.0  # Polymarket requires minimum 5 shares
MIN_TICK_SIZE = 0.01        # Minimum price increment

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
        "confidence_threshold": CONFIDENCE_THRESHOLD,
        "max_usd": MAX_USD,
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
    print(f"Confidence Threshold: {config['confidence_threshold']:.0%}")
    print(f"Max Trade Size: ${config['max_usd']:.2f}")
    print(f"Max Trades/Run: {MAX_TRADES_PER_RUN}")
    print()
    print(f"Config file: {config_path}")
    print(f"Config exists: {'Yes' if config_path.exists() else 'No'}")
    print("\nTo change settings:")
    print("  --set feeds=https://rss.example.com/feed1,https://rss.example.com/feed2")
    print("  --set keywords=bitcoin,ethereum,crypto")
    print("  --set confidence_threshold=0.8")


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


# Bearish keywords — if article contains these, lean toward selling YES / buying NO
# Uses word boundary matching via _count_keyword_hits() to avoid substring false positives
BEARISH_KEYWORDS = [
    "fail", "decline", "drop", "fall", "crash", "reject", "unlikely",
    "delay", "cancel", "oppose", "block", "veto", "lose", "defeat",
    "miss", "below", "under", "down", "negative", "bearish",
    "denied", "collapses", "plunges", "slumps",
]

# Bullish keywords — lean toward buying YES
BULLISH_KEYWORDS = [
    "pass", "approve", "rise", "gain", "surge", "confirm", "likely",
    "agree", "support", "win", "above", "over", "positive",
    "bullish", "succeed", "advance", "rally", "soars", "jumps",
    "launches", "announces", "breakthrough",
]


def _count_keyword_hits(text: str, keywords: List[str]) -> int:
    """Count keyword matches using word boundaries to avoid substring false positives."""
    count = 0
    for kw in keywords:
        # \b word boundary ensures "down" doesn't match "download"
        if re.search(r'\b' + re.escape(kw) + r'\b', text):
            count += 1
    return count


NEGATIVE_QUESTION_PATTERNS = [
    r'\bwill\b.*\b(fail|lose|drop|fall|crash|decline|collapse|miss|default|reject)\b',
    r'\bwill there be\b.*\b(recession|downturn|shutdown|crisis|war|conflict)\b',
    r'\bwill\b.*\b(below|under|less than|fewer than|lower than)\b',
    r'\bwill\b.*\bnot\b',
    r'\bwill\b.*\b(ban|block|veto|oppose|cancel|delay|deny)\b',
]


def _is_negative_question(question: str) -> bool:
    """Detect if a market question is negatively-phrased (YES = bad thing happens).

    For these markets, bearish article sentiment should map to YES (not NO),
    because the bad outcome the question asks about becomes more likely.
    """
    q = question.lower()
    return any(re.search(pat, q) for pat in NEGATIVE_QUESTION_PATTERNS)


def infer_side(article: Dict[str, str], market_question: str = "") -> Tuple[Optional[str], float]:
    """Infer trade side from article sentiment, aware of market question phrasing.

    Returns (side, confidence) where side is 'yes', 'no', or None.
    Confidence is 0-1 based on keyword hit strength.
    """
    text = f"{article['title']} {article['summary']}".lower()

    bull_score = _count_keyword_hits(text, BULLISH_KEYWORDS)
    bear_score = _count_keyword_hits(text, BEARISH_KEYWORDS)

    total = bull_score + bear_score
    if total == 0:
        return None, 0.0

    # Determine raw sentiment
    if bull_score > bear_score and bull_score >= 2:
        raw_side = "yes"
    elif bear_score > bull_score and bear_score >= 2:
        raw_side = "no"
    else:
        return None, 0.0

    # Confidence: how decisive the keyword balance is (0.5 = barely, 1.0 = all one way)
    confidence = abs(bull_score - bear_score) / total

    # Flip direction for negatively-phrased markets
    if market_question and _is_negative_question(market_question):
        raw_side = "no" if raw_side == "yes" else "yes"

    return raw_side, confidence


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


def set_risk_monitor(market_id: str, side: str,
                     stop_loss_pct: float = 0.20, take_profit_pct: float = 0.50) -> Dict:
    """
    Set stop-loss and take-profit for a position.
    The backend monitors every 15 min and auto-exits when thresholds hit.
    """
    try:
        return get_client().set_monitor(market_id, side,
                                        stop_loss_pct=stop_loss_pct,
                                        take_profit_pct=take_profit_pct)
    except Exception as e:
        return {"error": str(e)}


def execute_trade(
    market_id: str,
    side: str,
    amount: float,
    price: float = None,
    source: str = None,
    thesis: str = None,
    confidence: float = None,
) -> Dict:
    """Execute trade via SDK with 5-share minimum check and journal logging."""
    source = source or TRADE_SOURCE

    # Check Polymarket minimum shares requirement
    if price and price > 0:
        shares = amount / price
        if shares < MIN_SHARES_PER_ORDER:
            return {
                "success": False,
                "error": f"Position size ${amount:.2f} too small for {MIN_SHARES_PER_ORDER} shares at ${price:.2f} (would be {shares:.1f} shares)"
            }

    try:
        result = get_client().trade(
            market_id=market_id,
            side=side,
            amount=amount,
            source=source,
        )
        trade_result = {
            "success": result.success,
            "trade_id": result.trade_id,
            "shares_bought": result.shares_bought,
            "shares": result.shares_bought,
            "error": result.error,
        }
    except Exception as e:
        return {"error": str(e)}

    # Log to journal if successful
    if trade_result.get("success") and JOURNAL_AVAILABLE:
        trade_id = trade_result.get("trade_id")
        if trade_id:
            log_trade(
                trade_id=trade_id,
                source=source,
                thesis=thesis,
                confidence=confidence,
            )

    return trade_result


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
    dry_run: bool = True,
    scan_only: bool = False,
) -> Dict[str, Any]:
    """
    Run signal scan across feeds and markets.

    Returns summary of results.
    """
    if dry_run:
        print("\n  [PAPER MODE] Trades will be simulated with real prices. Use --live for real trades.\n")

    # Validate API key via client init
    try:
        get_client(live=not dry_run)
    except SystemExit:
        return {"error": "No API key"}

    if not feeds:
        print("❌ No RSS feeds configured")
        print("   Set SIMMER_SNIPER_FEEDS or use --feed URL")
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
    execution_errors = []
    results = {
        "feeds_scanned": len(feeds),
        "articles_found": 0,
        "articles_matched": 0,
        "articles_new": 0,
        "trades_executed": 0,
        "trades_skipped": 0,
        "total_usd_spent": 0.0,
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

    # 4. For each new article, check each market
    print(f"\n🔍 Analyzing {len(new_articles)} new articles against {len(markets)} markets...")

    for article in new_articles:
        h = article_hash(article["url"], article["title"])
        print(f"\n📰 {article['title'][:60]}...")

        for market_id in markets:
            print(f"\n   → Checking market: {market_id[:20]}...")

            # Get context with safeguards (no probability estimate — we don't
            # have a calibrated signal yet, so edge calculation would be misleading)
            context = get_market_context(market_id)
            if not context:
                print(f"     ⚠️ Could not fetch context")
                continue

            print(format_context_summary(context))

            # Check safeguards
            should_trade, reasons = check_safeguards(context)

            if reasons:
                print(f"     ⚠️ Warnings: {'; '.join(reasons)}")

            if not should_trade:
                print(f"     ⏭️ Skipping: safeguards failed")
                results["trades_skipped"] += 1
                skip_reasons.append(f"safeguard: {reasons[0]}" if reasons else "safeguard")
                processed[h] = {
                    "title": article["title"],
                    "url": article["url"],
                    "market_id": market_id,
                    "action": "skipped",
                    "reason": "; ".join(reasons),
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                }
                continue

            if scan_only:
                print(f"     👀 [SCAN ONLY] Would analyze for trading")
                results["signals"].append({
                    "article": article["title"],
                    "market_id": market_id,
                    "context": context.get("market", {}),
                })
                continue

            # Safeguards passed — infer trade direction from article sentiment
            market_question = context.get("market", {}).get("question", "")
            side, signal_confidence = infer_side(article, market_question)
            market_price = context.get("market", {}).get("current_price", 0.5)

            if not side:
                print(f"     🤷 Signal unclear — can't determine direction, skipping")
                skip_reasons.append("unclear signal")
                processed[h] = {
                    "title": article["title"],
                    "url": article["url"],
                    "market_id": market_id,
                    "action": "unclear_signal",
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                }
                continue

            # Gate on confidence threshold
            if signal_confidence < CONFIDENCE_THRESHOLD:
                print(f"     📉 Signal confidence {signal_confidence:.0%} below threshold {CONFIDENCE_THRESHOLD:.0%}, skipping")
                skip_reasons.append(f"low confidence ({signal_confidence:.0%})")
                processed[h] = {
                    "title": article["title"],
                    "url": article["url"],
                    "market_id": market_id,
                    "action": "low_confidence",
                    "confidence": signal_confidence,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                }
                continue

            print(f"\n     🧠 SIGNAL: {side.upper()} on {market_id[:20]} (confidence: {signal_confidence:.0%})")
            print(f"     Article: {article['title'][:60]}")
            print(f"     Market price: {market_price:.1%}")

            if dry_run:
                print(f"     🏜️ [DRY RUN] Would buy {side.upper()} for ${MAX_USD:.2f}")
                action = "dry_run"
            elif results["trades_executed"] >= MAX_TRADES_PER_RUN:
                print(f"     ⏭️ Max trades per run ({MAX_TRADES_PER_RUN}) reached")
                skip_reasons.append("max trades reached")
                action = "max_trades_reached"
            else:
                print(f"     💰 Executing: BUY {side.upper()} for ${MAX_USD:.2f}")
                trade_result = execute_trade(
                    market_id=market_id,
                    side=side,
                    amount=MAX_USD,
                    price=market_price,
                    source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
                    thesis=f"Signal: {article['title'][:100]}",
                    confidence=signal_confidence,
                )
                if trade_result.get("success"):
                    shares = trade_result.get("shares", 0)
                    print(f"     ✅ Bought {shares:.1f} shares")
                    results["trades_executed"] += 1
                    results["total_usd_spent"] += MAX_USD
                    action = "traded"
                else:
                    error = trade_result.get("error", "Unknown error")
                    print(f"     ❌ Trade failed: {error}")
                    execution_errors.append(error[:120])
                    action = "trade_failed"

            results["signals"].append({
                "article": article["title"],
                "url": article["url"],
                "market_id": market_id,
                "side": side,
                "action": action,
            })

            processed[h] = {
                "title": article["title"],
                "url": article["url"],
                "market_id": market_id,
                "side": side,
                "action": action,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            }

    # Save processed state
    save_processed(processed)

    # Summary
    print("\n" + "=" * 40)
    print("🎯 Scan Complete")
    print(f"   Articles found: {results['articles_found']}")
    print(f"   Matched keywords: {results['articles_matched']}")
    print(f"   New to process: {results['articles_new']}")
    print(f"   Signals detected: {len(results['signals'])}")
    print(f"   Skipped (safeguards): {results['trades_skipped']}")

    if results["signals"]:
        print("\n📡 Signals for Analysis:")
        for signal in results["signals"]:
            print(f"   • {signal['article'][:50]}...")
            print(f"     Market: {signal['market_id']}")

    # Structured report for automaton
    if os.environ.get("AUTOMATON_MANAGED"):
        global _automaton_reported
        signals_count = len(results['signals'])
        report = {"signals": signals_count, "trades_attempted": results['trades_executed'] + results['trades_skipped'], "trades_executed": results['trades_executed'], "amount_usd": round(results['total_usd_spent'], 2)}
        if signals_count > 0 and results['trades_executed'] == 0 and skip_reasons:
            report["skip_reason"] = ", ".join(dict.fromkeys(skip_reasons))
        if execution_errors:
            report["execution_errors"] = execution_errors
        print(json.dumps({"automaton": report}))
        _automaton_reported = True

    return results


def main():
    parser = argparse.ArgumentParser(description="Signal Sniper - Trade on user-configured signals")
    parser.add_argument("--live", action="store_true", help="Execute real trades (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="(Default) Don't execute trades")
    parser.add_argument("--scan-only", action="store_true", help="Only scan, don't analyze")
    parser.add_argument("--config", action="store_true", help="Show configuration")
    parser.add_argument("--history", action="store_true", help="Show processed articles")
    parser.add_argument("--feed", type=str, help="Override RSS feed URL")
    parser.add_argument("--market", type=str, help="Override target market ID")
    parser.add_argument("--keywords", type=str, help="Override keywords (comma-separated)")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="Set config value (e.g., --set feeds=url1,url2 --set keywords=a,b)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only output on trades/errors")

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
            global FEEDS, MARKETS, KEYWORDS, CONFIDENCE_THRESHOLD, MAX_USD, MAX_TRADES_PER_RUN
            _config = load_config(CONFIG_SCHEMA, __file__, slug="polymarket-signal-sniper")
            FEEDS = _config["feeds"]
            MARKETS = _config["markets"]
            KEYWORDS = _config["keywords"]
            CONFIDENCE_THRESHOLD = _config["confidence_threshold"]
            MAX_USD = _config["max_usd"]
            MAX_TRADES_PER_RUN = _config["max_trades_per_run"]

    if args.config:
        show_config()
        return

    if args.history:
        show_history()
        return

    # Build config with overrides
    config = get_config()
    feeds = [args.feed] if args.feed else config["feeds"]
    markets = [args.market] if args.market else config["markets"]
    keywords = [k.strip().lower() for k in args.keywords.split(",")] if args.keywords else config["keywords"]

    # Default to dry-run unless --live is explicitly passed
    dry_run = not args.live

    run_scan(
        feeds=feeds,
        markets=markets,
        keywords=keywords,
        dry_run=dry_run,
        scan_only=args.scan_only,
    )

    # Fallback report for automaton if the strategy returned early (no signal)
    if os.environ.get("AUTOMATON_MANAGED") and not _automaton_reported:
        print(json.dumps({"automaton": {"signals": 0, "trades_attempted": 0, "trades_executed": 0, "skip_reason": "no_signal"}}))


if __name__ == "__main__":
    main()
