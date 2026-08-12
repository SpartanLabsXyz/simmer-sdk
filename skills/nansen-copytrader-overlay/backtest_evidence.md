# Nansen Copytrader Overlay Forward-Lift Gate

Date: 2026-08-12
Issue: SIM-4485
Code under test: `skills/nansen-copytrader-overlay/` at SDK commit `83efe4c`

## Verdict

**Publish gate remains closed.**

I could run the overlay bundle through the SIM-3070 backtest harness, but this
heartbeat did not produce a valid forward-lift measurement. The observed replay
result is **neutral/unevaluable**, not positive lift:

- `markets_traded`: 0
- `decisions`: 0
- `trades`: 0
- `settlements`: 0
- `pnl`: 0.0
- `hit_rate`: n/a
- `bundle.clean`: true in the credentials-probe run

This must not be used to flip the skill from research tooling to allocation
signal. A zero-trade replay of a signal-only overlay does not compare realised
copytrading returns against a baseline.

## What Was Run

Sanity tests for the overlay:

```bash
python3 -m pytest skills/nansen-copytrader-overlay/tests -q
```

Result:

```text
120 passed in 0.41s
```

Minimal SIM-3070 bundle probe after installing the local optional backtest deps
(`duckdb`, `fastapi`, `uvicorn`, `pyarrow`):

```bash
PYTHONPATH=. python3 - <<'PY'
from simmer_sdk.backtest import run_backtest

report = run_backtest(
    "skills/nansen-copytrader-overlay",
    entrypoint="nansen_skill_cli.py",
    tape="simmer_sdk/backtest/demo/tape",
    t0="2026-03-01",
    t1="2026-03-02",
    cadence="12h",
    args=(
        "copytrader-overlay --market-id demo "
        "--leaders /tmp/nansen-leaders-sim4485.json "
        "--max-calls 1 --max-wallets 1"
    ),
    sdk_path=".",
)
print(report["summary"])
print("bundle.clean=", report["bundle"]["clean"])
print("failed_ticks=", report["bundle"]["failed_ticks"])
print(report["bundle"]["tick_logs"][0]["stderr_tail"])
PY
```

Observed summary:

```text
{'markets_traded': 0, 'decisions': 0, 'trades': 0, 'settlements': 0,
 'hit_rate': None, 'pnl': 0.0, 'final_equity': 1000.0,
 'max_drawdown': 0.0, 'ticks': 3, 'evaluations': 0,
 'evaluations_exhausted': False}
bundle.clean= True
failed_ticks= 0
```

Relevant stderr from the replayed bundle:

```text
pnl_by_market fetch failed for market demo: NANSEN_API_KEY is not set
-- market-specific ranking unavailable this run, leaders keep their original score
[credits] 1/1 Nansen calls used this run
```

## Methodology Assessment

The current SIM-3070 harness is working as designed for executable trading
skills: it runs a bundle entrypoint on each frozen tick and measures fills,
settlements, PnL, drawdown, hit rate, and baselines.

The Nansen overlay is not itself an executable trading strategy. Its CLI ranks a
caller-provided leader list and exits; it never calls `SimmerClient.trade()`.
Therefore the replay harness can execute the bundle, but there is no realised
copytrading return to compare unless the run includes a copytrading execution
wrapper that:

1. Builds a historical leader candidate list at each decision point.
2. Calls the overlay with a real target market id and Nansen `pnl-by-market`
   access.
3. Executes the baseline top-N and overlaid top-N selections through the same
   fill/settlement path.
4. Records realised copy return for both arms on the same resolved market sample.

The local repository does not contain that historical leader sample or wrapper,
and the heartbeat environment does not include `NANSEN_API_KEY`.

## Required Unblock

Owner: board/CTO or whoever owns the Nansen data account and copytrading replay
sample.

Required action:

- Provide a Nansen API key with access to `prediction-market/pnl-by-market` for
  the replay run, or provide cached Nansen response fixtures for a defined
  resolved market sample.
- Provide or identify the historical copytrading leader candidate snapshots for
  the same sample: baseline `wallet_score`, proxy wallet, optional owner wallet,
  target market id, and realised copy outcome per leader.
- Then run the comparison: baseline top-N by `wallet_score` versus overlay top-N
  by `adjusted_score`, same markets, same fill model, same sizing.

## Status Decision

`SKILL.md` remains unchanged. Its Status section already says the skill is
research tooling and that no forward test has shown improved realised
copytrading returns. That is still the correct label.
