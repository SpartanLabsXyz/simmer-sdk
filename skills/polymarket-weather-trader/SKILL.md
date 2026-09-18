---
name: polymarket-weather-trader
description: Trade Polymarket temperature markets from NOAA and Open-Meteo forecasts via the Simmer API. Use to run or configure a weather strategy, or to check the forecast a market resolves on.
metadata:
  author: Simmer (@simmer_markets)
  version: "1.23.23"
  displayName: Polymarket Weather Trader
  difficulty: beginner
  attribution: Strategy inspired by gopfan2 (public Polymarket trader; approach referenced, not endorsed).
---
# Polymarket Weather Trader

Trade temperature markets on Polymarket using NOAA forecast data.

> 🚨 **Framework, not a production trading system.** Read [DISCLAIMER.md](./DISCLAIMER.md) before connecting to a wallet with real funds.

> **Template skill.** Defaults to dry-run mode (no real money). The `--live` flag is a deliberate single-command opt-in for real-money execution. Configure tunables (entry/exit thresholds, locations, etc.) via env vars listed below.

## Safety rails (read first)

This skill executes real-money trades on Polymarket only when the `--live` flag is passed AND the human's wallet is linked to their Simmer account. Trading is bounded by default:

- **Dry-run is the default.** `python weather_trader.py` (no flag) shows opportunities but executes no trades. The `--live` flag is required for real-money execution. There is no "auto-graduate" path.
- **`$SIM` paper sandbox option.** Set `TRADING_VENUE=sim` to trade Simmer's $SIM virtual currency at real prices — useful for validating the strategy without USDC exposure.
- **Real-money trading requires explicit human verification.** A wallet must be linked at [simmer.markets/dashboard](https://simmer.markets/dashboard?ref=sdk-skill&utm_campaign=sdk-skill) before any real trade lands. Without a linked wallet the SDK rejects real-money order construction.
- **Per-trade cap.** `SIMMER_WEATHER_MAX_POSITION_USD` defaults to `$2.00` per trade. Configurable via env var, capped at the user's dashboard-set platform per-trade limit.
- **Daily caps.** Platform-level daily caps apply (max trades/day, max USD/day). Set at [simmer.markets/dashboard](https://simmer.markets/dashboard?ref=sdk-skill&utm_campaign=sdk-skill) → SDK settings.
- **Auto stop-loss is ON by default.** Server-side risk monitor watches every buy. Threshold is configurable per user at simmer.markets/dashboard → Settings → Auto Risk Monitor. **It cannot protect against gap-resolution, though:** weather temperature buckets jump straight to about 0 at resolution rather than decaying through your stop, so a percentage stop has no price to trigger on and no liquidity to exit into. Size for the full loss, not for the stop. See [DISCLAIMER.md](./DISCLAIMER.md).
- **Strategy-side safeguards.** Beyond platform risk monitors, this skill checks flip-flop, slippage (`SIMMER_WEATHER_SLIPPAGE_MAX`, default 15%), time-decay (`SIMMER_WEATHER_MIN_HOURS_TO_RESOLVE`, default 2h), and resolved-market status before every order. Run with safeguards on; `--no-safeguards` exists for backtests only.
- **Reversibility.** Open positions exit automatically when price > `SIMMER_WEATHER_EXIT_THRESHOLD` (default `0.45`), or via `client.cancel_order()` / a manual sell. `ENTRY_THRESHOLD` is an **upper** bound (buy *below*). See the `SIMMER_WEATHER_EXIT_THRESHOLD` configuration row before raising the entry threshold; own exits yourself only if you deliberately disable the built-in exit rule.

If anything above isn't clear, stop and ask the user before passing `--live`.

## Strategy logic

Weather market outcomes are discrete: a temperature bucket ("34-35°F") either matches the actual high on resolution day or it doesn't. The strategy works when the NOAA forecast is more accurate than what the market has priced in.

**Test before going live.** The `$SIM` venue gives you a fully virtual sandbox at real market prices — recommended before any `--live` run.

**Risk monitor.** Stop-loss and take-profit thresholds are user settings (configurable at [simmer.markets/dashboard](https://simmer.markets/dashboard?ref=sdk-skill&utm_campaign=sdk-skill) → Settings → Auto Risk Monitor), shared across all skills under that user account. Per-position overrides via `client.set_monitor(market_id, side, stop_loss_pct=..., take_profit_pct=...)`.

**External wallet users**: monitors emit alerts via the briefing endpoint — your agent must be running for sells to execute. Managed wallet users: server executes directly.

## Setup

For wallet setup, see [docs.simmer.markets/wallets](https://docs.simmer.markets/wallets).

Behaviour changes by version: [CHANGELOG.md](./CHANGELOG.md).

Required environment:
- `SIMMER_API_KEY` — get from `simmer.markets/dashboard → SDK tab`
- `WALLET_PRIVATE_KEY` — Polymarket wallet private key (the SDK signs orders client-side)

Then `pip install --upgrade simmer-sdk` (>=0.13.0) and configure tunables below.

## Configuration

| Setting | Environment Variable | Default | Description |
|---------|---------------------|---------|-------------|
| Trading venue | `TRADING_VENUE` | polymarket | Venue to trade on. Set `sim` for paper trading. |
| Entry threshold | `SIMMER_WEATHER_ENTRY_THRESHOLD` | 0.15 | **Upper** bound — buy when price is *below* this |
| Min entry price | `SIMMER_WEATHER_MIN_ENTRY_PRICE` | 0 | **Lower** bound — skip lottery tickets below this (`0` = off), e.g. `0.15` to skip sub-15¢ tickets. Must be below the entry threshold. |
| Min hours to resolve | `SIMMER_WEATHER_MIN_HOURS_TO_RESOLVE` | 2 | Skip entries if the market resolves in fewer than this many hours. Entry-only; exits keep a fixed 2h floor. Discovery looks ahead `max(this, 48h)` calendar days — raise it and the scan widens; a 24h floor still sees +1/+2 day markets in a morning heartbeat. |
| Exit threshold | `SIMMER_WEATHER_EXIT_THRESHOLD` | 0.45 | Sell when price is above this. Raise this before raising entry above `0.45`; otherwise the next cycle can exit the same position you just entered. |
| Max position | `SIMMER_WEATHER_MAX_POSITION_USD` | 2.00 | Maximum USD per trade |
| Max trades/run | `SIMMER_WEATHER_MAX_TRADES_PER_RUN` | 5 | Maximum trades per scan cycle |
| Max buys/market | `SIMMER_WEATHER_MAX_BUYS_PER_MARKET` | 1 | Cap on buy-fills into the same market (bucket), checked against held positions before entry. `1` = one entry then hold; raise to keep DCA-ing into an underpriced bucket; `0` disables the cap (unbounded DCA). |
| Locations | `SIMMER_WEATHER_LOCATIONS` | NYC | Comma-separated cities (NYC, Chicago, Seattle, Atlanta, Dallas, Miami, Austin, Houston, Denver, Beijing, Shanghai, Guangzhou, Shenzhen, Chengdu, Chongqing, Wuhan, Qingdao, Zhengzhou, Singapore, Kuala Lumpur, Manila, Busan, Toronto, Buenos Aires, Sao Paulo, Mexico City, Cape Town, Helsinki, Jeddah, Warsaw, Paris, Panama City) |
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
| Replay forecast archive | `SIMMER_REPLAY_FORECASTS` | (none) | Replay-only. JSON `{station: {YYYY-MM-DD: {high, low}}}`. Live NOAA is never used under replay. If unset, loads `fixtures/replay_forecasts.json` when you add that file (not committed). `.sample.json` is never auto-loaded. |

**Legacy env var aliases** (still accepted for backwards compatibility): `SIMMER_WEATHER_ENTRY`, `SIMMER_WEATHER_EXIT`, `SIMMER_WEATHER_MAX_POSITION`, `SIMMER_WEATHER_MAX_TRADES`

**Supported locations** (city-name filter applied to market questions): NYC, Chicago, Seattle, Atlanta, Dallas, Miami, Austin, Houston, Denver, Tel Aviv, Munich, London, Tokyo, Seoul, Ankara, Lucknow, Wellington, Madrid, Milan, Amsterdam, Taipei, Beijing, Shanghai, Guangzhou, Shenzhen, Chengdu, Chongqing, Wuhan, Qingdao, Zhengzhou, Singapore, Kuala Lumpur, Manila, Busan, Toronto, Buenos Aires, Sao Paulo, Mexico City, Cape Town, Helsinki, Jeddah, Warsaw, Paris, Panama City. The actual oracle station is parsed per-market from `resolution_criteria` — see "Resolution-source routing" below.

## Resolution-source routing

Polymarket weather markets carry a `resolution_criteria` field that names the exact station the market resolves on (e.g. "Chicago O'Hare Intl Airport Station" with `wunderground.com/.../KORD`). v1.21.0+ parses that text per-market and routes to the matching forecast station instead of a city default. If a market names a station the skill doesn't know, the event is skipped with a log line. Add new stations to `STATION_ID_TO_NOAA` (US) or `INTERNATIONAL_STATION_COORDS` (international) in `weather_trader.py` to extend coverage — PRs welcome.

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

# Disable safeguards for backtests only
python weather_trader.py --no-safeguards

# Disable trend detection
python weather_trader.py --no-trends

# Enable volatility targeting (dynamic sizing based on market vol)
python weather_trader.py --live --smart-sizing --vol-targeting

# Quiet mode — only output on trades/errors (ideal for high-frequency runs)
python weather_trader.py --live --quiet
```

## Backtest gate (keep / kill / fix)

90-day **real-capital P&L on Simmer** is the lock. This gate is the fast filter before more real capital. Dogfood's `MIN_HOURS=12` canary is a separate seat — do not change it here.

Reuse the existing replay harness. Do not invent a second one.

```bash
# Pinned gate (no tape, no network, no API key)
python skills/polymarket-weather-trader/scripts/run_backtest_gate.py

# Same tests, direct
python -m pytest skills/polymarket-weather-trader/tests/test_replay_discovery.py -q

# Full-tape KEEP/KILL — build a window archive, copy it (uncommitted), then replay.
# Auto-load is fixtures/replay_forecasts.json only. .sample.json is shape-only
# and is never loaded.
python skills/polymarket-weather-trader/scripts/build_replay_forecast_archive.py \
  --start YYYY-MM-DD --end YYYY-MM-DD --out /tmp/replay_forecasts.json
cp /tmp/replay_forecasts.json \
  skills/polymarket-weather-trader/fixtures/replay_forecasts.json
# SIMMER_WEATHER_LOCATIONS defaults to "NYC" — a tape covering other cities
# (Seoul, London, Chicago, ...) will show 0 entries for every one of them
# unless you widen this list to match the tape's city mix (SIM-5484). That
# is expected scoping, not a station/forecast bug — see the note below.
#
# A plain `export SIMMER_WEATHER_LOCATIONS=...` does NOT reach the skill:
# the replay harness builds the bundle subprocess env from a strict
# allowlist (SIM-5067) that this var is not on, so it is silently stripped
# and the run measures NYC regardless. Use --set on the entrypoint instead
# — it writes config.json next to weather_trader.py, which load_config()
# reads before env vars and which survives the per-tick bundle copy.
#
# Name the tape's actual cities, not a generic US list. E.g. for an
# April tape weighted Seoul/Hong Kong/London/Shanghai/NYC/Paris: Seoul,
# London, Paris and Shanghai are mapped (INTERNATIONAL_STATION_COORDS) and
# will enter; Hong Kong has no station mapping anywhere in the skill and
# cannot enter no matter what's in this list.
simmer backtest skills/polymarket-weather-trader \
  --entrypoint weather_trader.py --t0 YYYY-MM-DD --t1 YYYY-MM-DD \
  --cadence 12h --q temperature --min-volume 0 \
  --args "--live --quiet --set locations=NYC,Chicago,Seattle,Atlanta,Miami,Austin,Houston,Denver,Seoul,London,Paris,Shanghai"
# Then read the KEEP/KILL table below, plus the per-city entry count the
# report prints — a KEEP/PIVOT read needs coverage across the mapped
# international cities, not just NYC. Optional in-process / pytest:
# export SIMMER_REPLAY_FORECASTS=/tmp/replay_forecasts.json
```

| Verdict | What the backtest / replay outcome means for the money path |
|---------|--------------------------------------------------------------|
| **FIX** | Path is broken or the tape cannot evaluate the skill. Do not add capital. Repair: discovery 422 / fail-open empty listing; wall-clock horizon under replay; `yes_price` ignored (silent 0.50); every event skipped for missing `resolution_criteria`; live NOAA/Open-Meteo under replay (look-ahead); preflight `WALLET_UNVERIFIED` blocking SimState fills; 0 temperature markets on a high-volume slice (`--q temperature`, lower `--min-volume`); 0 entries because the forecast archive is missing, empty, or does not cover the tape dates (`SIMMER_REPLAY_FORECASTS` / `fixtures/replay_forecasts.json` — missing forecast plane, not "no edge"). |
| **not a defect** | Every entry lands in one city (default NYC) on a multi-city tape. `SIMMER_WEATHER_LOCATIONS` defaults to `"NYC"` — that is the skill honoring its configured scope, not a station/parsing bug. Widen it via `--set locations=...` (a plain `export` is stripped by the replay harness allowlist) to the tape's full city mix before judging KEEP/KILL from entry count (SIM-5484). Hong Kong specifically never enters on any tape — the skill has no station mapping for it, not a defect. |
| **KILL** | Pinned pytest gate fails, **or** the path is green on a weather-capable tape, evals > 0, and the skill still cannot reach `execute_trade` when a forecast is injected or loaded from the archive, **or** after the path works, P&L after costs is clearly ≤ 0 on an honest (not live-NOAA) forecast. Do not add more real capital. |
| **KEEP** | Full-tape `simmer backtest ... --q temperature` with evals > 0 **and** entries > 0 **and** an honest archive (not live NOAA). Pinned unit tests passing is a path check only — not KEEP. Provisional; the 90-day Simmer P&L lock still decides scale-up. |

A green full-tape run that places **0** trades because NOAA is correctly dark is **FIX** (forecast plane), not **KILL**. Copy a real window-covering archive to `fixtures/replay_forecasts.json`, then re-run. The committed `.sample.json` is not loaded.

## How It Works

Each cycle the script:
1. Fetches active weather markets from Simmer API (newest weather page plus dated queries out to `max(MIN_HOURS_TO_RESOLVE, 48h)`)
2. Groups markets by event (each temperature day is one event)
3. Parses event names to get location and date; keeps events inside that discovery horizon
4. Fetches NOAA forecast for that location/date (under replay: archive only — never live NOAA)
5. Finds the temperature bucket that matches the forecast
6. **Safeguards**: Checks context for flip-flop warnings, slippage, time decay
7. **Trend Detection**: Looks for recent price drops (stronger buy signal)
8. **Entry**: If min-entry ≤ bucket price < entry threshold and safeguards pass → BUY
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
- **Slippage**: Skips if estimated slippage > 15% (tunable via `SIMMER_WEATHER_SLIPPAGE_MAX`)
- **Time decay**: Skips if market resolves in < `SIMMER_WEATHER_MIN_HOURS_TO_RESOLVE` hours (default 2). Discovery looks ahead `max(this, 48h)` so raising the floor does not starve morning heartbeats.
- **Market status**: Skips if market already resolved

`SIMMER_WEATHER_MIN_ENTRY_PRICE` (default 0 = off) is the other side of the entry-price check, not this list — `--no-safeguards` does not disable it.

Run with safeguards on; `--no-safeguards` exists for backtests only.

## Source Tagging

All trades are tagged with `source: "sdk:weather"`. This means:
- Portfolio shows breakdown by strategy
- Trades tagged `sdk:weather` are excluded from generic copytrade sells.
- You can track weather P&L separately

## Troubleshooting

**"Safeguard blocked: Severe flip-flop warning"** — you've been changing direction too much on this market; wait before trading again.

**"Slippage too high"** — market is illiquid; reduce position size or skip.

**"Resolves in Xh - too soon"** — market resolving sooner than `SIMMER_WEATHER_MIN_HOURS_TO_RESOLVE` (default 2h). Raise `SIMMER_WEATHER_MIN_HOURS_TO_RESOLVE` to widen discovery; a morning heartbeat with 24h sees +1/+2-day markets.

**"Price $X.XX below min entry"** — mid is below `SIMMER_WEATHER_MIN_ENTRY_PRICE`. Lottery-ticket floor; default is off (`0`).

**"No weather markets found"** — weather markets may not be active (seasonal).

**`simmer backtest` / "Failed to fetch markets from Simmer API"** — replay does not implement `tags` or `status` (422 since sdk 0.25.3/0.25.4). v1.23.9+ uses `q=temperature` under replay. A fetch failure now fails the tick (`failed_ticks`, not a clean 0-eval). If the fetch succeeds and you still see 0 weather markets, the HF volume slice likely has none — default `--min-volume` / top-volume selection is a tape follow-up, not a skill bug. Replay listings omit `resolution_criteria`; v1.23.11+ falls back to the city station table only when criteria is **missing** (Dallas excluded; present-but-unreadable still skips). Live NOAA is never called under replay.

**A full-tape run with 0 entries and NOAA dark is FIX** until a real archive covers the tape dates. Build one with `scripts/build_replay_forecast_archive.py`, then copy it to `fixtures/replay_forecasts.json` before `simmer backtest` (harness strips host env; that user file is copied with the bundle). Shape: `{ "_meta": {…}, "KLGA": { "2026-04-30": { "high": 72, "low": 50, "leads": { "1": {…}, "2": {…}, "3": {…} } } } }` — top-level high/low is lead 1; `_meta` is ignored. Hand-built files may omit `leads`. `fixtures/replay_forecasts.sample.json` is that shape only — invented test temps, never auto-loaded. A set-but-missing path fails the tick. Do not treat a 0-entry tape as no-edge. See **Backtest gate** above.

**"External wallet requires a pre-signed order"** — `WALLET_PRIVATE_KEY` is not set. Fix: `export WALLET_PRIVATE_KEY=0x<your-polymarket-wallet-private-key>`. The SDK signs orders automatically when this env var is present — do not attempt to sign orders manually.

**"Balance shows $0 but I have funds on Polygon"** — Polymarket V2 (live 2026-04-28) uses **pUSD** (PolyUSD, 1:1 backed by USDC.e). Migrate at [simmer.markets/dashboard](https://simmer.markets/dashboard?ref=sdk-skill&utm_campaign=sdk-skill) (~30s). Full guide: [docs.simmer.markets/v2-migration](https://docs.simmer.markets/v2-migration).

**"API key invalid"** — get a new key from simmer.markets/dashboard → SDK tab.
