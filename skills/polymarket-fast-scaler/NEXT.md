# NEXT — polymarket-fast-scaler

**Status**: B1 (SDK wire-ups) shipped in PR — pending paper validation (B2) before live test (B3).

## Strategy invariants (DO NOT change without re-running the backtest)

- **Side-picker**: direction only — up → YES, down → NO. No divergence filter.
- **Magnitude gate**: `|momentum| >= 0.10%`. This is the load-bearing invariant. At 0.10% the strategy shows 89.4% win rate; below this threshold it enters the noise zone (structurally negative EV after fees).
- **Signal source**: Binance 1m candle at window-open (last complete 1m candle). Do NOT switch to 5m lookback without re-running the backtest.
- **Sizing table**: 3-tier conviction ladder. T1 $3 / T2 $5 / T3 $10. Tier thresholds: 0.10%/0.20%/0.35%.
- **Fee formula**: `fee = shares × 0.07 × p × (1-p)`. Crypto taker category.
- **Hold policy**: hold to expiry. No exit logic — results are binary.

## Open tickets

- **SIM-1971-B2** (parent: SIM-1971) — 24h paper-mode validation. Check gate fires ~7×/day; verify sizing; check caps respected.
- **SIM-1971-B3** (parent: SIM-1971) — Small-money live test ($10/day for 48h) + ClawHub publish at v1.0.0.

## Backtest source

- Evidence: `simmer/_dev/active/_fast-loop-rebuild/backtest_binance_ladder.md` (30d BTC fast-5m, 218 markets)
- Cohort profiling: `profile_winners.md` (ndjjwobaq, btcbeliver01 timeline analysis)
- Parent audit: SIM-1854 close-out comment (2026-05-18)

## Publish gate

B3 DOD: v1.0.0, status: scaffold removed, ≥3 installs in first 24h without errors.
