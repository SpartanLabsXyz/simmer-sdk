# Example: Mert Sniper

Pattern: **Simmer API only — filter markets by criteria, trade the edge.**

No external data source. Scans Simmer markets for near-expiry opportunities with heavily skewed odds, backs the favorite.

## SKILL.md Frontmatter

```yaml
---
name: polymarket-mert-sniper
displayName: Mert Sniper
description: Near-expiry conviction trading on Polymarket. Snipe markets about to resolve when odds are heavily skewed.
metadata: {"clawdbot":{"emoji":"<target>","requires":{"env":["SIMMER_API_KEY"],"pip":["simmer-sdk"]},"cron":null,"autostart":false,"automaton":{"managed":true,"entrypoint":"mert_sniper.py"}}}
version: "1.0.7"
published: true
---
```

## Config Schema

```python
CONFIG_SCHEMA = {
    "market_filter": {"env": "SIMMER_MERT_FILTER", "default": "", "type": str},
    "max_bet_usd": {"env": "SIMMER_MERT_MAX_BET", "default": 10.00, "type": float},
    "expiry_window_mins": {"env": "SIMMER_MERT_EXPIRY_MINS", "default": 2, "type": int},
    "min_split": {"env": "SIMMER_MERT_MIN_SPLIT", "default": 0.60, "type": float},
    "max_trades_per_run": {"env": "SIMMER_MERT_MAX_TRADES", "default": 5, "type": int},
    "sizing_pct": {"env": "SIMMER_MERT_SIZING_PCT", "default": 0.05, "type": float},
}
```

## Market Fetching with Tag + Text Fallback

```python
def fetch_markets(market_filter=""):
    params = {"status": "active", "limit": 200}
    if market_filter:
        params["tags"] = market_filter

    result = get_client()._request("GET", "/api/sdk/markets", params=params)
    markets = result.get("markets", [])

    # If tag returned nothing, try text search
    if not markets and market_filter:
        params.pop("tags", None)
        params["q"] = market_filter
        result = get_client()._request("GET", "/api/sdk/markets", params=params)
        markets = result.get("markets", [])

    return markets
```

## Strategy Core

```python
# 1. Fetch all active markets (optionally filtered by tag/keyword)
markets = fetch_markets(market_filter)

# 2. Filter to markets resolving within N minutes
now = datetime.now(timezone.utc)
for market in markets:
    resolves_at = parse_resolves_at(market.get("resolves_at"))
    minutes_remaining = (resolves_at - now).total_seconds() / 60
    if 0 < minutes_remaining <= EXPIRY_WINDOW_MINS:
        expiring_markets.append(market)

# 3. Check split — only trade when one side >= min_split (e.g. 60%)
price = market.get("current_probability", 0.5)
if price < MIN_SPLIT and price > (1 - MIN_SPLIT):
    continue  # Split too narrow

# 4. Back the favorite
if price >= MIN_SPLIT:
    side = "yes"
else:
    side = "no"

# 5. Safeguards, then execute
context = get_market_context(market_id)
should_trade, reasons = check_context_safeguards(context)
if should_trade:
    reasoning = f"Near-expiry snipe: {side.upper()} at {price:.0%} with {mins_left}m to resolution"
    result = execute_trade(market_id, side, position_size, reasoning=reasoning)
```

## Key Patterns Demonstrated

1. **No external API** — Uses only Simmer SDK for market data and trading
2. **Time-based filtering** — `resolves_at` parsing for near-expiry detection
3. **Split threshold** — Trades only when odds are heavily skewed
4. **Direction selection** — Backs the side with higher probability
5. **Reasoning included** — Trade thesis passed for public display
6. **Pre-computed sizing** — `calculate_position_size()` called once before loop (avoids repeated portfolio API calls)
7. **CLI overrides** — `--filter` and `--expiry` override config at runtime
