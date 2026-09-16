# Changelog — polymarket-weather-trader

## [1.23.12] - 2026-09-16

### Fixed
- Replay forecast archive builds now retry transient Open-Meteo 5xx/transport failures up to 3 attempts with backoff while still fail-closing immediately on 4xx responses.
- Long Previous Runs windows are fetched in 10-day chunks and merged internally. Each station asserts `utc_offset_seconds` is stable across chunks before writing merged data.
- Added `--stations us|intl|all` to the archive builder. `us` is limited to the 8 configured Polymarket US resolution stations, avoiding international DST checks and unnecessary requests for US-only replay backtests.
- Archive builds now request Previous Runs leads 1-3 and record lead/chunk/filter/offset provenance in `_meta`.

## [1.23.11] - 2026-09-16

### Added
- Added `scripts/build_replay_forecast_archive.py`, which builds `fixtures/replay_forecasts.json` from Open-Meteo Previous Runs daily day-ahead max/min temperatures for every configured US and international resolution station.
- Weather replays now load the bundled forecast archive when `SIMMER_REPLAY=1`, ignore the `_meta` provenance block as data, and print the archive path, station count, and date span before evaluating markets.
- Added recorded-response unit tests for the archive builder and loader metadata handling.

## [1.23.10] - 2026-09-15

### Docs
- Moved inline version history out of `SKILL.md`; behavior changes now live in this changelog.
- Clarified that `SIMMER_WEATHER_EXIT_THRESHOLD` owns the entry/exit threshold interaction, and safety rails point there instead of repeating it.
- Clarified that `SIMMER_WEATHER_MIN_HOURS_TO_RESOLVE=24` widens discovery to +1/+2-day markets during morning heartbeats.
- Reframed `--no-safeguards` as a backtest-only escape hatch.


## [1.23.9] - 2026-09-15

### Fixed
- Discovery now searches tomorrow and the following day before the legacy broad city query. This prevents morning heartbeats with `SIMMER_WEATHER_MIN_HOURS_TO_RESOLVE=24` from seeing only same-day markets that are then correctly rejected as too soon.

## [1.23.8] - 2026-09-08

### Added
- Added 26 Polymarket weather resolution stations: `KAUS`, `KHOU`, `KBKF`, `ZBAA`, `ZSPD`, `ZGGG`, `ZGSZ`, `ZUUU`, `ZUCK`, `ZHHH`, `ZSQD`, `ZHCC`, `WSSS`, `WMKK`, `RPLL`, `RKPK`, `CYYZ`, `SAEZ`, `SBGR`, `MMMX`, `FACT`, `EFHK`, `OEJN`, `EPWA`, `LFPB`, and `MPMG`. US stations route through NOAA with Open-Meteo cross-checks; international stations route through exact airport coordinates instead of city centers.

## [1.23.7] - 2026-09-08

### Added
- `SIMMER_WEATHER_MIN_ENTRY_PRICE` (default `0` = off). Rejects entries below this mid so a raised `ENTRY_THRESHOLD` (upper bound only) cannot buy lottery tickets. Same entry-price check as before; this is the missing floor.
- `SIMMER_WEATHER_MIN_HOURS_TO_RESOLVE` (default `2`). Env override of the hardcoded time-decay constant inside `check_context_safeguards`. Entry-only: exits keep the original 2h floor. No new gate, no calendar-day skip.

### Docs
- `ENTRY_THRESHOLD` is documented as an upper bound only. Raising it above the `0.45` exit default will self-exit unless `EXIT_THRESHOLD` is raised too.

## [1.23.6] - 2026-09-06

### Fixed
- **Polymarket reworded every weather-temperature market on 2026-08-22 and the station parser stopped reading all of them.** The criteria dropped their Wunderground URL (resolution source moved to NOAA) and gained an agency clause — `recorded BY NOAA at the LaGuardia Airport Station`. Both parser patterns matched neither, so `parse_resolution_station()` returned `None` on 100% of current criteria and the skill skipped every event without entering. Verified against the live corpus: 2,195 of 2,596 active weather-temperature markets carry the new wording; only 84 keep the old. The station phrase now accepts an optional, generic agency clause, so `by NWS` or `by the National Weather Service` will not break it again.
- Added name aliases for three stations Polymarket cites under a different name than our coordinate tables hold: `Hartsfield-Jackson International Airport` → `KATL`, `Amsterdam Airport Schiphol` → `EHAM`, `Malpensa Intl Airport` → `LIMC`. With these, all 16 advertised locations route again — the six US cities in `SIMMER_WEATHER_LOCATIONS` plus every international alias. Aliases may only point at stations we already have coordinates for; routing a market to an airport we cannot forecast is the silent KDFW/KDAL failure mode.
- The skip log no longer claims `need SDK ≥ 2026-05-03` when criteria is present. Missing criteria and unreadable criteria were sharing one message that blamed a stale SDK for both; they are now distinct reasons.

### Added
- Parse-coverage guard. When a run reads a station out of fewer than 50% of the events that had criteria, the skill says so loudly (surviving `--quiet`) instead of reporting a clean scan. A parser that falls behind upstream wording throws nothing, fails no trade and retries nothing, so every downstream health metric stays green while the skill quietly stops entering — this was invisible for two weeks. The warning names the real cause: it is not finding no opportunities, it is failing to look.
- `parse_resolution_station_result(criteria)` — returns the station plus the reason it could not be read (`SKIP_MISSING_CRITERIA` / `SKIP_UNPARSEABLE_CRITERIA`). `parse_resolution_station()` is unchanged for existing callers.
- Whitespace in the station phrase is collapsed before matching, so the pattern uses literal single spaces. The first cut used `\s+` around the lazy capture, which let the engine repartition a whitespace run on every failure — `recorded at the` followed by 1,600 spaces took 6.8s, growing ~8x per doubling. Criteria text is authored upstream, so one malformed market would have stalled the whole scan. Collapsing first also means a station phrase wrapped across newlines now parses, which the old pattern could not do.
- `tests/test_resolution_criteria_reword.py` — pins the new and old wording, the reason split, alias-to-coordinate integrity, and the coverage guard's thresholds against live criteria strings.

### Thanks
- Reported by the Grok Bot dogfood seat, which caught it on a clean v1.23.5 install and correctly identified that the log message was lying about the cause.


## [1.23.4] - 2026-07-22

### Fixed
- Added `EGLC` (London City Airport) to the international station coordinate map so Polymarket London weather markets that cite the official London City station can route to Open-Meteo instead of fail-closing as an unsupported station. This does not change markets whose Simmer/SDK metadata lacks usable `resolution_criteria`; those still fail closed.
- `order_type=FOK` (Fill Or Kill) is now overridden to GTC, the same way `FAK` has been since v1.20.0. Weather markets are structurally illiquid — both FOK and FAK orders are cancelled immediately with no fill, creating a retry-loop that burns attempts on every run. The warning message now names the actual configured type (`FAK` or `FOK`) so it's actionable.

## [1.23.3] - 2026-07-07

### Fixed
- Event grouping now keys on `event_ref` (the canonical parent-event id, present on every market) instead of the legacy `event_id`, which SDK-imported markets historically lacked. Fixes temperature buckets silently dropping out of their event group when `event_id` came back null.

## [1.22.2] - 2026-05-24

### Fixed
- Intl markets where Polymarket cites the resolution station by NAME only (no Wunderground URL / no ICAO) are no longer silently skipped (SIM-2428). Added a normalized-name → ICAO fallback index covering all 21 US + 16 intl stations. Normalizer strips diacritics (`ğ→g`), trailing `Intl` / `International` / `Airport` tokens, and lowercases — so `Esenboğa Intl Airport`, `Esenboga International Airport`, and `Esenboga Intl Airport` all resolve to `LTAC`.
- Added `name` field to all 16 `INTERNATIONAL_STATION_COORDS` entries (US table already had it). The name-index is built from both tables at module load.

### Added
- `resolve_station_id_from_name(station_name)` — public helper for name-to-ICAO lookup.
- `_normalize_station_name(name)` — internal normalizer used by the index + resolver.

### Behavior delta
Routing logic now: (a) if `station_id` is present in maps → as before; (b) if `station_id is None` but `station_name` matches an index entry → resolved and logged; (c) otherwise skip with the same message as before. No behavior change for markets that already resolve via ICAO.

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

## [1.21.0] - 2026-05-03

### Added
- Per-market resolution source routing. Each market is routed to the specific weather station Polymarket reads, parsed from `resolution_criteria`, instead of a hardcoded city-to-station map. Unknown stations are skipped with a log line.
- Expanded NOAA coverage: KLGA, KJFK, KEWR, KNYC, KORD, KMDW, KSEA, KATL, KDAL, KDFW, KMIA, KBOS, KDCA, KIAD, KPHX, KLAS, KSFO, KLAX, KDEN, KMSP, KPHL.
- Expanded international coverage: Madrid, Milan, Amsterdam, and Taipei.

### Changed
- Requires the `?include=resolution_criteria` flag on `/api/sdk/markets` (live on Simmer backend 2026-05-03).

## [1.20.1] - 2026-04-24

### Docs
- Surfaced safety rails at the top: paper-default, `--live` requirement, configurable caps, server-side risk monitor, strategy-side safeguards, and reversibility.
- Genericized risk-monitor framing around configurable user settings.
- Pointed wallet setup to `docs.simmer.markets/wallets`.

## [1.20.0] - 2026-04-20

### Changed
- Uses `SimmerClient.from_env()` from `simmer-sdk>=0.13.0`, auto-reading `SIMMER_API_KEY` and raising a dashboard-linked `RuntimeError` if unset.
- Trimmed duplicated wallet-setup, changelog, and decorative content as part of the slim per-skill catalog reshape.
- Removed retired `AUTOMATON_*` env reads.
