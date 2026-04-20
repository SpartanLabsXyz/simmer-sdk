---
name: weather-ev-port
description: "[WIP / DO NOT PUBLISH] Polymarket weather trader with EV + fractional-Kelly sizing + trailing stop-loss. Ports alteregoeth-ai/weatherbot (MIT) onto Simmer SDK. Placeholder slug — pending AlterEgo outreach response."
metadata:
  author: Simmer (WIP port)
  version: "0.1.0-wip"
  displayName: Weather EV Port (WIP)
  difficulty: advanced
  status: work-in-progress
  upstream: https://github.com/alteregoeth-ai/weatherbot
  attribution: "Strategy math, forecast pipeline, and airport-station city table derived from alteregoeth-ai/weatherbot (MIT License). See ATTRIBUTION.md."
---

# Weather EV Port (WIP)

> ⚠️ **This is an internal work-in-progress port. Not published. Placeholder slug.**
> Final naming, SKILL.md voice, and publishing path depend on AlterEgo's response to the outreach in SIM-983. Do not distribute, do not publish to ClawHub/skills.sh, do not rename. See `_dev/active/_weather-strategy-diversification/track-1b-local-port.md`.

## What this skill does

Trades Polymarket temperature markets using:

- **3 forecast sources** — ECMWF (global) and HRRR/GFS (US) via Open-Meteo, METAR observations via Aviation Weather
- **EV-gated entry** — only takes trades where expected value meets a configurable threshold (default +5%)
- **Fractional Kelly sizing** — 25% Kelly by default, capped at `max_bet_usd`
- **Pre-entry live-ask revalidation** — re-fetches bestAsk/bestBid from Polymarket Gamma immediately before placing the order; skips if spread widened or price moved above `max_price`
- **20% stop-loss with trailing-to-breakeven** — stop moves to breakeven once the trade is up 20%
- **Forecast-change exit** — closes the position if the forecast drifts more than 2°F / 1°C outside the bucket
- **Self-calibration stub** — per-city-per-source sigma storage in `data/calibration.json` (updater deferred to a follow-up)
- **Airport-station coordinates** — every weather market resolves on a specific station (NYC = LaGuardia, Dallas = Love Field, etc.), not city center. Using city-center coords causes systematic bias.

## Default cities (6 US)

`nyc, chicago, miami, dallas, seattle, atlanta`

The full 20-city table (4 continents) is in the LOCATIONS dict — extend `SIMMER_WEATHER_EV_LOCATIONS` to enable.

## Order routing

All order placement goes through Simmer SDK → PolyNode (Simmer's sole order router). The skill never touches CLOB signing directly. `TRADING_VENUE=sim` uses Simmer's LMSR paper venue with real market data for dogfooding before real money.

## Testing

```bash
export SIMMER_API_KEY=...
export TRADING_VENUE=sim    # Simmer LMSR paper, $SIM-denominated
python weather_ev_port.py --dry-run  # show opportunities, no writes
python weather_ev_port.py            # paper mode, local state, no SDK calls
python weather_ev_port.py --live     # real trades through SimmerClient
python weather_ev_port.py status     # local balance + open positions
python weather_ev_port.py report     # resolved-market breakdown
python weather_ev_port.py positions  # Simmer-side position snapshot
```

Unit tests on the math live in `tests/test_strategy.py`:

```bash
python -m pytest tests/
```

## Relation to the existing weather-trader

`polymarket-weather-trader` (v1.18.x) uses threshold-based entry and delegates stop-loss to the server-side risk monitor. This skill is a distinct strategy: EV-gated, Kelly-sized, in-code trailing stops. Both skills can coexist; they trade different edges.

## Attribution

See `ATTRIBUTION.md` and `LICENSE.UPSTREAM`. Strategy math + forecast pipeline + airport-station city table are derived from [alteregoeth-ai/weatherbot](https://github.com/alteregoeth-ai/weatherbot) (MIT License).
