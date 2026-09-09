# Changelog — kalshi-weather-trader

## [1.0.12] - 2026-09-09

### Added
- `SIMMER_WEATHER_MIN_ENTRY_PRICE` (default `0` = off). Rejects entries below this mid so a raised `ENTRY_THRESHOLD` cannot buy lottery tickets.
- `SIMMER_WEATHER_MIN_HOURS_TO_RESOLVE` (default `2`). Env override for the entry time-decay safeguard. Entry-only: exits keep the original 2h floor.

### Fixed
- The local entry price band now runs before context and trend network calls, so below-floor candidates are rejected with zero context/history requests.

## [1.0.8] - 2026-05-23

### Fixed
- `check_exit_opportunities` no longer crashes with `TypeError: argument of type 'NoneType' is not iterable` when a position's `sources` field is `None` (e.g. paper-mode entries). Changed `pos.get("sources", [])` → `pos.get("sources") or []` so explicit `None` values are coalesced to `[]`. Closes SIM-2371.

## [1.0.7] - prior
- See git history.
