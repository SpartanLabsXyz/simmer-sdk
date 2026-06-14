"""Tests for the `simmer` CLI (SIM-3070 slice 4).

Arg-validation paths run in CI with no extra. The `--demo` end-to-end run is
gated on the [backtest] extra (it spins up the replay server + subprocesses)
but needs no network or tape download — it's the hermetic offline smoke.
"""

import json

import pytest

from simmer_sdk import cli


# -- arg validation (CI-safe, no engine import reached) -----------------------

def test_backtest_requires_inputs_or_demo(capsys):
    rc = cli.main(["backtest"])
    assert rc == 2
    assert "required" in capsys.readouterr().err


def test_window_flag_is_not_yet_supported(capsys):
    rc = cli.main(["backtest", "./b", "--entrypoint", "r.py", "--tape", "./t",
                   "--t0", "2026-03-01", "--t1", "2026-03-02", "--window", "30d"])
    assert rc == 2
    assert "slice 5" in capsys.readouterr().err


def test_version_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "simmer-sdk" in capsys.readouterr().out


def test_no_subcommand_errors():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code != 0


# -- demo end-to-end (gated on the [backtest] extra) --------------------------

def test_demo_runs_offline(tmp_path, capsys):
    pytest.importorskip("duckdb", reason="requires the [backtest] extra")
    pytest.importorskip("fastapi", reason="requires the [backtest] extra")
    out = tmp_path / "report.json"
    rc = cli.main(["backtest", "--demo", "--out", str(out)])
    assert rc == 0, "demo run should exit clean"

    printed = capsys.readouterr().out
    assert "backtest summary" in printed
    assert "hit rate" in printed

    report = json.loads(out.read_text())
    s = report["summary"]
    # the demo skill buys YES favorites; the 10-market slice has a YES/NO mix,
    # so it must place real trades and settle them (not the empty 0-trade state).
    assert s["trades"] > 0
    assert s["settlements"] > 0
    assert report["bundle"]["clean"] is True
    # hit_rate must be sane (engine snaps resolved outcomes to {0,1}, so a
    # losing YES is NOT counted as a win — pre-fix this read ~1.0)
    assert 0.0 <= s["hit_rate"] <= 1.0


def test_demo_without_out_writes_no_file(tmp_path, monkeypatch, capsys):
    pytest.importorskip("duckdb", reason="requires the [backtest] extra")
    pytest.importorskip("fastapi", reason="requires the [backtest] extra")
    monkeypatch.chdir(tmp_path)
    rc = cli.main(["backtest", "--demo"])
    assert rc == 0
    # running --demo must NOT silently drop a report file into the user's CWD
    assert not list(tmp_path.glob("*.json")), "--demo wrote a file without --out"
    assert "--out" in capsys.readouterr().out  # hints how to save it
