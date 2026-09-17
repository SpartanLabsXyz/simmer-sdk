# Changelog — polymarket-nothing-ever-happens

## [1.1.1] - 2026-09-17

### Fixed
- **Live sizing cap is fail-closed (CTO P1).** A missing or non-numeric `max_safe_size` from `ensure_can_trade` now refuses the run. It no longer leaves `MAX_BET_USD` uncapped.
- **Replay standalone filter (CTO P1).** Listing rows with the same `event_id` (more than one on the page) are dropped, matching live Gamma `len(markets)==1`. The replay payload now includes `event_id` / `event_title`. Mirror that field in simmer_v3 `_market_payload` on the next `sync_replay_engine.py`. A missing `event_id` or a page that shows only one grouped leg is still FIX.
- Question/slug sports tokens run only under replay. Live stays tag/category.
- Import `status=active` / `already_imported` is replay-only.
- Empty `SIMMER_REPLAY_NOW` under replay raises `ReplayClockError` (no wall-clock fallback).

## [1.1.0] - 2026-09-16

### Added
- **Replay plane for discovery + import + clock (SIM-5443).** Under `SIMMER_REPLAY=1` the skill lists standalone cheap-NO candidates from the Simmer tape (`GET /api/sdk/markets`, no `tags`/`status`/`q` — those 422 or over-narrow on replay). Daily-spend uses `SIMMER_REPLAY_NOW`. Import accepts replay `status=active` / `already_imported`. Live Gamma stays dark. Replay trades pass `skip_preflight=True` so `WALLET_UNVERIFIED` cannot block SimState fills.

### Fixed
- Sports no longer leak when the tape omits Gamma tags — question/slug tokens (`nba`, `nfl`, …) still drop. Ambiguous words (`college`, `sports`, `golf`) stay tag-only so "electoral college" / "sports betting" / "Trump golf" remain eligible.
- Unparseable `SIMMER_REPLAY_NOW` now raises `ReplayClockError` instead of falling through to wall clock (look-ahead).
