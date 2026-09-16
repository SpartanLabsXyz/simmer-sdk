#!/usr/bin/env python3
"""SIM-5428: run the weather backtest/replay gate.

This is the agent-facing glue. It does not invent a second harness. It runs
the pinned replay discovery+entry tests that already sit next to the skill
(`tests/test_replay_discovery.py`) and prints keep / kill / fix rules for
the real-capital path.

90-day real-capital P&L on Simmer is the lock. Dogfood's MIN_HOURS=12 canary
is a separate seat — this script does not change it.

Usage (from repo root or this skill dir):

    python skills/polymarket-weather-trader/scripts/run_backtest_gate.py

Optional full-tape read (needs `pip install 'simmer-sdk[backtest]'`, a
weather-capable slice — `--q temperature`, low `--min-volume` — and a
forecast archive that covers the window). The harness strips host env;
put the JSON in the skill dir so the bundle copy sees it:

    cp /path/to/window-archive.json \\
        skills/polymarket-weather-trader/fixtures/replay_forecasts.json
    simmer backtest skills/polymarket-weather-trader \\
        --entrypoint weather_trader.py --window 30d --q temperature \\
        --min-volume 0 --out /tmp/wx-bt.json

Exit 0 if the pinned tests pass. Exit 1 if they fail. That is the gate.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
TEST_FILE = SKILL_DIR / "tests" / "test_replay_discovery.py"

# Pass/fail for the money path. Pinned here so an agent can read them
# without opening SKILL.md. 90d Simmer P&L remains the lock.
KEEP_KILL = """
KEEP / KILL / FIX  (weather skill, real-capital path)
====================================================
This gate is a fast filter. It does not replace 90-day real-capital P&L
on Simmer. Dogfood MIN_HOURS=12 is a separate canary — not this gate.

FIX  (do not add capital; repair the skill or the tape)
  - Discovery 422s on tags/status, or a listing failure exits 0 / paints
    bundle.clean on a 0-eval tick.
  - Horizon uses wall clock under replay (April tape dated 2027, 0 events).
  - Replay yes_price ignored (defaults to 0.50 → never enters).
  - Missing resolution_criteria skips every event and no city fallback.
  - Live NOAA/Open-Meteo fires under replay (look-ahead).
  - Preflight WALLET_UNVERIFIED blocks SimState fills.
  - Weather-capable tape + path green + 0 entries because the forecast
    archive is missing, empty, or does not cover the tape dates
    (SIMMER_REPLAY_FORECASTS / fixtures/replay_forecasts.json) — that is
    a missing forecast plane, not "no edge".
  - HF volume slice with 0 temperature markets — use --q temperature and
    a lower --min-volume. Do not treat an empty high-volume slice as kill.

KILL  (do not add more real capital)
  - Pinned pytest gate fails.
  - Path is green on a weather-capable tape, evals > 0, and the skill
    still cannot reach execute_trade when a forecast is injected or
    loaded from the archive.
  - After the path works: backtest P&L after costs is clearly ≤ 0 on a
    weather-capable tape with an honest forecast (not live NOAA).

KEEP  (provisional — 90d Simmer P&L is still the lock)
  - Full-tape `simmer backtest ... --q temperature` with evals > 0 AND
    entries > 0 AND an honest forecast (not live NOAA).
  - Pinned unit tests passing is a path check only. It is not KEEP.
  - Then, and only then, more real capital may be considered. The 90-day
    Simmer P&L lock still decides scale-up.
""".strip()


def main() -> int:
    print(KEEP_KILL, flush=True)
    print(flush=True)
    print(f"── pytest {TEST_FILE.relative_to(SKILL_DIR.parent.parent)} ──", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(TEST_FILE), "-q"],
        cwd=str(SKILL_DIR.parent.parent),
    )
    if result.returncode == 0:
        print(flush=True)
        print("PATH CHECKS PASS: pinned replay tests green. No keep/kill verdict yet — run the full-tape command and read the table.", flush=True)
    else:
        print(flush=True)
        print("GATE FAIL: pinned tests are red. Verdict: FIX or KILL — no more capital.", flush=True)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
