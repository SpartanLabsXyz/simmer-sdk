"""Local historical backtesting for Simmer trading skills (SIM-3070).

Backtest an UNMODIFIED skill bundle against historical prediction-market data
before risking capital — the missing "test before capital" leg alongside the
sim-venue, dry-run, and paper-trade modes (which are all live-forward).

The engine substrate under ``backtest.replay`` is vendored from the simmer
backend (see ``scripts/sync_replay_engine.py``); the same code path powers the
internal ``scripts/replay_run.py run-bundle``.

Requires the optional extra::

    pip install 'simmer-sdk[backtest]'

The public entrypoint ``run_backtest`` is added in slice 3.
"""
