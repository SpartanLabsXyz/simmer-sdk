# Changelog — polymarket-btc-up-down-trader

## [1.2.0] - 2026-09-16

### Added
- **Replay plane for markets + prices + clock (SIM-5442).** Under `SIMMER_REPLAY=1` the skill lists BTC Up/Down dailies from the Simmer tape (`GET /api/sdk/markets?q=bitcoin up or down`, no `tags`/`status` — those 422 on replay). Horizon uses `SIMMER_REPLAY_NOW`. Prices come from tape `yes_price` / `current_probability`. Live Gamma and CLOB midpoint stay dark. Volume-spike exits are live-only (CLOB trades would be look-ahead). Replay trades pass `skip_preflight=True` so `WALLET_UNVERIFIED` cannot block SimState fills.

### Fixed
- Failed `trade()` results no longer increment `daily_spend.json`. A miss must not burn the UTC-day budget.
- Discovery no longer look-aheads to today's live "Bitcoin Up or Down on …?" row while a full tape of historical dailies sits unused.
- **Exit monitor accepts SDK `Position` objects and replay share-rows.** `get_positions()` returns dataclasses (`shares_yes` / `shares_no` / `cost_basis` / `sources`), not dicts. Calling `p.get("source")` crashed after the first fill and killed every later tick. Replay rows have no `source`/`side`/`quantity`/`entry_price` — side and size come from shares; YES-scale entry is `cost_basis/shares` (NO: `1 - cost_basis/shares_no`). Under replay a missing `sources` list is this skill (the session only runs it).
- Unparseable `SIMMER_REPLAY_NOW` now raises `ReplayClockError` instead of falling through to wall clock (look-ahead).
- Replay listing `q` is `bitcoin up or down` so ETH/SOL/hourly rows do not burn the eval budget.
