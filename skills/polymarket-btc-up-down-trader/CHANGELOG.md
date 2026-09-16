# Changelog — polymarket-btc-up-down-trader

## [1.2.2] - 2026-09-16

### Fixed
- **Live NO `avg_cost` is held-side, not YES-scale (SIM-5442 F4).** `_normalize_open_position` preferred `avg_cost` over the `cost_basis` derivation. A NO bought at 0.30 (YES 0.70) stored `entry_price=0.30`, so `check_target_hit_exit` never fired when YES fell. Replay rows omit `avg_cost`, so a backtest looked right. Entry now comes from `cost_basis / shares` (NO: `1 - avg`). `avg_cost` is last-resort and is flipped for NO.

## [1.2.1] - 2026-09-16

### Fixed
- **Exit monitor no longer crashes on SDK `Position` (SIM-5442 hold-merge).** `get_open_positions` used `p.get("source")` on a dataclass. That `AttributeError` killed every tick after the first fill. The skill now reads Position attributes. Replay share-rows (`market_id`, `shares_yes`/`shares_no`, `cost_basis`, no `source`) normalize to side, size, and YES-scale entry (`cost_basis/shares`; NO is `1 - cost_basis/shares_no`). Missing `sources` under replay is this skill.

## [1.2.0] - 2026-09-16

### Added
- **Replay plane for markets + prices + clock (SIM-5442).** Under `SIMMER_REPLAY=1` the skill lists BTC Up/Down dailies from the Simmer tape (`GET /api/sdk/markets?q=bitcoin up or down`, no `tags`/`status` — those 422 on replay). Horizon uses `SIMMER_REPLAY_NOW`. Prices come from tape `yes_price` / `current_probability`. Live Gamma and CLOB midpoint stay dark. Volume-spike exits are live-only (CLOB trades would be look-ahead). Replay trades pass `skip_preflight=True` so `WALLET_UNVERIFIED` cannot block SimState fills.

### Fixed
- Failed `trade()` results no longer increment `daily_spend.json`. A miss must not burn the UTC-day budget.
- Discovery no longer look-aheads to today's live "Bitcoin Up or Down on …?" row while a full tape of historical dailies sits unused.
- **Exit monitor accepts SDK `Position` objects and replay share-rows.** `get_positions()` returns dataclasses (`shares_yes` / `shares_no` / `cost_basis` / `sources`), not dicts. Calling `p.get("source")` crashed after the first fill and killed every later tick. Replay rows have no `source`/`side`/`quantity`/`entry_price` — side and size come from shares; YES-scale entry is `cost_basis/shares` (NO: `1 - cost_basis/shares_no`). Under replay a missing `sources` list is this skill (the session only runs it).
- Unparseable `SIMMER_REPLAY_NOW` now raises `ReplayClockError` instead of falling through to wall clock (look-ahead).
- Replay listing `q` is `bitcoin up or down` so ETH/SOL/hourly rows do not burn the eval budget.
