# Changelog — polymarket-btc-up-down-trader

## [1.2.0] - 2026-09-16

### Added
- **Replay plane for markets + prices + clock (SIM-5442).** Under `SIMMER_REPLAY=1` the skill lists BTC Up/Down dailies from the Simmer tape (`GET /api/sdk/markets?q=up or down`, no `tags`/`status` — those 422 on replay). Horizon uses `SIMMER_REPLAY_NOW`. Prices come from tape `yes_price` / `current_probability`. Live Gamma and CLOB midpoint stay dark. Volume-spike exits are live-only (CLOB trades would be look-ahead). Replay trades pass `skip_preflight=True` so `WALLET_UNVERIFIED` cannot block SimState fills.

### Fixed
- Failed `trade()` results no longer increment `daily_spend.json`. A miss must not burn the UTC-day budget.
- Discovery no longer look-aheads to today's live "Bitcoin Up or Down on …?" row while a full tape of historical dailies sits unused.
