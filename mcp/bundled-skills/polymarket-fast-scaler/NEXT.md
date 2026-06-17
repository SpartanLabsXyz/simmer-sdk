# NEXT — polymarket-fast-scaler

**Status**: B3 complete — live test passed. Published to ClawHub at v1.0.0.

B1 ✅ shipped | B2 ✅ paper validation passed (SIM-1997, PR #115) | B3 ✅ live test passed + published (SIM-1998)

## Live test results (2026-05-20 → 2026-05-22)

- 4 trades executed over 48h, all fills confirmed (exit_code=0)
- $12.00 total spend vs $10/day budget cap
- All trades Tier 1 ($3) with |momentum| 0.10–0.20%
- 2349 signals evaluated, no unexpected errors
- Budget cap respected per-day

## Strategy invariants (DO NOT change without re-running the backtest)

- **Side-picker**: direction only — up → YES, down → NO. No divergence filter.
- **Magnitude gate**: `|momentum| >= 0.10%`. A noise filter, not a validated edge. The original "89.4% win rate" claim was RETRACTED 2026-06-12 (look-ahead bias — see below).
- **Signal source**: Binance 1m candle at window-open (last complete 1m candle). Do NOT switch to 5m lookback without re-running the backtest.
- **Sizing table**: 3-tier conviction ladder. T1 $3 / T2 $5 / T3 $10. Tier thresholds: 0.10%/0.20%/0.35%.
- **Fee formula**: `fee = shares × 0.07 × p × (1-p)`. Crypto taker category.
- **Hold policy**: hold to expiry. No exit logic — results are binary.

## Backtest source — RETRACTED 2026-06-12 (look-ahead bias)

The SIM-3070 skill-replay calibration gate found the original backtest measured the
1m candle that *starts* at window-open (closes 60s into the window it predicts).
The live-actionable signal (prior complete candle) shows no measured edge (~49%,
coin flip). Writeup: `simmer/_dev/active/_skill-intelligence/pilot-reports/fast-scaler-calibration-gate.md`.

- Original evidence (now known look-ahead-biased): `simmer/_dev/completed/_fast-loop-rebuild/backtest_binance_ladder.md` (30d BTC fast-5m, 218 markets)
- Cohort profiling: `profile_winners.md` (ndjjwobaq, btcbeliver01 timeline analysis)
- Parent audit: SIM-1854 close-out comment (2026-05-18)
