---
name: backtest-demo-favorites
description: Tiny demo skill bundled with `simmer backtest --demo`. Buys YES on liquid favorites to exercise the full backtest pipeline offline.
metadata:
  version: 1.0.0
---

# Backtest demo — favorites buyer

This is **not** a real trading strategy. It exists so `simmer backtest --demo`
produces a meaningful report (decisions, trades, settlements, hit_rate, pnl,
baselines) with no network access and no tape download.

Each tick it buys a small fixed amount of YES on the most liquid markets
trading as favorites (YES price in a mid-to-high band), skipping anything it
already holds. Run against the bundled 10-market demo slice, a handful of those
favorites resolve YES (wins) and a handful resolve NO (losses), so the demo
shows a realistic mixed outcome rather than "0 trades, stayed flat".

Entrypoint: `favorites_demo.py` (reads `SIMMER_API_URL` / `SIMMER_API_KEY` from
the replay harness; accepts and ignores `--live` / `--quiet`).
