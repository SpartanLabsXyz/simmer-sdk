# Simmer Skill Template

Every generated skill follows this structure. Copy-paste the boilerplate blocks verbatim and customize the strategy logic.

## Directory Layout

```
<skill-slug>/
├── SKILL.md          # Metadata + documentation
├── <script>.py       # Main trading script
├── config.json       # User overrides (auto-created by --set)
└── scripts/
    └── status.py     # Portfolio viewer (copy from template)
```

## SKILL.md Frontmatter

```yaml
---
name: <skill-slug>
displayName: <Human Readable Name>
description: <What it does + when to trigger>
metadata: {"clawdbot":{"emoji":"<emoji>","requires":{"env":["SIMMER_API_KEY"],"pip":["simmer-sdk"]},"cron":null,"autostart":false,"automaton":{"managed":true,"entrypoint":"<script>.py"}}}
authors:
  - <author>
version: "1.0.0"
published: true
---
```

Always include `automaton.managed: true` and `entrypoint` pointing to the main script.

## Script Structure

### 1. Header

```python
#!/usr/bin/env python3
"""
<Skill Name> - <Tagline>

Usage:
    python <script>.py              # Dry run
    python <script>.py --live       # Real trades
    python <script>.py --positions  # Show positions

Requires:
    SIMMER_API_KEY environment variable
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError

# Force line-buffered stdout (required for cron/Docker/OpenClaw visibility)
sys.stdout.reconfigure(line_buffering=True)
```

### 2. Config System

```python
from simmer_sdk.skill import load_config, update_config, get_config_path

SKILL_SLUG = "your-skill-slug"  # Must match skills_registry slug

CONFIG_SCHEMA = {
    "param_name": {"env": "SIMMER_SKILLNAME_PARAM", "default": 0.10, "type": float},
    "max_position_usd": {"env": "SIMMER_SKILLNAME_MAX_POSITION", "default": 5.00, "type": float},
    "max_trades_per_run": {"env": "SIMMER_SKILLNAME_MAX_TRADES", "default": 5, "type": int},
    "sizing_pct": {"env": "SIMMER_SKILLNAME_SIZING_PCT", "default": 0.05, "type": float},
}

_config = load_config(CONFIG_SCHEMA, __file__, slug=SKILL_SLUG)
```

Config priority: `config.json > automaton tuning > env vars > defaults`.

When `slug` is provided, `load_config` automatically fetches tuned config from the Simmer Automaton (if the user has one running). This is transparent — skills don't need to know about the automaton.

Env var naming convention: `SIMMER_<SKILLNAME>_<PARAM>`.

### 4. SimmerClient Singleton (copy verbatim)

```python
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
```

Import is deferred inside the function so `--config` and `--set` modes work without an API key. **Warning:** `live=` only takes effect on the first call. Never call `get_client()` at module level — always call it inside `run_strategy()` first.

### 5. Constants

```python
TRADE_SOURCE = "sdk:<skillname>"  # REQUIRED: unique tag for this skill

# Polymarket constraints
MIN_SHARES_PER_ORDER = 5.0
MIN_TICK_SIZE = 0.01

# Safeguard threshold
SLIPPAGE_MAX_PCT = 0.15

# Unpack config to module-level
MAX_POSITION_USD = _config["max_position_usd"]
MAX_TRADES_PER_RUN = _config["max_trades_per_run"]
SMART_SIZING_PCT = _config["sizing_pct"]
```

### 6. SDK Wrappers (copy verbatim, customize execute_trade if needed)

```python
def get_portfolio():
    try:
        return get_client().get_portfolio()
    except Exception as e:
        print(f"  Portfolio fetch failed: {e}")
        return None

def get_positions():
    try:
        positions = get_client().get_positions()
        from dataclasses import asdict
        return [asdict(p) for p in positions]
    except Exception as e:
        print(f"  Error fetching positions: {e}")
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
    elif warning_level == "mild":
        reasons.append("Mild flip-flop warning (proceed with caution)")

    estimates = slippage.get("estimates", []) if slippage else []
    if estimates:
        slippage_pct = estimates[0].get("slippage_pct", 0)
        if slippage_pct > SLIPPAGE_MAX_PCT:
            return False, [f"Slippage too high: {slippage_pct:.1%}"]

    return True, reasons

def execute_trade(market_id, side, amount, reasoning=""):
    try:
        result = get_client().trade(
            market_id=market_id, side=side, amount=amount,
            source=TRADE_SOURCE, reasoning=reasoning,
        )
        return {
            "success": result.success, "trade_id": result.trade_id,
            "shares_bought": result.shares_bought, "shares": result.shares_bought,
            "error": result.error, "simulated": result.simulated,
        }
    except Exception as e:
        return {"error": str(e)}

def execute_sell(market_id, side, shares, reasoning=""):
    """Sell shares. Requires shares >= 5 (Polymarket minimum)."""
    try:
        result = get_client().trade(
            market_id=market_id, side=side, action="sell",
            shares=shares, source=TRADE_SOURCE, reasoning=reasoning,
        )
        return {
            "success": result.success, "trade_id": result.trade_id,
            "error": result.error, "simulated": result.simulated,
        }
    except Exception as e:
        return {"error": str(e)}

def calculate_position_size(default_size, smart_sizing):
    if not smart_sizing:
        return default_size
    portfolio = get_portfolio()
    if not portfolio:
        return default_size
    balance = portfolio.get("balance_usdc", 0)
    if balance <= 0:
        return default_size
    smart_size = balance * SMART_SIZING_PCT
    smart_size = min(smart_size, MAX_POSITION_USD)
    smart_size = max(smart_size, 1.0)
    return smart_size
```

### 7. Market Fetching (customize per skill)

```python
def fetch_markets(filter_tag=""):
    """Fetch markets from Simmer API."""
    try:
        params = {"status": "active", "limit": 200}
        if filter_tag:
            params["tags"] = filter_tag
        result = get_client()._request("GET", "/api/sdk/markets", params=params)
        markets = result.get("markets", [])
        # Fallback to text search if tag returned nothing
        if not markets and filter_tag:
            params.pop("tags", None)
            params["q"] = filter_tag
            result = get_client()._request("GET", "/api/sdk/markets", params=params)
            markets = result.get("markets", [])
        return markets
    except Exception as e:
        print(f"  Failed to fetch markets: {e}")
        return []
```

### 8. Main Strategy Function

```python
def run_strategy(dry_run=True, positions_only=False, show_config=False,
                 smart_sizing=False, use_safeguards=True):
    """Run the trading strategy."""
    print("<emoji> <Skill Name>")
    print("=" * 50)

    get_client(live=not dry_run)  # Validate API key early

    if dry_run:
        print("\n  [PAPER MODE] Use --live for real trades.")

    # Print config...

    if show_config:
        # Print config help and return
        return

    if positions_only:
        # Print positions and return
        return

    # --- Strategy logic ---
    markets = fetch_markets()
    position_size = calculate_position_size(MAX_POSITION_USD, smart_sizing)
    trades_executed = 0

    for market in markets:
        market_id = market.get("id")
        price = market.get("current_probability", 0.5)

        # YOUR SIGNAL LOGIC HERE
        # Determine if this is a trading opportunity
        # ...

        # Price sanity
        if price < MIN_TICK_SIZE or price > (1 - MIN_TICK_SIZE):
            continue

        # Min order size
        if MIN_SHARES_PER_ORDER * price > position_size:
            continue

        # Safeguards
        if use_safeguards:
            context = get_market_context(market_id)
            should_trade, reasons = check_context_safeguards(context)
            if not should_trade:
                print(f"  Safeguard blocked: {'; '.join(reasons)}")
                continue

        # Rate limit
        if trades_executed >= MAX_TRADES_PER_RUN:
            continue

        # Execute
        result = execute_trade(market_id, side, position_size, reasoning="Your thesis")
        if result.get("success"):
            trades_executed += 1

    # Print summary
```

### 9. CLI Entry Point

```python
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="<Skill Name>")
    parser.add_argument("--live", action="store_true", help="Execute real trades")
    parser.add_argument("--dry-run", action="store_true", help="(Default) Dry run")
    parser.add_argument("--positions", action="store_true", help="Show positions only")
    parser.add_argument("--config", action="store_true", help="Show config")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="Set config value")
    parser.add_argument("--smart-sizing", action="store_true",
                        help="Portfolio-based sizing")
    parser.add_argument("--no-safeguards", action="store_true",
                        help="Disable safeguards")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Only output on trades/errors")
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
            updated = update_config(updates, __file__)
            print(f"Config updated: {updates}")
            print(f"Saved to: {get_config_path(__file__)}")
            _config = load_config(CONFIG_SCHEMA, __file__, slug=SKILL_SLUG)
            # Reload module-level vars:
            # globals()["MAX_POSITION_USD"] = _config["max_position_usd"]
            # ... one line per config var

    dry_run = not args.live

    run_strategy(
        dry_run=dry_run,
        positions_only=args.positions,
        show_config=args.config,
        smart_sizing=args.smart_sizing,
        use_safeguards=not args.no_safeguards,
    )
```

### 10. Automaton Reporting

The automaton runs skills as subprocesses and parses a structured JSON report line from stdout. Every skill **must** emit this report so the automaton can track signals, trades, and errors.

**In `run_strategy()`**, collect skip reasons and errors from trade results, then emit the report:

```python
    # Track skip reasons and errors from trade() results
    skip_reasons = []
    execution_errors = []

    for market in candidates:
        result = client.trade(market.id, side, amount, source=TRADE_SOURCE, reasoning=reasoning)
        trades_attempted += 1
        if result.success:
            trades_executed += 1
        elif result.skip_reason:
            skip_reasons.append(result.skip_reason)
        elif result.error:
            execution_errors.append(result.error)

    # Structured report for automaton
    if os.environ.get("AUTOMATON_MANAGED"):
        report = {"signals": signals_found, "trades_attempted": trades_attempted, "trades_executed": trades_executed}
        if skip_reasons:
            report["skip_reason"] = ", ".join(dict.fromkeys(skip_reasons))
        if execution_errors:
            report["execution_errors"] = execution_errors
        print(json.dumps({"automaton": report}))
```

**In `__main__`**, add a fallback report after the `run_strategy()` call. This covers early-return paths (no markets, config display, etc.) where `run_strategy()` exits before reaching its report line:

```python
    run_strategy(...)

    # Fallback report for automaton if the strategy returned early (no signal)
    if os.environ.get("AUTOMATON_MANAGED"):
        print(json.dumps({"automaton": {"signals": 0, "trades_attempted": 0, "trades_executed": 0, "skip_reason": "no_signal"}}))
```

The automaton's parser takes the **first** `{"automaton": ...}` line it finds. When `run_strategy()` emits the real report, that's found first. If it returned early, only the fallback is emitted.

**Fields:**
- `signals` — Number of opportunities/signals found
- `trades_attempted` — Number of trades attempted (including failed)
- `trades_executed` — Number of successful trades
- `skip_reason` (optional) — Comma-separated reasons from `result.skip_reason` (e.g. `"conflicts skipped, budget exhausted"`)
- `execution_errors` (optional) — List of error strings from `result.error` (e.g. `["insufficient liquidity"]`)

## Key Rules

1. **Always default to dry-run.** `--live` must be explicit.
2. **Always tag trades** with `source=TRADE_SOURCE` (e.g. `"sdk:myskill"`). This enables cross-skill conflict detection — `trade()` automatically skips buys on markets where another skill has an open position.
3. **Always check safeguards** before trading (unless `--no-safeguards`).
4. **Never import `py_clob_client` or call Polymarket directly for trades.** Use `SimmerClient` for all trade execution.
5. **Use `get_client()` singleton** — never instantiate `SimmerClient` inline.
6. **Polymarket minimum:** 5 shares per order, $0.01 min tick.
7. **Include reasoning** in trades — it's displayed publicly.
8. **`get_positions()` returns dataclasses** — convert with `asdict()`.
9. **Always emit automaton report** — include the `{"automaton": ...}` JSON line and `__main__` fallback (section 10).
