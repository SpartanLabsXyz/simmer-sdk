# Changelog — polymarket-nothing-ever-happens

## [1.1.0] - 2026-09-16

### Added
- **Replay plane for discovery + import + clock (SIM-5443).** Under `SIMMER_REPLAY=1` the skill lists standalone cheap-NO candidates from the Simmer tape (`GET /api/sdk/markets`, no `tags`/`status`/`q` — those 422 or over-narrow on replay). Daily-spend uses `SIMMER_REPLAY_NOW`. Import accepts replay `status=active` / `already_imported`. Live Gamma stays dark. Replay trades pass `skip_preflight=True` so `WALLET_UNVERIFIED` cannot block SimState fills.

### Fixed
- Sports no longer leak when the tape omits Gamma tags — question/slug tokens (`nba`, `nfl`, …) still drop. Ambiguous words (`college`, `sports`, `golf`) stay tag-only so "electoral college" / "sports betting" / "Trump golf" remain eligible.
- Unparseable `SIMMER_REPLAY_NOW` now raises `ReplayClockError` instead of falling through to wall clock (look-ahead).
