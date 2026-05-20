# NEXT — polymarket-fast-scaler

**Status**: B3 live test in progress — started 2026-05-20T15:32Z, target end 2026-05-22T15:32Z.

B1 ✅ shipped | B2 ✅ paper validation passed (SIM-1997, PR #115) | B3 🔄 live test running

## B3 live test

- Cron: `* * * * *` on `~/simmer-workspace/live-test/polymarket-fast-scaler/run_live_test.sh`
- Log: `~/simmer-workspace/live-test/polymarket-fast-scaler/live_test_log.jsonl`
- Config: `daily_budget_usd=10.0`, all other defaults
- Paper validation cron removed; replaced with live test cron

## Publish-ready branch

- Branch: `feat/sim-1998-fast-scaler-v1.0.0` on simmer-sdk
- Changes: version 0.9.0 → 1.0.0, `status: scaffold` removed, `published: true`
- PR creation pending PAT scope fix (adlai88 GH_TOKEN lacks createPullRequest on sdk repo)
- After 48h test passes: create PR, merge, run `python scripts/publish.sh polymarket-fast-scaler`

## Strategy invariants (DO NOT change without re-running the backtest)

- **Side-picker**: direction only — up → YES, down → NO. No divergence filter.
- **Magnitude gate**: `|momentum| >= 0.10%`. This is the load-bearing invariant.
- **Signal source**: Binance 1m candle at window-open (last complete 1m candle).
- **Sizing table**: 3-tier conviction ladder. T1 $3 / T2 $5 / T3 $10. Tier thresholds: 0.10%/0.20%/0.35%.
- **Fee formula**: `fee = shares × 0.07 × p × (1-p)`. Crypto taker category.
- **Hold policy**: hold to expiry. No exit logic — results are binary.

## Backtest source

- Evidence: `simmer/_dev/active/_fast-loop-rebuild/backtest_binance_ladder.md` (30d BTC fast-5m, 218 markets)
- Cohort profiling: `profile_winners.md` (ndjjwobaq, btcbeliver01 timeline analysis)
- Parent audit: SIM-1854 close-out comment (2026-05-18)
