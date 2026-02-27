#!/usr/bin/env python3
"""
Simmer AI Divergence Trader

Finds markets where Simmer's AI consensus diverges from the real market price,
then trades on the mispriced side using Kelly sizing.

Usage:
    python ai_divergence.py              # Scan only (dry run)
    python ai_divergence.py --live       # Scan + execute trades
    python ai_divergence.py --min 10     # Only >10% divergence
    python ai_divergence.py --bullish    # AI more bullish than market
    python ai_divergence.py --bearish    # AI more bearish than market
    python ai_divergence.py --json       # Machine-readable output
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

# Force line-buffered stdout so output is visible in non-TTY environments (cron, Docker, OpenClaw)
sys.stdout.reconfigure(line_buffering=True)


# =============================================================================
# Configuration
# =============================================================================

from simmer_sdk.skill import load_config, update_config, get_config_path

CONFIG_SCHEMA = {
    "min_divergence": {"env": "SIMMER_DIVERGENCE_MIN", "default": 5.0, "type": float},
    "default_direction": {"env": "SIMMER_DIVERGENCE_DIRECTION", "default": "", "type": str},
    "max_bet_usd": {"env": "SIMMER_DIVERGENCE_MAX_BET", "default": 5.0, "type": float},
    "max_trades_per_run": {"env": "SIMMER_DIVERGENCE_MAX_TRADES", "default": 3, "type": int},
    "min_edge": {"env": "SIMMER_DIVERGENCE_MIN_EDGE", "default": 0.02, "type": float},
    "kelly_cap": {"env": "SIMMER_DIVERGENCE_KELLY_CAP", "default": 0.25, "type": float},
    "daily_budget": {"env": "SIMMER_DIVERGENCE_DAILY_BUDGET", "default": 25.0, "type": float},
}

_config = load_config(CONFIG_SCHEMA, __file__, slug="polymarket-ai-divergence")

DEFAULT_MIN_DIVERGENCE = _config["min_divergence"]
DEFAULT_DIRECTION = _config["default_direction"]
MAX_BET_USD = _config["max_bet_usd"]
_automaton_max = os.environ.get("AUTOMATON_MAX_BET")
if _automaton_max:
    MAX_BET_USD = min(MAX_BET_USD, float(_automaton_max))
MAX_TRADES_PER_RUN = _config["max_trades_per_run"]
MIN_EDGE = _config["min_edge"]
KELLY_CAP = _config["kelly_cap"]
DAILY_BUDGET = _config["daily_budget"]

TRADE_SOURCE = "sdk:divergence"
SKILL_SLUG = "polymarket-ai-divergence"
_automaton_reported = False
MIN_SHARES_PER_ORDER = 5.0


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
        venue = os.environ.get("TRADING_VENUE", "polymarket")
        _client = SimmerClient(api_key=api_key, venue=venue, live=live)
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
# Trading helpers
# =============================================================================

def execute_trade(market_id, side, amount):
    """Execute a buy trade via Simmer SDK with source tagging."""
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
        return {"success": False, "error": str(e)}


def get_positions():
    """Get current positions as list of dicts."""
    try:
        positions = get_client().get_positions()
        from dataclasses import asdict
        return [asdict(p) for p in positions]
    except Exception:
        return []


def get_market_context(market_id):
    """Fetch market context (includes fee_rate_bps and safeguards)."""
    try:
        return get_client()._request("GET", f"/api/sdk/context/{market_id}")
    except Exception:
        return None


def calculate_kelly_size(edge, price, max_bet, kelly_cap):
    """Kelly criterion position sizing.

    kelly_fraction = edge / (1 - price) for YES side
    Capped at kelly_cap fraction of max_bet.
    """
    if price <= 0 or price >= 1:
        return 0
    kelly = edge / (1 - price)
    kelly = max(0, min(kelly, kelly_cap))
    return round(kelly * max_bet, 2)


# =============================================================================
# Market data
# =============================================================================

def get_markets() -> list:
    """Fetch markets with meaningful divergence data.

    Uses /opportunities endpoint which filters out tracking-only markets
    (no AI cycles) where divergence is just LMSR drift noise.
    """
    data = get_client()._request("GET", "/api/sdk/markets/opportunities", params={"limit": 50, "min_divergence": 0.01})
    return [
        {
            "id": m.get("id"),
            "question": m.get("question"),
            "current_probability": m.get("current_probability"),
            "external_price_yes": m.get("external_price_yes"),
            "divergence": m.get("divergence"),
            "import_source": m.get("import_source"),
            "resolves_at": m.get("resolves_at"),
            "opportunity_score": m.get("opportunity_score"),
            "recommended_side": m.get("recommended_side"),
        }
        for m in data.get("opportunities", [])
    ]


# =============================================================================
# Scanner display
# =============================================================================

def format_divergence(markets: list, min_div: float = 0, direction: str = None) -> None:
    """Display divergence table."""
    filtered = []
    for m in markets:
        div = m.get("divergence") or 0
        if abs(div) < min_div / 100:
            continue
        if direction == "bullish" and div <= 0:
            continue
        if direction == "bearish" and div >= 0:
            continue
        filtered.append(m)

    filtered.sort(key=lambda m: abs(m.get("divergence") or 0), reverse=True)

    if not filtered:
        print("No markets match your filters.")
        return

    print()
    print("🔮 AI Divergence Scanner")
    print("=" * 75)
    print(f"{'Market':<40} {'Simmer':>8} {'Poly':>8} {'Div':>8} {'Signal':>8}")
    print("-" * 75)

    for m in filtered[:20]:
        q = m.get("question", "")[:38]
        simmer = m.get("current_probability") or 0
        poly = m.get("external_price_yes") or 0
        div = m.get("divergence") or 0

        is_polymarket = m.get("import_source") in ("polymarket", "kalshi")
        if div > 0.05:
            signal = "🟡 AI>MKT" if is_polymarket else "🟢 BUY"
        elif div < -0.05:
            signal = "🟡 AI<MKT" if is_polymarket else "🔴 SELL"
        else:
            signal = "⚪ HOLD"

        print(f"{q:<40} {simmer:>7.1%} {poly:>7.1%} {div:>+7.1%} {signal:>8}")

    print("-" * 75)
    print(f"Showing {len(filtered[:20])} of {len(filtered)} markets with divergence")
    print()

    bullish = len([m for m in filtered if (m.get("divergence") or 0) > 0])
    bearish = len([m for m in filtered if (m.get("divergence") or 0) < 0])
    avg_div = sum(abs(m.get("divergence") or 0) for m in filtered) / len(filtered) if filtered else 0

    print(f"📊 Summary: {bullish} bullish, {bearish} bearish, avg divergence {avg_div:.1%}")


def show_opportunities(markets: list) -> None:
    """Show actionable high-conviction opportunities."""
    print()
    print("💡 Top Opportunities (>10% divergence)")
    print("=" * 75)

    opps = [m for m in markets if abs(m.get("divergence") or 0) > 0.10]
    opps.sort(key=lambda m: abs(m.get("divergence") or 0), reverse=True)

    if not opps:
        print("No high-divergence opportunities right now.")
        return

    for m in opps[:5]:
        q = m.get("question", "")
        simmer = m.get("current_probability") or 0
        poly = m.get("external_price_yes") or 0
        div = m.get("divergence") or 0
        resolves = m.get("resolves_at", "Unknown")

        is_external = m.get("import_source") in ("polymarket", "kalshi")
        venue_name = "Kalshi" if m.get("import_source") == "kalshi" else "Polymarket"
        if is_external:
            action = f"Simmer AI: {simmer:.0%} vs {venue_name}: {poly:.0%} — do your own research before trading"
        elif div > 0:
            action = f"AI says BUY YES (AI: {simmer:.0%} vs Market: {poly:.0%})"
        else:
            action = f"AI says BUY NO (AI: {simmer:.0%} vs Market: {poly:.0%})"

        print(f"\n📌 {q[:70]}")
        print(f"   {action}")
        print(f"   Divergence: {div:+.1%} | Resolves: {resolves[:10] if resolves else 'TBD'}")


# =============================================================================
# Trade execution
# =============================================================================

def run_divergence_trades(markets, dry_run=True, quiet=False):
    """Scan for divergence opportunities and execute trades.

    Returns (signals_found, trades_attempted, trades_executed).
    """
    def log(msg, force=False):
        if not quiet or force:
            print(msg)

    # Filter to tradeable candidates
    candidates = [
        m for m in markets
        if m.get("id") and abs(m.get("divergence") or 0) >= MIN_EDGE
    ]
    candidates.sort(key=lambda m: abs(m.get("divergence") or 0), reverse=True)

    signals_found = len(candidates)
    skip_reasons = []
    execution_errors = []
    if not candidates:
        log("  No markets above min edge threshold")
        return signals_found, 0, 0, skip_reasons, 0.0, []

    # Load daily spend
    daily_spend = _load_daily_spend()
    remaining_budget = DAILY_BUDGET - daily_spend["spent"]
    if remaining_budget <= 0:
        log(f"  Daily budget exhausted (${daily_spend['spent']:.2f}/${DAILY_BUDGET:.2f})", force=True)
        skip_reasons.append("daily budget exhausted")
        return signals_found, 0, 0, skip_reasons, 0.0, []

    # Get existing positions to avoid doubling up
    positions = get_positions()
    held_market_ids = {p.get("market_id") for p in positions if (p.get("shares_yes") or 0) > 0 or (p.get("shares_no") or 0) > 0}

    trades_attempted = 0
    trades_executed = 0
    total_usd_spent = 0.0

    log(f"\n{'=' * 50}")
    log(f"  🎯 Divergence Trading")
    log(f"{'=' * 50}")

    for m in candidates[:MAX_TRADES_PER_RUN * 2]:  # Check extra in case some get filtered
        if trades_executed >= MAX_TRADES_PER_RUN:
            break
        if remaining_budget < 0.50:
            log(f"  Budget remaining ${remaining_budget:.2f} < $0.50 — stopping")
            break

        market_id = m["id"]
        div = m.get("divergence") or 0
        question = m.get("question", "Unknown")[:50]

        # Skip if already holding
        if market_id in held_market_ids:
            log(f"  ⏭️  {question}... — already holding position")
            skip_reasons.append("already holding")
            continue

        # Fetch context for fee rate + safeguards
        context = get_market_context(market_id)
        if not context:
            log(f"  ⏭️  {question}... — context fetch failed")
            continue

        ctx_market = context.get("market", {})
        fee_rate_bps = ctx_market.get("fee_rate_bps", 0)
        if fee_rate_bps > 0:
            log(f"  ⏭️  {question}... — fee {fee_rate_bps}bps, skipping")
            skip_reasons.append("fee market")
            continue

        # Check flip-flop safeguard
        discipline = context.get("discipline", {})
        warning_level = discipline.get("warning_level", "none")
        if warning_level == "severe":
            log(f"  ⏭️  {question}... — flip-flop warning (severe)")
            skip_reasons.append("safeguard: flip-flop severe")
            continue

        # Determine side and price
        side = "yes" if div > 0 else "no"
        edge = abs(div)
        # Price we're buying at (external price for the side we're trading)
        if side == "yes":
            price = m.get("external_price_yes") or 0.5
        else:
            price = 1 - (m.get("external_price_yes") or 0.5)

        # Kelly sizing
        position_size = calculate_kelly_size(edge, price, MAX_BET_USD, KELLY_CAP)
        if position_size < 0.50:
            log(f"  ⏭️  {question}... — Kelly size ${position_size:.2f} too small")
            skip_reasons.append("position too small")
            continue

        # Cap to remaining budget
        position_size = min(position_size, remaining_budget)

        trades_attempted += 1

        if dry_run:
            log(f"  🔒 [PAPER] {side.upper()} ${position_size:.2f} on {question}...")
            log(f"     Edge: {edge:.1%} | Price: ${price:.3f} | Kelly: ${position_size:.2f}")
            continue

        # Execute trade
        log(f"  🎯 Trading {side.upper()} ${position_size:.2f} on {question}...")
        log(f"     Edge: {edge:.1%} | Price: ${price:.3f}")
        result = execute_trade(market_id, side, position_size)

        if result and result.get("success"):
            trades_executed += 1
            total_usd_spent += position_size
            shares = result.get("shares_bought") or result.get("shares") or 0
            simulated = result.get("simulated", False)
            prefix = "[PAPER] " if simulated else ""
            log(f"  ✅ {prefix}Bought {shares:.1f} {side.upper()} shares", force=True)

            if not simulated:
                daily_spend["spent"] += position_size
                daily_spend["trades"] += 1
                _save_daily_spend(daily_spend)
                remaining_budget -= position_size
        else:
            error = result.get("error", "Unknown error") if result else "No response"
            log(f"  ❌ Trade failed: {error}", force=True)
            execution_errors.append(error[:120])

    log(f"\n  Signals: {signals_found} | Attempted: {trades_attempted} | Executed: {trades_executed}")
    if dry_run:
        log("  [PAPER MODE — use --live for real trades]")

    return signals_found, trades_attempted, trades_executed, skip_reasons, total_usd_spent, execution_errors


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Simmer AI Divergence Trader")
    parser.add_argument("--live", action="store_true", help="Execute real trades (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="(Default) Show opportunities without trading")
    parser.add_argument("--min", type=float, default=DEFAULT_MIN_DIVERGENCE,
                        help=f"Minimum divergence %% for scanner (default: {DEFAULT_MIN_DIVERGENCE})")
    parser.add_argument("--bullish", action="store_true", help="Only bullish divergence (Simmer > Poly)")
    parser.add_argument("--bearish", action="store_true", help="Only bearish divergence (Simmer < Poly)")
    parser.add_argument("--opportunities", "-o", action="store_true", help="Show top opportunities only")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--config", action="store_true", help="Show configuration")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only output on trades/errors")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="Set config value (e.g., --set min_edge=0.03)")
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
            update_config(updates, __file__)
            print(f"✅ Config updated: {updates}")
            print(f"   Saved to: {get_config_path(__file__)}")

    # Show config
    if args.config:
        config_path = get_config_path(__file__)
        print("🔮 AI Divergence Trader Configuration")
        print("=" * 40)
        for key, spec in CONFIG_SCHEMA.items():
            val = _config.get(key, spec.get("default"))
            print(f"  {key:<22} = {val}")
        print(f"\nConfig file: {config_path}")
        print(f"Config exists: {'Yes' if config_path.exists() else 'No'}")
        return

    # Validate API key by initializing client
    dry_run = not args.live
    get_client(live=not dry_run)

    direction = DEFAULT_DIRECTION or None
    if args.bullish:
        direction = "bullish"
    elif args.bearish:
        direction = "bearish"

    markets = get_markets()
    markets = [m for m in markets if m.get('is_live_now', True) is not False]  # skip not-yet-open markets (no-op if field absent)

    if args.json:
        filtered = [m for m in markets if abs(m.get("divergence") or 0) >= args.min / 100]
        filtered.sort(key=lambda m: abs(m.get("divergence") or 0), reverse=True)
        print(json.dumps(filtered, indent=2))
        return

    # Scanner display
    if not args.quiet:
        if args.opportunities:
            show_opportunities(markets)
        else:
            format_divergence(markets, args.min, direction)
            show_opportunities(markets)

    # Trade execution
    skip_reasons = []
    is_paper_venue = os.environ.get("TRADING_VENUE", "polymarket") == "simmer"
    if args.live or is_paper_venue:
        effective_dry_run = dry_run and not is_paper_venue
        signals, attempted, executed, skip_reasons, total_usd_spent, execution_errors = run_divergence_trades(markets, dry_run=effective_dry_run, quiet=args.quiet)
    else:
        signals = len([m for m in markets if abs(m.get("divergence") or 0) >= MIN_EDGE])
        attempted = 0
        executed = 0
        total_usd_spent = 0.0
        execution_errors = []

    # Structured report for automaton
    if os.environ.get("AUTOMATON_MANAGED"):
        global _automaton_reported
        report = {"signals": signals, "trades_attempted": attempted, "trades_executed": executed, "amount_usd": round(total_usd_spent, 2)}
        if signals > 0 and executed == 0 and skip_reasons:
            report["skip_reason"] = ", ".join(dict.fromkeys(skip_reasons))
        if execution_errors:
            report["execution_errors"] = execution_errors
        print(json.dumps({"automaton": report}))
        _automaton_reported = True


if __name__ == "__main__":
    main()

    # Fallback report for automaton if main() returned early (no signal)
    if os.environ.get("AUTOMATON_MANAGED") and not _automaton_reported:
        print(json.dumps({"automaton": {"signals": 0, "trades_attempted": 0, "trades_executed": 0, "skip_reason": "no_signal"}}))
