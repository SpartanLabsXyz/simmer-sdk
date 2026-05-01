---
name: polymarket-weather-trader
description: Trade Polymarket weather markets using NOAA (US) and Open-Meteo (international) forecasts via Simmer API. Inspired by gopfan2's weather trading approach. Use when user wants to trade temperature markets, automate weather bets, check forecasts, or run weather-based strategies.
metadata:
  author: Simmer (@simmer_markets)
  version: "1.20.0"
  displayName: Polymarket Weather Trader
  difficulty: beginner
  attribution: Strategy inspired by gopfan2 (public Polymarket trader — approach referenced, not endorsed).
---
# Polymarket Weather Trader

Trade temperature markets on Polymarket using NOAA forecast data.

> **This is a template.** The default signal is NOAA temperature forecasts — remix it with other weather APIs, different forecast models, or additional market types (precipitation, wind, etc.). The skill handles the plumbing (market discovery, NOAA parsing, trade execution, safeguards). Your agent provides the alpha.

## Risk Management

Weather market outcomes are discrete: a temperature bucket ("34-35°F") either matches the actual high on resolution day or it doesn't. The strategy works when the NOAA forecast is more accurate than what the market has priced in.

**Test before going live.** The skill defaults to paper mode — trades are simulated at real market prices while your USDC stays untouched. Pass `--live` when you're ready. For a fully virtual sandbox, switch the SDK venue to `sim` for $SIM-denominated paper trading.

Simmer's server-side risk monitor handles stop-loss and take-profit automatically. Defaults (editable at `simmer.markets/dashboard → Settings → Auto Risk Monitor`):

- Stop-loss at 20% drawdown from entry
- Take-profit at 50% price

**External wallet users**: monitors emit alerts via the briefing endpoint — your agent must be running for sells to execute. Managed wallet users: server executes directly.

You can override defaults per-skill in the dashboard.

## When to Use This Skill

Use this skill when the user wants to:
- Trade weather markets automatically
- Set up gopfan2-style temperature trading
- Buy low on weather predictions
- Check their weather trading positions
- Configure trading thresholds or locations

## What's New in v1.20.0

- **SDK 0.13.0 integration** — uses `SimmerClient.from_env()` (auto-reads `SIMMER_API_KEY`, raises a clear `RuntimeError` with a dashboard pointer if unset). Requires `simmer-sdk>=0.13.0`.
- **Slim per skill catalog reshape (Phase 3)** — duplicated wallet-setup / changelog / decorative content removed; SKILL.md trimmed to focus on what's specific to this skill.
- **Dead code removed** — retired `AUTOMATON_*` env reads (the automaton runtime was retired 2026-04-20).

## Setup

For wallet setup, see [simmer-wallet-setup on ClawHub](https://clawhub.ai/skills/simmer-wallet-setup) or [docs.simmer.markets/wallets](https://docs.simmer.markets/wallets).

Required environment:
- `SIMMER_API_KEY` — get from `simmer.markets/dashboard → SDK tab`
- `WALLET_PRIVATE_KEY` — Polymarket wallet private key (the SDK signs orders client-side)

Then `pip install --upgrade simmer-sdk` (>=0.13.0) and configure tunables below.

## Configuration

| Setting | Environment Variable | Default | Description |
|---------|---------------------|---------|-------------|
| Trading venue | `TRADING_VENUE` | polymarket | Venue to trade on. Set `sim` for paper trading. |
| Entry threshold | `SIMMER_WEATHER_ENTRY_THRESHOLD` | 0.15 | Buy when price below this |
| Exit threshold | `SIMMER_WEATHER_EXIT_THRESHOLD` | 0.45 | Sell when price above this |
| Max position | `SIMMER_WEATHER_MAX_POSITION_USD` | 2.00 | Maximum USD per trade |
| Max trades/run | `SIMMER_WEATHER_MAX_TRADES_PER_RUN` | 5 | Maximum trades per scan cycle |
| Locations | `SIMMER_WEATHER_LOCATIONS` | NYC | Comma-separated cities (NYC, Chicago, Seattle, Atlanta, Dallas, Miami) |
| Binary only | `SIMMER_WEATHER_BINARY_ONLY` | false | Skip range-bucket events (e.g., "34-35°F"), only trade binary yes/no markets |
| Smart sizing % | `SIMMER_WEATHER_SIZING_PCT` | 0.05 | % of balance per trade |
| Slippage max | `SIMMER_WEATHER_SLIPPAGE_MAX` | 0.15 | Skip trades with slippage above this (0.15 = 15%) |
| Min liquidity | `SIMMER_WEATHER_MIN_LIQUIDITY` | 0 | Skip markets with liquidity below this USD amount (0 = disabled) |
| Vol targeting | `SIMMER_WEATHER_VOL_TARGETING` | false | Enable volatility targeting for dynamic position sizing |
| Target vol | `SIMMER_WEATHER_TARGET_VOL` | 0.20 | Target annualized volatility (0.20 = 20%) |
| Vol max leverage | `SIMMER_WEATHER_VOL_MAX_LEVERAGE` | 2.0 | Max scale-up multiplier in calm markets |
| Vol min alloc | `SIMMER_WEATHER_VOL_MIN_ALLOC` | 0.2 | Min allocation floor in volatile markets (0.2 = 20%) |
| Vol EWMA span | `SIMMER_WEATHER_VOL_SPAN` | 10 | EWMA span for vol calculation (lower = more responsive) |
| Order type | `SIMMER_WEATHER_ORDER_TYPE` | GTC | GTC (limit, waits for fill) or FAK (cancel if not filled). GTC recommended. |

**Legacy env var aliases** (still accepted for backwards compatibility): `SIMMER_WEATHER_ENTRY`, `SIMMER_WEATHER_EXIT`, `SIMMER_WEATHER_MAX_POSITION`, `SIMMER_WEATHER_MAX_TRADES`

**Supported locations:** NYC, Chicago, Seattle, Atlanta, Dallas, Miami

## SDK initialization

```python
from simmer_sdk import SimmerClient

client = SimmerClient.from_env(venue="polymarket", live=True)
```

`from_env()` (added in simmer-sdk 0.13.0) reads `SIMMER_API_KEY` from the environment and raises `RuntimeError` with a dashboard pointer if unset. If `OWS_WALLET` is set, it auto-routes through the OpenClaw shared wallet.

## Quick Commands

```bash
# Check account balance and positions
python scripts/status.py

# Detailed position list
python scripts/status.py --positions
```

**API Reference:**
- Base URL: `https://api.simmer.markets`
- Auth: `Authorization: Bearer $SIMMER_API_KEY`
- Portfolio: `GET /api/sdk/portfolio`
- Positions: `GET /api/sdk/positions`

## Running the Skill

```bash
# Dry run (default — shows opportunities, no trades)
python weather_trader.py

# Execute real trades
python weather_trader.py --live

# With smart position sizing (uses portfolio balance)
python weather_trader.py --live --smart-sizing

# Check positions only
python weather_trader.py --positions

# View config
python weather_trader.py --config

# Disable safeguards (not recommended)
python weather_trader.py --no-safeguards

# Disable trend detection
python weather_trader.py --no-trends

# Enable volatility targeting (dynamic sizing based on market vol)
python weather_trader.py --live --smart-sizing --vol-targeting

# Quiet mode — only output on trades/errors (ideal for high-frequency runs)
python weather_trader.py --live --quiet
```

## How It Works

Each cycle the script:
1. Fetches active weather markets from Simmer API
2. Groups markets by event (each temperature day is one event)
3. Parses event names to get location and date
4. Fetches NOAA forecast for that location/date
5. Finds the temperature bucket that matches the forecast
6. **Safeguards**: Checks context for flip-flop warnings, slippage, time decay
7. **Trend Detection**: Looks for recent price drops (stronger buy signal)
8. **Entry**: If bucket price < threshold and safeguards pass → BUY
9. **Exit**: Checks open positions, sells if price > exit threshold
10. **Tagging**: All trades tagged with `sdk:weather` for tracking

## Smart Sizing

With `--smart-sizing`, position size is calculated as:
- 5% of available USDC balance (configurable via `SIMMER_WEATHER_SIZING_PCT`)
- Capped at max position setting ($2.00 default)
- Falls back to fixed size if portfolio unavailable

## Volatility Targeting

With `--vol-targeting`, position sizes are dynamically adjusted based on realized market volatility:

```
position_size = base_size × clamp(target_vol / realized_vol, min_alloc, max_leverage)
```

- **High volatility**: positions scale down → less risk
- **Low volatility**: positions scale up → more alpha capture
- Falls back to base size if insufficient price history (< 15 data points)

## Safeguards

Before trading, the skill checks:
- **Flip-flop warning**: Skips if you've been reversing too much
- **Slippage**: Skips if estimated slippage > 15% (tunable)
- **Time decay**: Skips if market resolves in < 2 hours
- **Market status**: Skips if market already resolved

Disable with `--no-safeguards` (not recommended).

## Source Tagging

All trades are tagged with `source: "sdk:weather"`. This means:
- Portfolio shows breakdown by strategy
- Trades tagged `sdk:weather` are excluded from generic copytrade sells.
- You can track weather P&L separately

## Troubleshooting

**"Safeguard blocked: Severe flip-flop warning"** — you've been changing direction too much on this market; wait before trading again.

**"Slippage too high"** — market is illiquid; reduce position size or skip.

**"Resolves in Xh - too soon"** — market resolving soon, risk is elevated.

**"No weather markets found"** — weather markets may not be active (seasonal).

**"External wallet requires a pre-signed order"** — `WALLET_PRIVATE_KEY` is not set. Fix: `export WALLET_PRIVATE_KEY=0x<your-polymarket-wallet-private-key>`. The SDK signs orders automatically when this env var is present — do not attempt to sign orders manually.

**"Balance shows $0 but I have funds on Polygon"** — Polymarket V2 (live 2026-04-28) uses **pUSD** (PolyUSD, 1:1 backed by USDC.e). Migrate at [simmer.markets/dashboard](https://simmer.markets/dashboard) (~30s). Full guide: [docs.simmer.markets/v2-migration](https://docs.simmer.markets/v2-migration).

**"API key invalid"** — get a new key from simmer.markets/dashboard → SDK tab.
