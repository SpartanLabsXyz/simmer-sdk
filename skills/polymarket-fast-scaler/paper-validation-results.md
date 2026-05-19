# polymarket-fast-scaler — 24h Paper Validation Results

**SIM-2008 DOD analysis**
Cron started: 2026-05-18T11:50 UTC
Window: 2026-05-18T11:50 → 2026-05-19T11:53 UTC (24h 3m)
Log: `~/simmer-workspace/paper-validation/polymarket-fast-scaler/paper_validation_log.jsonl`

---

## Verdict: DIVERGED

Gate fires: **1** in 24h  
Expected: **~7** (acceptable range 3.5–10.5, i.e. ±50%)

1 is 71% below the minimum acceptable threshold. **Investigation required before unblocking SIM-1998.**

---

## Summary Stats

| Metric | Value |
|---|---|
| Total cron runs | 1,444 |
| Runs/hour (steady state) | 60 (1-min interval) |
| Gate fires (trades_attempted > 0) | **1** |
| Trades executed | 1 |
| Total amount spent | $3.00 USD |
| Exit code 0 (clean runs) | 1,444 / 1,444 (100%) |

---

## Gate Fire Detail

| Field | Value |
|---|---|
| Timestamp | 2026-05-19T11:45:00Z |
| Tier | 1 |
| momentum_pct | -10.36% |
| amount_usd | $3.00 |
| trades_attempted | 1 |
| trades_executed | 1 |

Only one gate fire occurred, in the last 8 minutes of the observation window.

---

## Skip Reason Breakdown

| Skip Reason | Count | % |
|---|---|---|
| `no_markets` | 775 | 53.7% |
| `below_magnitude_gate` | 558 | 38.6% |
| `wide_spread` | 64 | 4.4% |
| `no_live_market` | 41 | 2.8% |
| `clob_price_unavailable` | 3 | 0.2% |
| `signal_fetch_failed` | 2 | 0.1% |
| *(gate fire)* | 1 | 0.1% |

---

## Temporal Pattern (critical finding)

The 24h window splits into two distinct regimes:

### Regime 1: 2026-05-18T12:00 → 2026-05-19T01:00 (~13 hours)
- Every single run returned `no_markets` (markets_found=0)
- 60/60 runs per hour, all blocking on `no_markets`
- **Complete market discovery blackout for 13 consecutive hours**

### Regime 2: 2026-05-19T01:00 → 2026-05-19T11:53 (~11 hours)
- `no_markets` drops to 0
- `below_magnitude_gate` becomes dominant (45–55/hr)
- `wide_spread` and `no_live_market` appear intermittently
- 1 gate fire at 11:45 UTC (Tier 1, -10.36% momentum, $3.00)

### Hourly profile

| Hour (UTC) | gate_fire | no_markets | below_mag | wide_spread | no_live |
|---|---|---|---|---|---|
| 2026-05-18T11 | 0 | 0 | 8 | 0 | 0 |
| 2026-05-18T12 | 0 | 35 | 22 | 2 | 0 |
| 2026-05-18T13–23 | 0 | 60 ea | 0 | 0 | 0 |
| 2026-05-19T00 | 0 | 60 | 0 | 0 | 0 |
| 2026-05-19T01 | 0 | 20 | 21 | 7 | 12 |
| 2026-05-19T02–10 | 0 | 0 | 42–56 | 2–11 | 0–10 |
| 2026-05-19T11 | **1** | 0 | 46 | 7 | 0 |

---

## Markets Found

For all 775 `no_markets` entries, `markets_found=0`. No market candidates were even returned before filtering in Regime 1.

---

## Momentum Distribution (when signals present)

- Entries with `momentum_pct` field: 553
- Non-zero momentum readings: 92
- Range: -10.36% to +9.94%
- Mean (non-zero): +0.54%

The single gate fire had momentum -10.36% — the largest absolute momentum observed in the window.

---

## Investigation Required

Two root causes need investigation:

### Issue 1: `no_markets` blackout (Regime 1, 13h)
- `markets_found=0` for 780+ consecutive minutes starting ~2026-05-18T12:13
- Possible causes: Polymarket API returned empty or malformed market list; market filter thresholds too strict for intraday liquidity; a specific query parameter changed at that time
- This alone prevented ~780 potential gate evaluation cycles

### Issue 2: `below_magnitude_gate` dominance (Regime 2)
- Even when markets are found and signals are generated, 45–56/hr fail the magnitude gate
- Only 1 pass in ~660 tries (0.15% pass rate during Regime 2)
- Expected ~7/day implies ~1/3.4h pass rate — actual is ~1/24h
- Possible causes: momentum threshold too high for current market conditions; tier cutoffs miscalibrated; Polymarket CLOB depth thinner than expected

**Recommendation:** Filed as investigation issue (SIM-2067) — see that ticket for root cause analysis plan.

---

## DOD Result

| DOD Item | Status |
|---|---|
| Read all JSONL entries | ✅ 1,444 entries |
| Count gate fires/day vs expected ~7 | ✅ 1 gate fire — **DIVERGED** |
| Summarize skip reasons | ✅ |
| Check markets_found / tier / amounts | ✅ |
| Write paper-validation-results.md | ✅ (this file) |
| Post summary to SIM-1997 | ✅ |
| If within ±50%: set SIM-1997 done | ⛔ diverged |
| If diverged: file investigation issue | ✅ SIM-2067 |
