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

---

# Post-Investigation Update — SIM-2067

**Ticket:** SIM-2067
**Window:** continuing from the 24h DOD observation above; analysis through 2026-05-19T15:47Z.

## Status

RC1 fixed (auto_redeem throttle). RC2 is low-volatility market condition — gate calibration verified correct. Re-validation in progress.
## Root Cause Analysis

### RC1: no_markets blackout (12:25 May 18 → 01:00 May 19 UTC, ~780 min)

**Cause:** `auto_redeem()` making multiple sequential API calls to the Simmer backend, each with a 30s read timeout. On 2026-05-18 between ~12:25–01:00 UTC, the backend was responding slowly (partial headers, stalled body), preventing each API call from timing out in <30s. With 4+ sequential API calls (agents/me + positions + N×redeem), cumulative wall time hit exactly 210s per run — consuming the entire 1-minute cron cycle BEFORE `discover_fast_markets()` could execute.

**Evidence:** 772 of 775 no_markets runs show `duration_s=210-211` — a precision match for 7 API calls × 30s timeout. Before 12:25 UTC, runs took 12-41s. The instant jump to 210s at exactly 12:25 UTC indicates the backend degradation caused the blocking.

**Fix (applied):** Added `_should_run_auto_redeem()` cooldown — auto_redeem runs at most once per 10 minutes. The other 9 cycles/10min skip it entirely, spending < 5s on market discovery. A backend outage can no longer black out more than a single cron window per 10 minutes.

### RC2: below_magnitude_gate dominance (01:00–11:45 UTC May 19, ~645 min)

**Cause:** Genuine low BTC 1m volatility during Asian hours. 582/713 runs showed `momentum_pct=0.0` (i.e., |momentum| < 0.00005%); max observed was 0.0994% — just below the 0.10% gate.

**Not a bug.** The gate is correctly calibrated: the backtest gate of 0.10% yields 89.4% win rate. The strategy simply doesn't trade during low-volatility periods, which is correct behavior.

**Note on issue description:** The reported range "-10.36% to +9.94%" in SIM-2067 was a misread — `momentum_pct=0.0994` in the JSONL log means 0.0994%, not 9.94%. Actual observed range: -0.087% to +0.099%.

## Observed Gate Fires (running total)

| Timestamp (UTC) | Momentum | Dir | Tier | Amount |
|---|---|---|---|---|
| 2026-05-19T11:45Z | -0.1036% | NO | T1 | $3.00 |
| 2026-05-19T14:05Z | +0.1321% | YES | T1 | $3.00 |
| 2026-05-19T14:20Z | -0.1200% | NO | T1 | $3.00 |
| 2026-05-19T14:28Z | -0.1040% | NO | T1 | $3.00 |
| 2026-05-19T14:35Z | -0.1251% | NO | T1 | $3.00 |

All 5 fires are Tier 1 (0.10%–0.20% momentum). Gate passing during US market hours (10AM–10:35AM ET). Gate fires at 5/28h = ~4.3/day; within 3.5–10.5 target range.

## Skip Reason Breakdown (at 15:47 UTC May 19, 1675 runs)

| Reason | Count | % |
|---|---|---|
| no_markets | 775 | 46.3% |
| below_magnitude_gate | 748 | 44.7% |
| wide_spread | 98 | 5.9% |
| no_live_market | 41 | 2.4% |
| gate fire (trades_executed=1) | 5 | 0.3% |
| clob_price_unavailable | 4 | 0.2% |
| signal_fetch_failed | 2 | 0.1% |
| position_too_small | 2 | 0.1% |

The 775 no_markets entries are the RC1 blackout. Without the blackout, the below_magnitude_gate and gate fires would dominate — which is the expected distribution.

## Re-validation Status

Ongoing. After RC1 fix applied (2026-05-19T15:xx UTC), new runs are < 30s each. A fresh 24h window post-fix needs 3.5–10.5 gate fires to confirm DOD.

Current projected rate (5 fires in 14 effective trading hours): ~8.6/day. Within target.
