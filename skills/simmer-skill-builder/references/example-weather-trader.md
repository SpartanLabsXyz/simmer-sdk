# Example: Weather Trader

Pattern: **External API signal + Simmer SDK trading.**

Fetches NOAA temperature forecasts, compares to Polymarket weather market prices, buys underpriced buckets.

## SKILL.md Frontmatter

```yaml
---
name: polymarket-weather-trader
displayName: Polymarket Weather Trader
description: Trade Polymarket weather markets using NOAA forecasts via Simmer API.
metadata: {"clawdbot":{"emoji":"<thermometer>","requires":{"env":["SIMMER_API_KEY"],"pip":["simmer-sdk"]},"cron":null,"autostart":false,"automaton":{"managed":true,"entrypoint":"weather_trader.py"}}}
version: "1.10.1"
published: true
---
```

## Config Schema

```python
CONFIG_SCHEMA = {
    "entry_threshold": {"env": "SIMMER_WEATHER_ENTRY", "default": 0.15, "type": float},
    "exit_threshold": {"env": "SIMMER_WEATHER_EXIT", "default": 0.45, "type": float},
    "max_position_usd": {"env": "SIMMER_WEATHER_MAX_POSITION", "default": 2.00, "type": float},
    "sizing_pct": {"env": "SIMMER_WEATHER_SIZING_PCT", "default": 0.05, "type": float},
    "max_trades_per_run": {"env": "SIMMER_WEATHER_MAX_TRADES", "default": 5, "type": int},
    "locations": {"env": "SIMMER_WEATHER_LOCATIONS", "default": "NYC", "type": str},
}
```

## Signal: External API (NOAA)

```python
NOAA_API_BASE = "https://api.weather.gov"

def get_noaa_forecast(location):
    """Get NOAA forecast. Returns {date: {high: temp, low: temp}}."""
    loc = LOCATIONS[location]
    headers = {"User-Agent": "SimmerWeatherSkill/1.0", "Accept": "application/geo+json"}

    # Step 1: Get grid coordinates
    points_data = fetch_json(f"{NOAA_API_BASE}/points/{loc['lat']},{loc['lon']}", headers)
    forecast_url = points_data["properties"]["forecast"]

    # Step 2: Get forecast
    forecast_data = fetch_json(forecast_url, headers)
    periods = forecast_data["properties"]["periods"]

    # Step 3: Parse into {date: {high, low}}
    forecasts = {}
    for period in periods:
        date_str = period["startTime"][:10]
        temp = period["temperature"]
        if period["isDaytime"]:
            forecasts.setdefault(date_str, {})["high"] = temp
        else:
            forecasts.setdefault(date_str, {})["low"] = temp
    return forecasts
```

## Market Matching

```python
def parse_temperature_bucket(outcome_name):
    """Parse '32-36' or '40 or above' into (low, high) tuple."""
    # "32 or below" -> (-999, 32)
    # "50 or higher" -> (50, 999)
    # "37-41" -> (37, 41)
    ...

# Match NOAA forecast to correct bucket
for market in event_markets:
    bucket = parse_temperature_bucket(market["outcome_name"])
    if bucket and bucket[0] <= forecast_temp <= bucket[1]:
        matching_market = market
        break
```

## Strategy Core

```python
# For each weather event:
# 1. Get NOAA forecast temperature
# 2. Find the market bucket that contains the forecast temp
# 3. If that bucket's price < entry_threshold (15c), BUY
# 4. If holding and price > exit_threshold (45c), SELL

if price < ENTRY_THRESHOLD:
    result = execute_trade(market_id, "yes", position_size)

# Exit check on existing positions
if current_price >= EXIT_THRESHOLD:
    result = execute_sell(market_id, shares)
```

## Key Patterns Demonstrated

1. **External API integration** — NOAA REST API with custom User-Agent
2. **Market grouping** — Groups markets by event (location + date)
3. **Bucket matching** — Parses outcome names to match forecast to market
4. **Entry + exit logic** — Buys low, sells when price rises
5. **Price trend detection** — Checks 24h price history for drops (stronger signal)
6. **Auto-discovery** — Uses `list_importable_markets()` to find and import new weather markets
7. **Source tagging** — `TRADE_SOURCE = "sdk:weather"` on all trades
8. **Edge analysis** — Passes `my_probability` to context endpoint for edge recommendation
