# Attribution

**This is a work-in-progress port. Not published. Not for distribution.**

## Upstream

Strategy math, market selection logic, forecast pipeline, and airport-station city table are derived from [alteregoeth-ai/weatherbot](https://github.com/alteregoeth-ai/weatherbot) (MIT License, Copyright (c) 2026 alteregoeth-ai). See `LICENSE.UPSTREAM`.

## What is derived

From `bot_v2.py`:

- `calc_ev`, `calc_kelly`, `bucket_prob`, `in_bucket`, `norm_cdf`, `bet_size` — math functions, ported line-for-line
- 20-city `LOCATIONS` table with airport station codes (`station`, `lat`, `lon`, `unit`, `region`)
- `TIMEZONES` per-city mapping
- Forecast fetchers: `get_ecmwf`, `get_hrrr`, `get_metar` via Open-Meteo + Aviation Weather APIs
- Market selection: `get_polymarket_event`, `parse_temp_range`, `hours_to_resolution`
- Entry filter stack: MIN_EV gate + Kelly sizing + spread + liquidity + max_price ceiling + pre-entry live-ask revalidation
- Exit logic: 20% stop-loss + trailing-stop-to-breakeven at +20% + forecast-change exit (>2°F / 1°C drift outside bucket)
- Per-market + per-state JSON storage layout under `data/markets/` and `data/state.json`
- Self-calibration sigma update protocol (MAE over ≥30 resolved markets per city+source)

## What is new

- Integration with Simmer SDK `SimmerClient.trade()` for order placement — AlterEgo's bot paper-trades only; no CLOB signing
- Order placement via PolyNode (Simmer's sole order router) with `sdk:weather-ev` source tag
- Position state reconciliation against Simmer's position store (in addition to local JSON)
- Simmer SDK config schema and env-var tunables for ClawHub autotune compatibility
- Paper mode default + `--live` flag following the Simmer skill convention
- `TRADING_VENUE=sim` support for $SIM-venue testing before real USDC

## Status of this port

Pre-response to outreach. Private branch. Placeholder slug `weather-ev-port`. Not for public distribution. Final naming, SKILL.md voice, and publishing path depend on AlterEgo's response to the outreach detailed in SIM-983.
