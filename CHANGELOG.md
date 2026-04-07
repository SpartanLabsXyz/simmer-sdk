# Changelog

All notable changes to `simmer-sdk` are documented here.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.21] — 2026-04-07

### Added
- **`simmer_sdk.sizing`** — Kelly Criterion + Expected Value position sizing for binary prediction markets.
  - `size_position()` — dollar amount to trade, returns `0.0` when edge is below `min_ev` so skills can simply skip.
  - `kelly_fraction()`, `expected_value()` — raw primitives.
  - `SIZING_CONFIG_SCHEMA` — drop-in `CONFIG_SCHEMA` fragment exposing `SIMMER_POSITION_SIZING`, `SIMMER_KELLY_MULTIPLIER`, `SIMMER_MIN_EV` env vars.
  - Default is fractional Kelly (0.25x) to prevent overbetting. Sourced from research on top Polymarket traders (SIM-370).
- **`auto_redeem()`** wired into all official trading skills — winning Polymarket positions are now claimed automatically each cycle.

### Changed
- **`GammaClient` removed from the SDK.** The Polymarket Gamma helper has been relocated into the `polymarket-ai-divergence` skill (its only consumer). The SDK is scoped to the Simmer API surface plus universal primitives every skill needs (sizing, auth, error handling); third-party API helpers belong with the skills that use them. If you need Polymarket metadata directly, hit `https://gamma-api.polymarket.com/` from your skill — see `skills/building.mdx`.

### Docs
- README "Skill Builder Utilities" section covering `simmer_sdk.sizing`.
- New Mintlify page at `docs.simmer.markets/sdk/position-sizing`.
- `skills/building.mdx` "Recommended primitives" section pointing skill authors at `simmer_sdk.sizing`, with a note about external market data sources.

## [0.9.20] — Prior release

- `import_kalshi_event()` for bulk Kalshi event import.
- Tightened `py-order-utils` and `py-clob-client` minimum versions.
- Removed deprecated `get_skill_config` / `apply_skill_config`.
- Volatility targeting in `polymarket-weather-trader`.
