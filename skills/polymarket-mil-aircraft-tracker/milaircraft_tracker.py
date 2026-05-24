#!/usr/bin/env python3
"""
Simmer Military Aircraft Tracker Skill.

Trades Polymarket strike/action markets using military aircraft ADS-B clusters
from pref.trade.

Usage:
    python milaircraft_tracker.py              # Dry run
    python milaircraft_tracker.py --live       # Execute real trades
    python milaircraft_tracker.py --positions  # Show positions
    python milaircraft_tracker.py --status     # Show cluster dashboard
    python milaircraft_tracker.py --check      # Check pref account status

Requires:
    SIMMER_API_KEY and PREF_API_KEY environment variables
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

sys.stdout.reconfigure(line_buffering=True)

from simmer_sdk.sizing import SIZING_CONFIG_SCHEMA, size_position
from simmer_sdk.skill import get_config_path, load_config, update_config

from pref_client import get_account_status, get_military_aircraft
from regions import filter_aircraft_by_regions, load_regions

SKILL_SLUG = "polymarket-mil-aircraft-tracker"
TRADE_SOURCE = "sdk:milaircraft"

CONFIG_SCHEMA = {
    "entry_threshold": {"env": "SIMMER_MILACFT_ENTRY_THRESHOLD", "default": 0.15, "type": float},
    "exit_threshold": {"env": "SIMMER_MILACFT_EXIT_THRESHOLD", "default": 0.45, "type": float},
    "trade_size": {"env": "SIMMER_MILACFT_TRADE_SIZE", "default": 5.00, "type": float},
    "cluster_cap": {"env": "SIMMER_MILACFT_CLUSTER_CAP", "default": 25.00, "type": float},
    "max_trades_per_run": {"env": "SIMMER_MILACFT_MAX_TRADES_PER_RUN", "default": 3, "type": int},
    "cadence_min": {"env": "SIMMER_MILACFT_CADENCE_MIN", "default": 15, "type": int},
    "daily_loss_kill": {"env": "SIMMER_MILACFT_DAILY_LOSS_KILL", "default": 25.00, "type": float},
    "daily_trade_kill": {"env": "SIMMER_MILACFT_DAILY_TRADE_KILL", "default": 10, "type": int},
    "slippage_max": {"env": "SIMMER_MILACFT_SLIPPAGE_MAX", "default": 0.15, "type": float},
    "order_type": {"env": "SIMMER_MILACFT_ORDER_TYPE", "default": "GTC", "type": str},
    **SIZING_CONFIG_SCHEMA,
}

_config = load_config(CONFIG_SCHEMA, __file__, slug=SKILL_SLUG)

ENTRY_THRESHOLD = _config["entry_threshold"]
EXIT_THRESHOLD = _config["exit_threshold"]
MAX_POSITION_USD = _config["trade_size"]
CLUSTER_CAP_USD = _config["cluster_cap"]
MAX_TRADES_PER_RUN = _config["max_trades_per_run"]
DAILY_LOSS_KILL = _config["daily_loss_kill"]
DAILY_TRADE_KILL = _config["daily_trade_kill"]
SLIPPAGE_MAX_PCT = _config["slippage_max"]
ORDER_TYPE = (_config["order_type"] or "GTC").upper()
POSITION_SIZING = _config["position_sizing"]
KELLY_MULTIPLIER = _config["kelly_multiplier"]
MIN_EV = _config["min_ev"]

MIN_SHARES_PER_ORDER = 5.0
MIN_TICK_SIZE = 0.01
ACTIVE_CLUSTER_P_WIN = 0.70
STATE_DIR = os.path.expanduser("~/.simmer/milaircraft-tracker")
STATE_FILE = os.path.join(STATE_DIR, "state.json")

_client = None


def get_client(live=True):
    """Lazy-init SimmerClient singleton."""
    global _client
    if _client is None:
        try:
            from simmer_sdk import SimmerClient
        except ImportError:
            print("Error: simmer-sdk>=0.13.0 not installed. Run: pip install --upgrade simmer-sdk")
            sys.exit(1)
        venue = os.environ.get("TRADING_VENUE", "polymarket")
        _client = SimmerClient.from_env(venue=venue, live=live)
    return _client


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def utc_today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def reset_daily_state_if_needed(state):
    today = utc_today()
    if state.get("day") != today:
        state["day"] = today
        state["daily_pnl"] = 0.0
        state["daily_trades"] = 0
    state.setdefault("open_positions", [])
    return state


def get_portfolio():
    try:
        return get_client().get_portfolio()
    except Exception as exc:
        print(f"  Portfolio fetch failed: {exc}")
        return None


def get_positions():
    try:
        client = get_client()
        positions = client.get_positions(venue=client.venue, source=TRADE_SOURCE)
        return [asdict(pos) if is_dataclass(pos) else pos for pos in positions]
    except Exception as exc:
        print(f"  Error fetching positions: {exc}")
        return []


def get_market_context(market_id):
    try:
        return get_client().get_market_context(market_id)
    except Exception:
        return None


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
    if warning_level == "mild":
        reasons.append("Mild flip-flop warning")

    estimates = slippage.get("estimates", []) if slippage else []
    if estimates:
        slippage_pct = estimates[0].get("slippage_pct", 0)
        if slippage_pct > SLIPPAGE_MAX_PCT:
            return False, [f"Slippage too high: {slippage_pct:.1%}"]

    return True, reasons


def execute_trade(market_id, side, amount, reasoning="", price=None, signal_data=None):
    try:
        kwargs = dict(
            market_id=market_id,
            side=side,
            amount=amount,
            source=TRADE_SOURCE,
            reasoning=reasoning,
            skill_slug=SKILL_SLUG,
            order_type=ORDER_TYPE,
            signal_data=signal_data,
        )
        if price is not None:
            kwargs["price"] = price
        result = get_client().trade(**kwargs)
        return {
            "success": result.success,
            "trade_id": result.trade_id,
            "shares_bought": result.shares_bought,
            "shares": result.shares_bought,
            "order_id": result.order_id,
            "fill_status": result.fill_status,
            "error": result.error,
            "skip_reason": getattr(result, "skip_reason", None),
            "simulated": result.simulated,
        }
    except Exception as exc:
        return {"error": str(exc)}


def execute_sell(market_id, side, shares, reasoning=""):
    try:
        result = get_client().trade(
            market_id=market_id,
            side=side,
            action="sell",
            shares=shares,
            source=TRADE_SOURCE,
            reasoning=reasoning,
            skill_slug=SKILL_SLUG,
            order_type=ORDER_TYPE,
        )
        return {
            "success": result.success,
            "trade_id": result.trade_id,
            "error": result.error,
            "skip_reason": getattr(result, "skip_reason", None),
            "simulated": result.simulated,
        }
    except Exception as exc:
        return {"error": str(exc)}


def market_price(market):
    for key in ("current_probability", "yes_price", "price", "last_price"):
        value = market.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass

    prices = market.get("outcome_prices")
    if isinstance(prices, list) and prices:
        try:
            return float(prices[0])
        except (TypeError, ValueError):
            return None
    return None


def market_question(market):
    return market.get("question") or market.get("title") or market.get("name") or ""


def is_strike_action_market(market, keywords):
    text = " ".join([
        market_question(market),
        market.get("event_name", ""),
        market.get("resolution_criteria", ""),
        market.get("description", ""),
    ]).lower()
    if not any(str(keyword).lower() in text for keyword in keywords):
        return False
    action_terms = (
        "strike",
        "attack",
        "military",
        "missile",
        "airstrike",
        "invasion",
        "bomb",
        "war",
        "conflict",
    )
    return any(term in text for term in action_terms)


def find_strike_markets(keywords):
    """Search active Polymarket strike/action markets by keyword list."""
    markets_by_id = {}
    client = get_client()

    for keyword in keywords:
        try:
            result = client._request(
                "GET",
                "/api/sdk/markets",
                params={
                    "q": keyword,
                    "status": "active",
                    "limit": 50,
                    "include": "resolution_criteria",
                },
            )
        except Exception as exc:
            print(f"  Market search failed for {keyword}: {exc}")
            continue

        for market in result.get("markets", []):
            market_id = str(market.get("id") or market.get("market_id") or "")
            if market_id and is_strike_action_market(market, keywords):
                markets_by_id[market_id] = market

    return list(markets_by_id.values())


def region_exposure_from_state(state, region_name):
    exposure = 0.0
    for pos in state.get("open_positions", []):
        if pos.get("region") == region_name:
            exposure += float(pos.get("size", 0) or 0)
    return exposure


def position_shares(pos):
    return float(pos.get("shares_yes") or pos.get("shares") or pos.get("shares_bought") or 0)


def handle_exits(positions, clusters, dry_run):
    exits = 0
    for pos in positions:
        market_id = pos.get("market_id")
        question = pos.get("question", "Unknown")
        current_price = pos.get("current_price")
        sources = pos.get("sources") or []

        if sources and TRADE_SOURCE not in sources and TRADE_SOURCE not in str(sources):
            continue

        reason = None
        if current_price is not None and float(current_price) >= EXIT_THRESHOLD:
            reason = f"exit threshold hit: price={float(current_price):.2f}"
        else:
            lowered = question.lower()
            for region_name, data in clusters.items():
                keywords = [kw.lower() for kw in data.get("keywords", [])]
                if any(keyword in lowered for keyword in keywords) and not data.get("fired"):
                    reason = f"cluster stale: {region_name} below threshold"
                    break

        if not reason:
            continue

        shares = position_shares(pos)
        if shares < MIN_SHARES_PER_ORDER:
            print(f"  Exit skipped, shares below minimum: {question[:60]}")
            continue

        print(f"  Exit signal: {question[:70]} ({reason})")
        if dry_run:
            exits += 1
            continue

        result = execute_sell(market_id, "yes", shares, reasoning=reason)
        if result.get("success"):
            exits += 1
        elif result.get("error"):
            print(f"  Sell failed: {result['error']}")
    return exits


def print_config():
    print("Configuration")
    print("-" * 50)
    for key, spec in CONFIG_SCHEMA.items():
        print(f"  {key:<24} {_config.get(key)!r:<12} env={spec.get('env', '')}")
    print(f"\nConfig file: {get_config_path(__file__)}")


def print_positions():
    positions = get_positions()
    print(f"Open {TRADE_SOURCE} positions: {len(positions)}")
    for pos in positions:
        question = pos.get("question", pos.get("market_id", "Unknown"))
        print(f"  {question[:72]}")
        print(
            f"    YES {float(pos.get('shares_yes') or 0):.2f} "
            f"price={pos.get('current_price', '?')} pnl={pos.get('pnl', '?')}"
        )


def check_pref_account():
    status = get_account_status()
    if not status:
        print("Pref account check failed. Confirm PREF_API_KEY is set and valid.")
        return 1
    print("Pref account status")
    print("-" * 50)
    for key in ("tier", "used", "limit", "quota_remaining", "reset_at", "agent_handle"):
        if key in status:
            print(f"  {key}: {status[key]}")
    if not any(key in status for key in ("tier", "used", "limit", "quota_remaining")):
        print(json.dumps(status, indent=2, sort_keys=True))
    return 0


def run_strategy(
    dry_run=True,
    positions_only=False,
    show_config=False,
    use_safeguards=True,
    status_only=False,
    quiet=False,
):
    """Run the trading strategy."""
    if status_only:
        from scripts.status import print_status

        print_status()
        return

    if show_config:
        print_config()
        return

    print("Military Aircraft Tracker")
    print("=" * 50)
    get_client(live=not dry_run)

    if dry_run:
        print("  [PAPER MODE] Use --live for real trades.")

    if positions_only:
        print_positions()
        return

    state = reset_daily_state_if_needed(load_state())
    state["last_tick_at"] = datetime.now(timezone.utc).isoformat()

    if float(state.get("daily_pnl", 0.0)) <= -abs(DAILY_LOSS_KILL):
        print(f"  Daily loss kill switch active: ${state.get('daily_pnl', 0.0):+.2f}")
        save_state(state)
        return
    if int(state.get("daily_trades", 0)) >= DAILY_TRADE_KILL:
        print(f"  Daily trade kill switch active: {state.get('daily_trades')} trades")
        save_state(state)
        return

    regions = load_regions()
    aircraft = get_military_aircraft()
    clusters = filter_aircraft_by_regions(aircraft, regions)

    if not quiet:
        print(f"  Aircraft fetched: {len(aircraft)}")
        print("  Cluster state:")
        for name, data in clusters.items():
            marker = "FIRED" if data["fired"] else "idle"
            print(f"    {name:<24} {data['count']}/{data['cluster_threshold']} {marker}")

    positions = get_positions()
    exits = handle_exits(positions, clusters, dry_run=dry_run)

    portfolio = get_portfolio() or {}
    bankroll = float(portfolio.get("balance_usdc") or portfolio.get("balance") or 0.0)
    if bankroll <= 0:
        bankroll = MAX_POSITION_USD * MAX_TRADES_PER_RUN

    trades_attempted = 0
    trades_executed = 0
    signals_found = 0
    skip_reasons = []
    execution_errors = []

    for region in regions:
        region_name = region["name"]
        cluster = clusters[region_name]
        if not cluster["fired"]:
            continue

        markets = find_strike_markets(cluster.get("keywords", []))
        if not markets:
            skip_reasons.append(f"no markets for {region_name}")
            continue

        for market in markets:
            if trades_executed >= MAX_TRADES_PER_RUN:
                skip_reasons.append("max trades reached")
                break

            market_id = str(market.get("id") or market.get("market_id") or "")
            price = market_price(market)
            question = market_question(market)
            if not market_id or price is None:
                skip_reasons.append("missing market id or price")
                continue
            if price < MIN_TICK_SIZE or price > (1 - MIN_TICK_SIZE):
                skip_reasons.append("price at extreme")
                continue
            if price >= ENTRY_THRESHOLD:
                continue

            current_region_exposure = region_exposure_from_state(state, region_name)
            if current_region_exposure >= CLUSTER_CAP_USD:
                skip_reasons.append(f"region cap reached: {region_name}")
                continue

            position_size = size_position(
                p_win=ACTIVE_CLUSTER_P_WIN,
                market_price=price,
                bankroll=bankroll,
                method=POSITION_SIZING,
                kelly_multiplier=KELLY_MULTIPLIER,
                min_ev=MIN_EV,
            )
            position_size = min(position_size, MAX_POSITION_USD, CLUSTER_CAP_USD - current_region_exposure)
            if position_size <= 0:
                skip_reasons.append("position sizing returned 0")
                continue
            if MIN_SHARES_PER_ORDER * price > position_size:
                skip_reasons.append("position too small")
                continue

            if use_safeguards:
                should_trade, reasons = check_context_safeguards(get_market_context(market_id))
                if not should_trade:
                    skip_reasons.append(f"safeguard: {reasons[0]}")
                    print(f"  Safeguard blocked: {'; '.join(reasons)}")
                    continue

            signals_found += 1
            reasoning = (
                f"{region_name} military aircraft cluster "
                f"{cluster['count']}/{cluster['cluster_threshold']}; p_win={ACTIVE_CLUSTER_P_WIN:.2f} "
                f"vs price={price:.2f}"
            )
            print(f"  Signal: {question[:80]}")
            print(f"    Region={region_name} price={price:.2f} size=${position_size:.2f}")

            if dry_run:
                continue

            trades_attempted += 1
            result = execute_trade(
                market_id,
                "yes",
                position_size,
                reasoning=reasoning,
                price=price,
                signal_data={
                    "region": region_name,
                    "cluster_count": cluster["count"],
                    "cluster_threshold": cluster["cluster_threshold"],
                    "signal_source": "pref.trade:aviation.get_adsb_military",
                },
            )
            if result.get("success"):
                trades_executed += 1
                state["daily_trades"] = int(state.get("daily_trades", 0)) + 1
                state.setdefault("open_positions", []).append({
                    "market_id": market_id,
                    "market": question,
                    "region": region_name,
                    "side": "YES",
                    "size": position_size,
                    "price": price,
                    "opened_at": datetime.now(timezone.utc).isoformat(),
                })
            elif result.get("skip_reason"):
                skip_reasons.append(result["skip_reason"])
            elif result.get("error"):
                execution_errors.append(result["error"])
                print(f"  Trade failed: {result['error']}")

    save_state(state)

    print("\nSummary")
    print("-" * 50)
    print(f"  Signals found: {signals_found}")
    print(f"  Exits signaled: {exits}")
    print(f"  Trades attempted: {trades_attempted}")
    print(f"  Trades executed: {trades_executed}")

    if os.environ.get("AUTOMATON_MANAGED"):
        report = {
            "signals": signals_found,
            "trades_attempted": trades_attempted,
            "trades_executed": trades_executed,
        }
        if skip_reasons:
            report["skip_reason"] = ", ".join(dict.fromkeys(skip_reasons))
        if execution_errors:
            report["execution_errors"] = execution_errors
        print(json.dumps({"automaton": report}))


def apply_config_updates(items):
    updates = {}
    for item in items:
        if "=" not in item:
            continue
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


def main():
    parser = argparse.ArgumentParser(description="Polymarket Military Aircraft Tracker")
    parser.add_argument("--live", action="store_true", help="Execute real trades")
    parser.add_argument("--dry-run", action="store_true", help="Dry run")
    parser.add_argument("--positions", action="store_true", help="Show positions only")
    parser.add_argument("--config", action="store_true", help="Show config")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE", help="Set config value")
    parser.add_argument("--status", action="store_true", help="Show terminal status dashboard")
    parser.add_argument("--check", action="store_true", help="Check pref account status")
    parser.add_argument("--no-safeguards", action="store_true", help="Disable safeguards")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only output on trades/errors")
    args = parser.parse_args()

    if args.set:
        apply_config_updates(args.set)
        return 0

    if args.check:
        return check_pref_account()

    run_strategy(
        dry_run=not args.live,
        positions_only=args.positions,
        show_config=args.config,
        use_safeguards=not args.no_safeguards,
        status_only=args.status,
        quiet=args.quiet,
    )

    if os.environ.get("AUTOMATON_MANAGED"):
        print(json.dumps({
            "automaton": {
                "signals": 0,
                "trades_attempted": 0,
                "trades_executed": 0,
                "skip_reason": "no_signal",
            }
        }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
