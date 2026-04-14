#!/usr/bin/env python3
"""
Simmer Nothing-Ever-Happens Trader

Buys NO on standalone non-sports yes/no Polymarket markets below a configured
price cap. Based on the "nothing-ever-happens" strategy by sterlingcrispin.

The thesis: on most standalone binary markets, nothing dramatic happens and
the event resolves NO. Markets often overprice YES, leaving cheap NO shares.

Usage:
    python nothing_ever_happens.py              # Scan only (dry run)
    python nothing_ever_happens.py --live       # Execute real trades
    python nothing_ever_happens.py --scan       # Print candidate markets
    python nothing_ever_happens.py --quiet      # Only output on trades/errors
    python nothing_ever_happens.py --config     # Show configuration
    python nothing_ever_happens.py --set max_bet_usd=10  # Update config
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

# Force line-buffered stdout for non-TTY environments (cron, Docker, OpenClaw)
sys.stdout.reconfigure(line_buffering=True)


# =============================================================================
# Configuration
# =============================================================================

from simmer_sdk.skill import load_config, update_config, get_config_path

CONFIG_SCHEMA = {
    "price_cap": {
        "env": "SIMMER_NEH_PRICE_CAP",
        "default": 0.10,
        "type": float,
        "help": "Max NO ask price to buy (e.g. 0.10 = buy NO at ≤10¢)",
    },
    "max_bet_usd": {
        "env": "SIMMER_NEH_MAX_BET_USD",
        "default": 5.0,
        "type": float,
        "help": "Max USDC per trade",
    },
    "max_trades_per_run": {
        "env": "SIMMER_NEH_MAX_TRADES_PER_RUN",
        "default": 3,
        "type": int,
        "help": "Max trades per run",
    },
    "daily_budget": {
        "env": "SIMMER_NEH_DAILY_BUDGET_USD",
        "default": 15.0,
        "type": float,
        "help": "Daily spend limit in USDC",
    },
    "min_liquidity": {
        "env": "SIMMER_NEH_MIN_LIQUIDITY",
        "default": 500.0,
        "type": float,
        "help": "Min market liquidity to consider (USDC)",
    },
    "min_volume_24h": {
        "env": "SIMMER_NEH_MIN_VOLUME_24H",
        "default": 100.0,
        "type": float,
        "help": "Min 24h volume to consider (USDC)",
    },
    "candidate_pages": {
        "env": "SIMMER_NEH_CANDIDATE_PAGES",
        "default": 20,
        "type": int,
        "help": "Number of Gamma API pages to scan for candidates",
    },
}

_config = load_config(CONFIG_SCHEMA, __file__, slug="polymarket-nothing-ever-happens")

PRICE_CAP = _config["price_cap"]
MAX_BET_USD = _config["max_bet_usd"]
_automaton_max = os.environ.get("AUTOMATON_MAX_BET")
if _automaton_max:
    MAX_BET_USD = min(MAX_BET_USD, float(_automaton_max))
MAX_TRADES_PER_RUN = _config["max_trades_per_run"]
DAILY_BUDGET = _config["daily_budget"]
MIN_LIQUIDITY = _config["min_liquidity"]
MIN_VOLUME_24H = _config["min_volume_24h"]
CANDIDATE_PAGES = _config["candidate_pages"]

TRADE_SOURCE = "sdk:nothing-ever-happens"
SKILL_SLUG = "polymarket-nothing-ever-happens"
_automaton_reported = False

# Categories that are excluded (sports markets)
SPORTS_CATEGORIES = {
    "sports", "nfl", "nba", "mlb", "nhl", "nascar", "ufc", "mma",
    "soccer", "football", "basketball", "baseball", "hockey", "tennis",
    "golf", "formula-1", "f1", "boxing", "wrestling", "esports",
    "olympics", "fifa", "epl", "college", "ncaa", "rugby", "cricket",
}


# =============================================================================
# SimmerClient singleton
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
            print("Get your API key from: simmer.markets/dashboard -> SDK tab")
            sys.exit(1)
        _client = SimmerClient(api_key=api_key, venue="polymarket", live=live)
    return _client


# =============================================================================
# Daily spend tracking
# =============================================================================

def _get_spend_path():
    return Path(__file__).parent / "daily_spend.json"


def _load_daily_spend():
    """Load today's spend. Resets if date != today (UTC)."""
    spend_path = _get_spend_path()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if spend_path.exists():
        try:
            with open(spend_path) as f:
                data = json.load(f)
            if data.get("date") == today:
                return data
        except (json.JSONDecodeError, IOError):
            pass
    return {"date": today, "spent": 0.0, "trades": 0}


def _save_daily_spend(spend_data):
    with open(_get_spend_path(), "w") as f:
        json.dump(spend_data, f, indent=2)


# =============================================================================
# Market discovery via Gamma API
# =============================================================================

def _extract_tag_slugs(tags) -> set:
    """Extract lowercase slug strings from a Gamma API tags list."""
    slugs = set()
    for t in (tags or []):
        if isinstance(t, dict):
            slug = (t.get("slug") or t.get("label") or "").lower()
        else:
            slug = str(t).lower()
        if slug:
            slugs.add(slug)
    return slugs


def _is_sports(tags, category: str = "") -> bool:
    """Return True if tags or category indicate a sports market/event."""
    if category.lower() in SPORTS_CATEGORIES:
        return True
    return bool(_extract_tag_slugs(tags) & SPORTS_CATEGORIES)


def _is_binary_yes_no(market: dict) -> bool:
    """Return True if this is a binary Yes/No market."""
    outcomes = market.get("outcomes") or []
    if len(outcomes) != 2:
        return False
    normalized = {str(o).strip().lower() for o in outcomes}
    return normalized == {"yes", "no"}


def fetch_candidate_markets(pages: int = 3) -> list:
    """
    Fetch standalone non-sports yes/no markets with NO price <= price cap.

    Standalone = events with exactly one market (not a grouped multi-outcome event).
    Sorted by NO price ascending (cheapest NO first = highest conviction from thesis).
    """
    try:
        from gamma_api import GammaClient
    except ImportError:
        # Fall back to path relative to this script
        script_dir = Path(__file__).parent
        sys.path.insert(0, str(script_dir.parent / "polymarket-ai-divergence"))
        try:
            from gamma_api import GammaClient
        except ImportError:
            print("Error: gamma_api.py not found. Copy from polymarket-ai-divergence skill.")
            sys.exit(1)

    gamma = GammaClient()
    candidates = []

    for page in range(pages):
        try:
            events = gamma.get_events(
                active=True,
                closed=False,
                limit=50,
                offset=page * 50,
                order="volume24hr",
                ascending=False,
            )
        except Exception as e:
            print(f"[warn] Gamma API error on page {page}: {e}")
            break
        if not events:
            break

        for event in events:
            markets = event.get("markets") or []

            # Standalone = single-market event
            if len(markets) != 1:
                continue

            market = markets[0]

            # Must be yes/no binary
            if not _is_binary_yes_no(market):
                continue

            # Skip sports — check both event-level tags and market-level category
            # (tags are on the event; category may be on the market)
            if _is_sports(event.get("tags"), event.get("category", "")):
                continue
            if _is_sports(market.get("tags"), market.get("category", "")):
                continue

            # Check NO price against cap and floor
            # Floor at 2% — markets below this are nearly resolved (YES ~98%+)
            # and Simmer's import guard will reject them as "price too extreme"
            no_price = market.get("no_price", 1.0)
            if no_price > PRICE_CAP or no_price < 0.02:
                continue

            # Liquidity / volume filters
            if (event.get("liquidity") or 0) < MIN_LIQUIDITY:
                continue
            if (event.get("volume_24h") or 0) < MIN_VOLUME_24H:
                continue

            candidates.append({
                "slug": event.get("slug", ""),
                "question": market.get("question", ""),
                "condition_id": market.get("condition_id", ""),
                "no_price": no_price,
                "yes_price": market.get("yes_price", 0.0),
                "liquidity": event.get("liquidity") or 0,
                "volume_24h": event.get("volume_24h") or 0,
                "end_date": market.get("end_date", ""),
                "category": market.get("category", ""),
            })

    # Sort by cheapest NO first
    candidates.sort(key=lambda m: m["no_price"])
    return candidates


# =============================================================================
# Simmer market import
# =============================================================================

def import_market(slug: str) -> tuple:
    """Import a Polymarket event slug to Simmer. Returns (market_id, error)."""
    url = f"https://polymarket.com/event/{slug}"
    try:
        result = get_client().import_market(url)
    except Exception as e:
        return None, str(e)

    if not result:
        return None, "No response from import endpoint"

    if result.get("error"):
        return None, result.get("error", "Unknown error")

    status = result.get("status")
    market_id = result.get("market_id")

    if status == "resolved":
        return None, "Market already resolved"

    if status in ("imported", "already_exists"):
        return market_id, None

    return None, f"Unexpected import status: {status}"


def get_market_context(market_id: str) -> dict | None:
    """Fetch market context (fees, safeguards) from Simmer."""
    try:
        return get_client().get_market_context(market_id)
    except Exception:
        return None


def get_positions() -> list:
    """Get current positions, filtered to polymarket venue."""
    try:
        client = get_client()
        positions = client.get_positions(venue="polymarket")
        from dataclasses import asdict
        return [asdict(p) for p in positions]
    except Exception:
        return []


# =============================================================================
# Trade execution
# =============================================================================

def execute_trade(market_id: str, amount: float, signal_data: dict | None = None) -> dict:
    """Buy NO on a market via Simmer SDK."""
    try:
        result = get_client().trade(
            market_id=market_id,
            side="no",
            amount=amount,
            source=TRADE_SOURCE,
            skill_slug=SKILL_SLUG,
            reasoning="nothing-ever-happens: buying NO on standalone market below price cap",
            signal_data=signal_data,
        )
        return {
            "success": result.success,
            "trade_id": result.trade_id,
            "shares_bought": result.shares_bought,
            "error": result.error,
            "simulated": result.simulated,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


# =============================================================================
# Scanner
# =============================================================================

def print_candidates(candidates: list) -> None:
    """Display candidate markets in a table."""
    if not candidates:
        print("No candidates found below price cap.")
        return

    print()
    print("Nothing-Ever-Happens Scanner")
    print("=" * 72)
    print(f"{'Market':<38} {'NO':>5} {'YES':>5} {'Liq':>8} {'Vol24h':>8}")
    print("-" * 72)

    for m in candidates[:20]:
        q = m["question"][:36]
        print(
            f"{q:<38} {m['no_price']:>4.2f}c {m['yes_price']:>4.2f}c "
            f"{m['liquidity']:>7.0f} {m['volume_24h']:>7.0f}"
        )

    print("-" * 72)
    print(f"Showing {min(len(candidates), 20)} of {len(candidates)} candidates (NO ≤ {PRICE_CAP:.0%})")
    print()


# =============================================================================
# Main trading loop
# =============================================================================

def run_trades(candidates: list, dry_run: bool = True, quiet: bool = False) -> tuple:
    """
    Import and buy NO on candidate markets.
    Returns (signals, attempted, executed, skip_reasons, total_usd, errors).
    """
    def log(msg, force=False):
        if not quiet or force:
            print(msg)

    signals = len(candidates)
    if not candidates:
        log("  No candidates below price cap.")
        return signals, 0, 0, [], 0.0, []

    daily_spend = _load_daily_spend()
    remaining_budget = DAILY_BUDGET - daily_spend["spent"]
    if remaining_budget <= 0:
        log(f"  Daily budget exhausted (${daily_spend['spent']:.2f}/${DAILY_BUDGET:.2f})", force=True)
        return signals, 0, 0, ["daily budget exhausted"], 0.0, []

    # Skip markets where we already hold a position
    positions = get_positions()
    held_market_ids = {
        p.get("market_id")
        for p in positions
        if (p.get("shares_no") or 0) > 0 or (p.get("shares_yes") or 0) > 0
    }

    attempted = 0
    executed = 0
    total_usd = 0.0
    skip_reasons = []
    execution_errors = []

    log(f"\n{'=' * 50}")
    log(f"  Nothing-Ever-Happens Trading")
    log(f"{'=' * 50}")

    for m in candidates:
        if executed >= MAX_TRADES_PER_RUN:
            break
        if remaining_budget < 0.50:
            log(f"  Budget remaining ${remaining_budget:.2f} < $0.50 — stopping")
            break

        slug = m["slug"]
        question = m["question"][:50]
        no_price = m["no_price"]

        # Import market to Simmer
        market_id, err = import_market(slug)
        if err or not market_id:
            log(f"  Import failed for {question[:40]}...: {err}")
            skip_reasons.append(f"import failed: {err}")
            continue

        # Skip if already holding
        if market_id in held_market_ids:
            log(f"  Already holding {question[:40]}...")
            skip_reasons.append("already holding")
            continue

        # Get context for fees and safeguards
        context = get_market_context(market_id)
        if not context:
            log(f"  Context fetch failed for {question[:40]}...")
            skip_reasons.append("context fetch failed")
            continue

        ctx_market = context.get("market", {})
        fee_rate_bps = ctx_market.get("fee_rate_bps", 0)
        fee_pct = fee_rate_bps / 10000

        # Only trade zero-fee markets (fee eats into the thin edge on cheap NO)
        if fee_pct > 0:
            log(f"  Skipping {question[:40]}... — {fee_rate_bps}bps fee")
            skip_reasons.append(f"fee {fee_rate_bps}bps")
            continue

        # Check flip-flop safeguard
        discipline = context.get("discipline", {})
        if discipline.get("warning_level") == "severe":
            log(f"  Skipping {question[:40]}... — flip-flop warning")
            skip_reasons.append("safeguard: flip-flop")
            continue

        position_size = min(MAX_BET_USD, remaining_budget)
        attempted += 1

        if dry_run:
            log(f"  [PAPER] BUY NO ${position_size:.2f} on {question}...")
            log(f"          NO price: {no_price:.3f} | Liquidity: ${m['liquidity']:.0f}")
            executed += 1
            total_usd += position_size
            continue

        log(f"  BUY NO ${position_size:.2f} on {question}...")
        _signal_data = {
            "no_price": round(no_price, 4),
            "strategy": "nothing-ever-happens",
            "liquidity": round(m["liquidity"], 2),
            "volume_24h": round(m["volume_24h"], 2),
        }
        result = execute_trade(market_id, position_size, signal_data=_signal_data)

        if result and result.get("success"):
            executed += 1
            total_usd += position_size
            shares = result.get("shares_bought") or 0
            simulated = result.get("simulated", False)
            prefix = "[PAPER] " if simulated else ""
            log(f"  {prefix}Bought {shares:.1f} NO shares", force=True)

            if not simulated:
                daily_spend["spent"] += position_size
                daily_spend["trades"] += 1
                _save_daily_spend(daily_spend)
                remaining_budget -= position_size
        else:
            error = (result.get("error") or "Unknown error") if result else "No response"
            log(f"  Trade failed: {error}", force=True)
            execution_errors.append(error[:120])

    log(f"\n  Signals: {signals} | Attempted: {attempted} | Executed: {executed}")
    if dry_run:
        log("  [PAPER MODE — use --live for real trades]")

    return signals, attempted, executed, skip_reasons, total_usd, execution_errors


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Nothing-Ever-Happens Trader")
    parser.add_argument("--live", action="store_true", help="Execute real trades (default is dry-run)")
    parser.add_argument("--scan", action="store_true", help="Print candidates and exit (no trades)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only output on trades/errors")
    parser.add_argument("--config", action="store_true", help="Show configuration")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="Set config value (e.g., --set price_cap=0.03)")
    args = parser.parse_args()

    # Handle --set
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
            print(f"Config updated: {updates}")
            print(f"Saved to: {get_config_path(__file__)}")

    if args.config:
        config_path = get_config_path(__file__)
        print("Nothing-Ever-Happens Configuration")
        print("=" * 40)
        for key, spec in CONFIG_SCHEMA.items():
            val = _config.get(key, spec.get("default"))
            print(f"  {key:<22} = {val}  # {spec.get('help', '')}")
        print(f"\nConfig file: {config_path}")
        print(f"Config exists: {'Yes' if config_path.exists() else 'No'}")
        return

    dry_run = not args.live
    get_client(live=not dry_run)  # Validate API key early

    # Redeem any winning positions
    try:
        redeemed = get_client().auto_redeem()
        for r in redeemed:
            if r.get("success"):
                print(f"  Redeemed {r['market_id'][:8]}... (NO)")
    except Exception:
        pass  # Non-critical

    if not args.quiet:
        print(f"Scanning Polymarket for NO opportunities (cap: {PRICE_CAP:.0%})...")

    candidates = fetch_candidate_markets(pages=CANDIDATE_PAGES)

    if args.scan or not args.quiet:
        print_candidates(candidates)

    if args.scan:
        return

    is_paper_venue = os.environ.get("TRADING_VENUE", "polymarket") == "sim"
    signals, attempted, executed, skip_reasons, total_usd, execution_errors = run_trades(
        candidates,
        dry_run=dry_run and not is_paper_venue,
        quiet=args.quiet,
    )

    if os.environ.get("AUTOMATON_MANAGED"):
        global _automaton_reported
        report = {
            "signals": signals,
            "trades_attempted": attempted,
            "trades_executed": executed,
            "amount_usd": round(total_usd, 2),
        }
        if signals > 0 and executed == 0 and skip_reasons:
            report["skip_reason"] = ", ".join(dict.fromkeys(skip_reasons))
        if execution_errors:
            report["execution_errors"] = execution_errors
        print(json.dumps({"automaton": report}))
        _automaton_reported = True


if __name__ == "__main__":
    main()

    if os.environ.get("AUTOMATON_MANAGED") and not _automaton_reported:
        print(json.dumps({"automaton": {"signals": 0, "trades_attempted": 0, "trades_executed": 0, "skip_reason": "no_candidates"}}))
