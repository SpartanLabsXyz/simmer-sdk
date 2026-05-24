# Changelog — polymarket-weather-trader

## [1.22.1] - 2026-05-24

### Fixed
- `Matching bucket: None` log when a market's `outcome_name` field is explicitly `None` (matcher loop fell back to `question`, but post-selection line used `.get("outcome_name", "")` which returns `None` not the default). Same class-of-bug as SIM-2371. Closes SIM-2427 issue 1.
- Source-tier classification + log now runs for EVERY bucket-matched candidate, not only those that pass safeguards. Moved the cross-check fetch + `evaluate_source_agreement` call from inside the entry-threshold branch to immediately after bucket-match. Sizing application stays in the entry-threshold branch. Autoresearch + dogfood receipts now see would-have-been tier classification on slippage-blocked candidates too. Closes SIM-2427 issue 2.

### Cost note
The cross-check now fires on every bucket-matched candidate (vs. only entry-eligible ones in 1.22.0). `secondary_cache[station_id]` deduplicates within a scan run, so worst case = 1 Open-Meteo fetch per unique station per scan, unchanged from 1.22.0 for any candidate that reached entry-threshold evaluation. New cost: candidates above entry threshold now also incur the fetch — bounded by the per-station cache.

## [1.22.0] - 2026-05-24

### Added
- Multi-source bucket-confidence scoring (SIM-2420). Cross-checks NOAA primary against Open-Meteo secondary at the same station coords before sizing live entries. Four tiers:
  - `match` (same bucket) → normal size
  - `adjacent` (neighboring bucket, spread ≤ MAX_SOURCE_SPREAD_F) → cap to MAX_CANARY_USD (default $2)
  - `wide` (spread > MAX_SOURCE_SPREAD_F or non-adjacent buckets) → skip
  - `missing_secondary` (intl markets — Open-Meteo IS primary) → behave per REQUIRE_SOURCE_AGREEMENT
- Four new env knobs:
  - `SIMMER_WEATHER_REQUIRE_SOURCE_AGREEMENT` (default `false`)
  - `SIMMER_WEATHER_CANARY_ON_ADJACENT_DISAGREEMENT` (default `true`)
  - `SIMMER_WEATHER_MAX_CANARY_USD` (default `2.0`)
  - `SIMMER_WEATHER_MAX_SOURCE_SPREAD_F` (default `2.0`)
- `source_agreement` block added to trade signal payload (tier, primary/secondary temps, spread, secondary bucket).
- `get_openmeteo_forecast_for_us_station(station_id)` — returns Open-Meteo forecast at a NOAA-mapped US station's coords, converted to °F.
- 14 unit tests covering the 4 tier branches + edge cases (Celsius spread conversion, canary ceiling-not-floor behavior, etc.).

### Rationale
Polymarket weather markets have whole-degree buckets — a ~1°F source disagreement can flip the outcome. Prior versions sized fully whenever NOAA crossed an entry threshold. Herman's dogfood (Atlanta May 26 KATL) surfaced cases where NOAA placed the forecast in one bucket while Open-Meteo placed it in an adjacent or non-adjacent bucket; this release downgrades sizing tier in those cases instead of trading with full conviction.

## [1.21.2] - 2026-05-23

### Fixed
- `check_exit_opportunities` no longer crashes with `TypeError: argument of type 'NoneType' is not iterable` when a position's `sources` field is `None` (e.g. paper-mode entries). Changed `pos.get("sources", [])` → `pos.get("sources") or []` so explicit `None` values are coalesced to `[]`. Closes SIM-2371.

## [1.21.1] - prior
- See git history.
