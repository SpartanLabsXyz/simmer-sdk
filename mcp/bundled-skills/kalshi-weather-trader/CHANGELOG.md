# Changelog — kalshi-weather-trader

## [1.0.8] - 2026-05-23

### Fixed
- `check_exit_opportunities` no longer crashes with `TypeError: argument of type 'NoneType' is not iterable` when a position's `sources` field is `None` (e.g. paper-mode entries). Changed `pos.get("sources", [])` → `pos.get("sources") or []` so explicit `None` values are coalesced to `[]`. Closes SIM-2371.

## [1.0.7] - prior
- See git history.
