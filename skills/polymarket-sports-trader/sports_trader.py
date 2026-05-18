#!/usr/bin/env python3
"""
Simmer Polymarket Sports Trader Skill (Template tier).

Scans Polymarket sports markets, asks an LLM (OpenAI-compatible API — defaults
to OpenRouter) for a fair-value estimate, trades when the model's fair value
diverges from the current YES price by a configurable threshold.

This is a FRAMEWORK SKILL. The default strategy is generic LLM-driven divergence
trading — it has not been backtested against historical sports outcomes and
makes no edge claim. Read DISCLAIMER.md before using with real funds.

Usage:
    python sports_trader.py                 # Dry run (scan, show signals, no trades)
    python sports_trader.py --live          # Execute real trades
    python sports_trader.py --positions     # Show current sports positions
    python sports_trader.py --config        # Show config
    python sports_trader.py --set max_position_usd=10

Requires:
    pip install simmer-sdk
    SIMMER_API_KEY    — from simmer.markets/dashboard → SDK tab
    WALLET_PRIVATE_KEY — for external-wallet trading (managed wallets don't need this)
    LLM_API_KEY       — OpenRouter / Anthropic / OpenAI key for the fair-value model
"""

import argparse
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.stdout.reconfigure(line_buffering=True)

from simmer_sdk.skill import load_config, update_config, get_config_path

# =============================================================================
# Identity (source-tag every trade so the catalog can split P&L by skill)
# =============================================================================

TRADE_SOURCE = "sdk:sports-trader"
SKILL_SLUG = "polymarket-sports-trader"

# =============================================================================
# Configuration (config.json > env vars > defaults)
# =============================================================================

CONFIG_SCHEMA = {
    "min_volume_usd":      {"env": "SIMMER_SPORTS_MIN_VOLUME",      "default": 25000,  "type": float},
    "max_markets_per_run": {"env": "SIMMER_SPORTS_MAX_MARKETS",     "default": 8,      "type": int},
    "divergence_min":      {"env": "SIMMER_SPORTS_DIVERGENCE_MIN",  "default": 0.08,   "type": float},
    "max_position_usd":    {"env": "SIMMER_SPORTS_MAX_POSITION",    "default": 5.00,   "type": float},
    "min_position_usd":    {"env": "SIMMER_SPORTS_MIN_POSITION",    "default": 2.00,   "type": float},
    "sizing_pct":          {"env": "SIMMER_SPORTS_SIZING_PCT",      "default": 0.05,   "type": float},
    "max_trades_per_run":  {"env": "SIMMER_SPORTS_MAX_TRADES",      "default": 4,      "type": int},
    "extreme_price_floor": {"env": "SIMMER_SPORTS_PRICE_FLOOR",     "default": 0.05,   "type": float},
    "extreme_price_ceil":  {"env": "SIMMER_SPORTS_PRICE_CEIL",      "default": 0.95,   "type": float},
    "slippage_max_pct":    {"env": "SIMMER_SPORTS_SLIPPAGE_MAX",    "default": 0.05,   "type": float},
    "auto_import":         {"env": "SIMMER_SPORTS_AUTO_IMPORT",     "default": False,  "type": bool},
    "order_type":          {"env": "SIMMER_SPORTS_ORDER_TYPE",      "default": "GTC",  "type": str},
    "llm_base_url":        {"env": "SIMMER_SPORTS_LLM_BASE_URL",    "default": "https://openrouter.ai/api/v1", "type": str},
    "llm_model":           {"env": "SIMMER_SPORTS_LLM_MODEL",       "default": "anthropic/claude-haiku-4.5", "type": str},
    "llm_timeout_secs":    {"env": "SIMMER_SPORTS_LLM_TIMEOUT",     "default": 30,     "type": int},
}

_config = load_config(CONFIG_SCHEMA, __file__, slug=SKILL_SLUG)


def _g(key):
    return _config.get(key, CONFIG_SCHEMA[key]["default"])


# =============================================================================
# SDK client
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
            print("Error: SIMMER_API_KEY not set. Get from simmer.markets/dashboard → SDK tab.")
            sys.exit(1)
        venue = os.environ.get("TRADING_VENUE", "polymarket")
        _client = SimmerClient(api_key=api_key, venue=venue, live=live)
    return _client


# =============================================================================
# Market discovery
# =============================================================================

def scan_sports_markets():
    """
    Discover sports markets to evaluate.

    Strategy: pull Simmer's `get_markets` first (already-imported, fastest path
    to trade-readiness), filter to Polymarket sports markets. If `auto_import`
    is enabled, also pull `list_importable_markets(category='sports')` and
    import the top by volume.
    """
    client = get_client()
    candidates = []

    try:
        already = client.get_markets(status="active", import_source="polymarket", limit=100)
    except Exception as e:
        print(f"  scan: failed to fetch active markets — {e}")
        already = []

    # We don't have a category filter on get_markets; lean on the importable
    # endpoint to identify which already-imported markets are sports.
    sports_question_set = set()
    try:
        importable = client.list_importable_markets(
            category="sports",
            min_volume=_g("min_volume_usd"),
            limit=50,
        )
        sports_question_set = {(m.get("question") or "").strip().lower() for m in importable}
    except Exception as e:
        print(f"  scan: list_importable_markets failed — {e}")
        importable = []

    for m in already:
        q = (m.question or "").strip().lower()
        if q in sports_question_set:
            candidates.append(m)

    if _g("auto_import") and len(candidates) < _g("max_markets_per_run"):
        # Pull most-liquid sports markets we don't yet have, import them.
        have_questions = {(m.question or "").strip().lower() for m in already}
        for m in sorted(importable, key=lambda x: x.get("volume_24h", 0), reverse=True):
            q = (m.get("question") or "").strip().lower()
            if not q or q in have_questions:
                continue
            url = m.get("url")
            if not url:
                continue
            try:
                result = client.import_market(url)
            except Exception as e:
                print(f"  import: {q[:60]} — {e}")
                continue
            new_id = result.get("market_id") if isinstance(result, dict) else None
            if new_id:
                full = client.get_market_by_id(new_id)
                if full:
                    candidates.append(full)
            if len(candidates) >= _g("max_markets_per_run"):
                break

    return candidates[: _g("max_markets_per_run")]


# =============================================================================
# LLM fair-value signal (OpenAI-compatible HTTP, OpenRouter default)
# =============================================================================

FAIR_VALUE_PROMPT_TEMPLATE = """You are a sports trader pricing a binary YES/NO market on Polymarket.

Market: {question}
Resolves at: {resolves_at}
Current YES price: {yes_price:.4f} (probability the market resolves YES)

Estimate the fair YES probability based on public information you have. If you
are uncertain, return a probability closer to the current market price — do
not invent edge.

Respond with a single JSON object on one line, no prose:
{{"fair_yes": <float 0-1>, "confidence": <"low"|"medium"|"high">, "reasoning": <one-sentence string>}}
"""


def llm_fair_value(question, resolves_at, yes_price):
    """
    Ask an OpenAI-compatible LLM for a fair-value YES probability.

    Returns dict {fair_yes, confidence, reasoning} or None on failure.
    """
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        return None

    base_url = _g("llm_base_url").rstrip("/")
    prompt = FAIR_VALUE_PROMPT_TEMPLATE.format(
        question=question,
        resolves_at=resolves_at or "unknown",
        yes_price=yes_price,
    )
    payload = {
        "model": _g("llm_model"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 200,
    }
    body = json.dumps(payload).encode()
    req = Request(
        f"{base_url}/chat/completions",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
    )
    try:
        with urlopen(req, timeout=_g("llm_timeout_secs")) as resp:
            data = json.loads(resp.read().decode())
    except (HTTPError, URLError, json.JSONDecodeError, TimeoutError) as e:
        print(f"  llm: error — {e}")
        return None

    try:
        content = data["choices"][0]["message"]["content"].strip()
        # Some providers wrap with ```json blocks; extract first {...}
        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1:
            return None
        parsed = json.loads(content[start : end + 1])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None

    fair = parsed.get("fair_yes")
    if not isinstance(fair, (int, float)) or not (0.0 <= float(fair) <= 1.0):
        return None
    return {
        "fair_yes": float(fair),
        "confidence": parsed.get("confidence", "medium"),
        "reasoning": parsed.get("reasoning", ""),
    }


# =============================================================================
# Signal → trade decision
# =============================================================================

def evaluate_market(market):
    """
    Return a trade decision dict for one market, or None to skip.

    Decision dict: {side, amount, fair_yes, market_price, edge, reasoning}.
    """
    yes_price = market.external_price_yes
    if yes_price is None:
        yes_price = market.current_probability
    if yes_price is None:
        return {"skip": True, "reason": "no yes price available"}

    if yes_price <= _g("extreme_price_floor") or yes_price >= _g("extreme_price_ceil"):
        return {"skip": True, "reason": f"extreme price {yes_price:.3f}"}

    signal = llm_fair_value(market.question, market.resolves_at, yes_price)
    if signal is None:
        return {"skip": True, "reason": "llm signal unavailable"}

    fair = signal["fair_yes"]
    edge = fair - yes_price  # positive → underpriced YES; negative → overpriced YES

    if abs(edge) < _g("divergence_min"):
        return {"skip": True, "reason": f"edge {edge:+.3f} below threshold"}

    side = "yes" if edge > 0 else "no"
    return {
        "skip": False,
        "side": side,
        "fair_yes": fair,
        "market_price": yes_price,
        "edge": edge,
        "confidence": signal["confidence"],
        "reasoning": signal.get("reasoning", ""),
    }


# =============================================================================
# Portfolio + sizing
# =============================================================================

def get_portfolio():
    try:
        return get_client().get_portfolio()
    except Exception:
        return None


def calculate_position_size(smart_sizing):
    """Compute USD per trade. Smart sizing scales with balance; otherwise fixed."""
    if not smart_sizing:
        return _g("max_position_usd")
    portfolio = get_portfolio()
    if not portfolio:
        return _g("max_position_usd")
    balance = (portfolio.get("balance_usdc") or portfolio.get("balance") or 0.0) if isinstance(portfolio, dict) else 0.0
    sized = balance * _g("sizing_pct")
    return max(_g("min_position_usd"), min(_g("max_position_usd"), sized))


def execute_trade(market_id, side, amount, reasoning=None, signal_data=None):
    """Execute a buy trade with source tagging."""
    try:
        result = get_client().trade(
            market_id=market_id,
            side=side,
            amount=amount,
            source=TRADE_SOURCE,
            skill_slug=SKILL_SLUG,
            reasoning=reasoning,
            signal_data=signal_data,
            order_type=(_g("order_type") or "GTC").upper(),
        )
        return {
            "success": result.success,
            "trade_id": result.trade_id,
            "shares": result.shares_bought,
            "error": result.error,
            "simulated": result.simulated,
            "order_status": result.order_status,
        }
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# Main run loop
# =============================================================================

def run_strategy(dry_run=True, positions_only=False, show_config=False,
                 smart_sizing=False, quiet=False):
    if show_config:
        path = get_config_path(__file__)
        print(f"⚙️  Config ({path}):")
        for key, spec in CONFIG_SCHEMA.items():
            print(f"  {key:<22} {_config.get(key, spec['default'])}")
        return

    if positions_only:
        try:
            positions = get_client().get_positions(source=TRADE_SOURCE)
        except Exception as e:
            print(f"Error fetching positions: {e}")
            return
        if not positions:
            print("No sports-trader positions.")
            return
        print(f"📊 Open positions ({len(positions)}):")
        for p in positions:
            print(f"  {p.market_question[:60]:<62} {p.shares:>8.2f} shares @ {getattr(p, 'avg_price', 0):.3f}")
        return

    print("🏀 Simmer Sports Trader")
    print("=" * 50)
    if dry_run:
        print("(dry run — no orders placed. Use --live to execute.)")

    candidates = scan_sports_markets()
    if not candidates:
        print("No sports markets to evaluate. Try --set auto_import=true.")
        return

    print(f"📋 Evaluating {len(candidates)} market(s)…")
    trades_done = 0
    max_trades = _g("max_trades_per_run")

    for market in candidates:
        if trades_done >= max_trades:
            print(f"  reached max_trades_per_run={max_trades}, stopping")
            break
        decision = evaluate_market(market)
        question = (market.question or "")[:60]
        if decision.get("skip"):
            if not quiet:
                print(f"  - {question:<62} skip: {decision.get('reason')}")
            continue
        side = decision["side"]
        fair = decision["fair_yes"]
        price = decision["market_price"]
        edge = decision["edge"]
        amount = calculate_position_size(smart_sizing)
        reasoning = decision.get("reasoning") or ""
        print(f"  → {question:<62} {side.upper():>3} fair={fair:.3f} mkt={price:.3f} edge={edge:+.3f} confidence={decision['confidence']}")
        print(f"     ↳ {reasoning}")

        if dry_run:
            continue

        result = execute_trade(
            market_id=market.id,
            side=side,
            amount=amount,
            reasoning=reasoning,
            signal_data={"fair_yes": fair, "market_price": price, "edge": edge, "confidence": decision["confidence"]},
        )
        if result.get("error"):
            print(f"     ✗ trade error: {result['error']}")
            continue
        if result.get("success"):
            print(f"     ✓ bought {result.get('shares', 0):.2f} shares (trade {result.get('trade_id')})")
            trades_done += 1
        else:
            status = result.get("order_status")
            print(f"     ↺ order status: {status}")
            trades_done += 1

    print()
    print(f"📊 Summary: {len(candidates)} scanned · {trades_done} trades")


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--live", action="store_true", help="Execute real trades (default: dry run)")
    parser.add_argument("--positions", action="store_true", help="Show current positions")
    parser.add_argument("--config", action="store_true", help="Show config")
    parser.add_argument("--set", action="append", default=[], metavar="key=value", help="Update config (e.g. max_position_usd=10)")
    parser.add_argument("--smart-sizing", action="store_true", help="Size positions as a percentage of balance")
    parser.add_argument("--quiet", action="store_true", help="Only print on trades and errors")
    args = parser.parse_args()

    if args.set:
        updates = {}
        for item in args.set:
            if "=" not in item:
                print(f"--set expects key=value, got: {item}")
                sys.exit(1)
            key, value = item.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key in CONFIG_SCHEMA:
                type_fn = CONFIG_SCHEMA[key].get("type", str)
                try:
                    if type_fn is bool:
                        value = value.lower() in ("1", "true", "yes", "on")
                    else:
                        value = type_fn(value)
                except (ValueError, TypeError):
                    pass
            updates[key] = value
        if updates:
            update_config(updates, __file__)
            print(f"✅ Config updated: {updates}")
            print(f"   Saved to: {get_config_path(__file__)}")
        return

    run_strategy(
        dry_run=not args.live,
        positions_only=args.positions,
        show_config=args.config,
        smart_sizing=args.smart_sizing,
        quiet=args.quiet,
    )


if __name__ == "__main__":
    main()
