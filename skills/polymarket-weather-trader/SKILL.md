---
name: polymarket-weather-trader
description: Trade Polymarket weather markets using NOAA (US) and Open-Meteo (international) forecasts via Simmer API. Inspired by gopfan2's weather trading approach. Use when user wants to trade temperature markets, automate weather bets, check forecasts, or run weather-based strategies.
metadata:
  author: Simmer (@simmer_markets)
  version: "1.22.0"
  displayName: Polymarket Weather Trader
  difficulty: beginner
  attribution: Strategy inspired by gopfan2 (public Polymarket trader — approach referenced, not endorsed).
---
# Polymarket Weather Trader

Trade temperature markets on Polymarket using NOAA forecast data.

> **Template skill.** Defaults to dry-run mode (no real money). The `--live` flag is a deliberate single-command opt-in for real-money execution. Configure tunables (entry/exit thresholds, locations, etc.) via env vars listed below.

## Safety rails (read first)

This skill executes real-money trades on Polymarket only when the `--live` flag is passed AND the human's wallet is linked to their Simmer account. Trading is bounded by default:

- **Dry-run is the default.** `python weather_trader.py` (no flag) shows opportunities but executes no trades. The `--live` flag is required for real-money execution. There is no "auto-graduate" path.
- **`$SIM` paper sandbox option.** Set `TRADING_VENUE=sim` to trade Simmer's $SIM virtual currency at real prices — useful for validating the strategy without USDC exposure.
- **Real-money trading requires explicit human verification.** A wallet must be linked at [simmer.markets/dashboard](https://simmer.markets/dashboard) before any real trade lands. Without a linked wallet the SDK rejects real-money order construction.
- **Per-trade cap.** `SIMMER_WEATHER_MAX_POSITION_USD` defaults to `$2.00` per trade. Configurable via env var, capped at the user's dashboard-set platform per-trade limit.
- **Daily caps.** Platform-level daily caps apply (max trades/day, max USD/day). Set at [simmer.markets/dashboard](https://simmer.markets/dashboard) → SDK settings.
- **Auto stop-loss is ON by default.** Server-side risk monitor watches every buy. Threshold is configurable per user at simmer.markets/dashboard → Settings → Auto Risk Monitor.
- **Strategy-side safeguards.** Beyond platform risk monitors, this skill checks flip-flop, slippage (`SIMMER_WEATHER_SLIPPAGE_MAX`, default 15%), time-decay, and resolved-market status before every order. Disable only with `--no-safeguards` (not recommended).
- **Reversibility.** Open positions exit automatically when price > `SIMMER_WEATHER_EXIT_THRESHOLD` (default `0.45`), or via `client.cancel_order()` / a manual sell.

If anything above isn't clear, stop and ask the user before passing `--live`.

## Strategy logic

Weather market outcomes are discrete: a temperature bucket ("34-35°F") either matches the actual high on resolution day or it doesn't. The strategy works when the NOAA forecast is more accurate than what the market has priced in.

**Test before going live.** The `$SIM` venue gives you a fully virtual sandbox at real market prices — recommended before any `--live` run.

**Risk monitor.** Stop-loss and take-profit thresholds are user settings (configurable at [simmer.markets/dashboard](https://simmer.markets/dashboard) → Settings → Auto Risk Monitor), shared across all skills under that user account. Per-position overrides via `client.set_monitor(market_id, side, stop_loss_pct=..., take_profit_pct=...)`.

**External wallet users**: monitors emit alerts via the briefing endpoint — your agent must be running for sells to execute. Managed wallet users: server executes directly.

## When to Use This Skill

Use this skill when the user wants to:
- Trade weather markets automatically
- Set up gopfan2-style temperature trading
- Buy low on weather predictions
- Check their weather trading positions
- Configure trading thresholds or locations

## What's New in v1.21.0

- **Per-market resolution source.** Each market is now routed to the specific weather station Polymarket actually reads (parsed from the market's `resolution_criteria` field). Previously the skill used a hardcoded city → station map, which silently traded against the wrong forecast in a few cases (notably Dallas, where Polymarket resolves on Love Field / KDAL but the skill assumed DFW / KDFW). Markets that name a station the skill doesn't know are now skipped with a log line — better to skip than to trade a stale oracle. Robust to Polymarket swapping airports.
- **Expanded NOAA station coverage.** KLGA, KJFK, KEWR, KNYC, KORD, KMDW, KSEA, KATL, KDAL, KDFW, KMIA, KBOS, KDCA, KIAD, KPHX, KLAS, KSFO, KLAX, KDEN, KMSP, KPHL.
- **Expanded international coverage.** Adds Madrid, Milan, Amsterdam, Taipei to Open-Meteo routing (alongside existing Tel Aviv, Munich, London, Tokyo, Seoul, Ankara, Lucknow, Wellington).
- **Requires the new `?include=resolution_criteria` flag** on `/api/sdk/markets` (live on Simmer backend 2026-05-03).

## What's New in v1.20.1

- **Safety rails section first.** Bounding contract surfaced at the top — paper-default, `--live` requirement, configurable caps, server-side risk monitor, strategy-side safeguards, reversibility.
- **Risk monitor framing genericized.** Stop-loss / take-profit thresholds are described as configurable user settings rather than specific percentages. (See FAQ at docs.simmer.markets for current defaults — they're user-tunable in the dashboard.)
- **Wallet setup link genericized.** Points at [docs.simmer.markets/wallets](https://docs.simmer.markets/wallets) instead of a named cross-skill.

## What's New in v1.20.0

- **SDK 0.13.0 integration** — uses `SimmerClient.from_env()` (auto-reads `SIMMER_API_KEY`, raises a clear `RuntimeError` with a dashboard pointer if unset). Requires `simmer-sdk>=0.13.0`.
- **Slim per skill catalog reshape (Phase 3)** — duplicated wallet-setup / changelog / decorative content removed; SKILL.md trimmed to focus on what's specific to this skill.
- **Dead code removed** — retired `AUTOMATON_*` env reads (the automaton runtime was retired 2026-04-20).

## Setup

For wallet setup, see [docs.simmer.markets/wallets](https://docs.simmer.markets/wallets).

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
| Portfolio cap | `SIMMER_PORTFOLIO_CAP_ENABLED` | false | Enable portfolio-level concurrent-exposure cap (cross-skill open notional ceiling). Off by default. |
| Portfolio cap pct | `SIMMER_PORTFOLIO_CAP_PCT` | 0.15 | Total open notional cap as a fraction of bankroll. Only used when the cap is enabled. |

### Portfolio-level concurrent-exposure cap (v1.22.0+)

Multi-strategy users can layer a *cross-skill* exposure ceiling on top of the per-trade sizing. With `SIMMER_PORTFOLIO_CAP_ENABLED=true`, before every buy the skill:

1. Computes the candidate size via the existing entry/sizing logic.
2. Snapshots open positions across **all** skills (not just weather) for the same agent.
3. Calls `simmer_sdk.risk.check_portfolio_cap` with a `SIMMER_PORTFOLIO_CAP_PCT` × bankroll ceiling.
4. Either places the candidate (`allow`), trims it to fit (`trim_to`), or skips (`deny`).

This is the SIM-1451 primitive — see [docs.simmer.markets/sdk/risk-management/portfolio-cap](https://docs.simmer.markets/sdk/risk-management/portfolio-cap) for details. It's *opt-in*: existing installs see no behavior change. The cap is forward-looking (gates new entries based on currently-open exposure) and is distinct from the SIM-1072 `DrawdownController` (peak-trough realized-PnL halt — separate primitive, separate ticket).

**Legacy env var aliases** (still accepted for backwards compatibility): `SIMMER_WEATHER_ENTRY`, `SIMMER_WEATHER_EXIT`, `SIMMER_WEATHER_MAX_POSITION`, `SIMMER_WEATHER_MAX_TRADES`

**Supported locations** (city-name filter applied to market questions): NYC, Chicago, Seattle, Atlanta, Dallas, Miami, plus international cities (Tel Aviv, Munich, London, Tokyo, Seoul, Ankara, Lucknow, Wellington, Madrid, Milan, Amsterdam, Taipei). The actual oracle station is parsed per-market from `resolution_criteria` — see "Resolution-source routing" below.

## Resolution-source routing

Polymarket weather markets carry a `resolution_criteria` field that names the exact station the market resolves on (e.g. "Chicago O'Hare Intl Airport Station" with `wunderground.com/.../KORD`). v1.21.0+ parses that text per-market and routes to the matching forecast station instead of a city default. If a market names a station the skill doesn't know, the event is skipped with a log line. Add new stations to `STATION_ID_TO_NOAA` (US) or `INTERNATIONAL_STATION_TO_CITY` (international) in `weather_trader.py` to extend coverage — PRs welcome.

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
