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
- **Magnitude gate**: `|momentum| >= 0.10%`. This is the load-bearing invariant. At 0.10% the strategy shows 89.4% win rate; below this threshold it enters the noise zone (structurally negative EV after fees).
- **Signal source**: Binance 1m candle at window-open (last complete 1m candle). Do NOT switch to 5m lookback without re-running the backtest.
- **Sizing table**: 3-tier conviction ladder. T1 $3 / T2 $5 / T3 $10. Tier thresholds: 0.10%/0.20%/0.35%.
- **Fee formula**: `fee = shares × 0.07 × p × (1-p)`. Crypto taker category.
- **Hold policy**: hold to expiry. No exit logic — results are binary.

## Backtest source

- Evidence: `simmer/_dev/active/_fast-loop-rebuild/backtest_binance_ladder.md` (30d BTC fast-5m, 218 markets)
- Cohort profiling: `profile_winners.md` (ndjjwobaq, btcbeliver01 timeline analysis)
- Parent audit: SIM-1854 close-out comment (2026-05-18)
