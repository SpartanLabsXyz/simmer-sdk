"""Unit tests for simmer_sdk.backtest.run_backtest (SIM-3070 slice 3).

Two tiers:

* Pure input-parsing + sdk-path resolution — always run in CI (no extra, no
  network, no fixtures).
* End-to-end replay of a real bundle over a local tape slice — gated on
  SIMMER_BACKTEST_TAPE + SIMMER_BACKTEST_BUNDLE (the tape parquets are 100MB+
  and not in the repo). Locally:

      SIMMER_BACKTEST_TAPE=/tmp/seed-tape-neh-fresh \
      SIMMER_BACKTEST_BUNDLE=skills/polymarket-nothing-ever-happens \
      SIMMER_BACKTEST_ENTRYPOINT=nothing_ever_happens.py \
      python -m pytest tests/test_backtest_run.py -v
"""

import os
from datetime import datetime, timedelta, timezone

import pytest

import simmer_sdk.backtest as bt
from simmer_sdk.backtest import (
    BacktestError,
    resolve_sdk_path,
    run_backtest,
)


# -- pure parsing (CI-safe) ---------------------------------------------------

def test_parse_dt_naive_string_pins_utc():
    dt = bt._parse_dt("2026-03-01")
    assert dt == datetime(2026, 3, 1, tzinfo=timezone.utc)
    # byte-identical isoformat to the internal CLI's _dt — config_hash depends on it
    assert dt.isoformat() == "2026-03-01T00:00:00+00:00"


def test_parse_dt_passthrough_datetime_and_tz_convert():
    aware = datetime(2026, 3, 1, 12, tzinfo=timezone(timedelta(hours=8)))
    assert bt._parse_dt(aware) == datetime(2026, 3, 1, 4, tzinfo=timezone.utc)


def test_parse_dt_offset_string_forces_utc_for_config_hash_parity():
    # an offset-bearing STRING must mirror the internal _dt EXACTLY: force the
    # tz to UTC (keep wall-clock), do NOT convert — else config_hash desyncs from
    # scripts/replay_run.py. (Contrast the aware-DATETIME case above, which converts.)
    assert bt._parse_dt("2026-03-01T00:00:00+05:00") == datetime(2026, 3, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("value,seconds", [
    ("90s", 90),
    ("15m", 900),
    ("12h", 43200),
    ("30d", 2592000),
    ("720", 43200),          # bare number = minutes
    (720, 43200),            # int minutes
    (timedelta(hours=2), 7200),
])
def test_parse_cadence(value, seconds):
    assert bt._parse_cadence(value).total_seconds() == seconds


def test_parse_cadence_rejects_garbage():
    with pytest.raises(BacktestError):
        bt._parse_cadence("soon")


def test_normalize_args():
    assert bt._normalize_args(None) is None
    assert bt._normalize_args("--live --quiet") == ["--live", "--quiet"]
    assert bt._normalize_args(["--live"]) == ["--live"]
    assert bt._normalize_args("") is None


def test_resolve_sdk_path_points_at_dir_containing_package():
    sdk_path = resolve_sdk_path()
    # the subprocess does `import simmer_sdk` with PYTHONPATH=sdk_path
    assert os.path.isdir(os.path.join(sdk_path, "simmer_sdk"))


def test_resolve_sdk_path_explicit_wins():
    assert resolve_sdk_path("/some/where") == os.path.abspath("/some/where")


# -- validation errors (CI-safe; fail before the engine import is needed) -----

def test_run_backtest_rejects_missing_bundle(tmp_path):
    # run_backtest checks for the [backtest] extra before it reaches the
    # bundle-dir validation, so skip cleanly when the extra isn't installed —
    # mirrors test_run_backtest_rejects_missing_{entrypoint,tape} below.
    pytest.importorskip("duckdb", reason="requires the [backtest] extra")
    with pytest.raises(BacktestError, match="bundle dir not found"):
        run_backtest(str(tmp_path / "nope"), entrypoint="x.py",
                     tape=str(tmp_path), t0="2026-03-01", t1="2026-03-02")


def test_run_backtest_rejects_missing_entrypoint(tmp_path):
    # the [backtest] extra must be present for run_backtest to reach the
    # bundle-validation branch; skip cleanly if it isn't.
    pytest.importorskip("duckdb", reason="requires the [backtest] extra")
    (tmp_path / "SKILL.md").write_text("---\nversion: 1.0.0\n---\n")
    with pytest.raises(BacktestError, match="entrypoint"):
        run_backtest(str(tmp_path), entrypoint="ghost.py",
                     tape=str(tmp_path), t0="2026-03-01", t1="2026-03-02")


def test_run_backtest_rejects_missing_tape(tmp_path):
    pytest.importorskip("duckdb", reason="requires the [backtest] extra")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "run.py").write_text("print('hi')\n")
    with pytest.raises(BacktestError, match="missing tape file"):
        run_backtest(str(bundle), entrypoint="run.py",
                     tape=str(tmp_path / "empty"), t0="2026-03-01", t1="2026-03-02")


# -- end-to-end (gated on local fixtures) -------------------------------------

_TAPE = os.environ.get("SIMMER_BACKTEST_TAPE")
_BUNDLE = os.environ.get("SIMMER_BACKTEST_BUNDLE")
_ENTRY = os.environ.get("SIMMER_BACKTEST_ENTRYPOINT", "nothing_ever_happens.py")
_T0 = os.environ.get("SIMMER_BACKTEST_T0", "2026-04-28")
_T1 = os.environ.get("SIMMER_BACKTEST_T1", "2026-05-05")


@pytest.mark.skipif(
    not (_TAPE and _BUNDLE),
    reason="set SIMMER_BACKTEST_TAPE + SIMMER_BACKTEST_BUNDLE for the e2e run",
)
def test_run_backtest_end_to_end():
    pytest.importorskip("duckdb", reason="requires the [backtest] extra")
    report = run_backtest(
        _BUNDLE, entrypoint=_ENTRY, tape=_TAPE,
        t0=_T0, t1=_T1, cadence="12h",
        # NEH reads no candles; skip the plane so the test is hermetic + fast.
        candles=False,
    )
    # shape contract
    for key in ("summary", "baselines", "decisions", "fills", "equity_curve",
                "realism_gaps", "data_plane", "reproducibility", "bundle",
                "coverage_ok", "replay_job"):
        assert key in report, f"missing report key {key}"
    assert report["bundle"]["clean"] is True, "bundle had failed ticks"
    assert report["summary"]["ticks"] > 0
    # config_hash is deterministic for the same inputs
    again = run_backtest(_BUNDLE, entrypoint=_ENTRY, tape=_TAPE,
                         t0=_T0, t1=_T1, cadence="12h", candles=False)
    assert (report["reproducibility"]["config_hash"]
            == again["reproducibility"]["config_hash"])
    assert report["summary"]["pnl"] == again["summary"]["pnl"]


@pytest.mark.parametrize("value,expected", [
    ("2026-10-20", "2026-10-20"),
    ("2026-10-20T13:00:00+00:00", "2026-10-20T13:00:00+00:00"),
    (None, "2026-10-21"), ("invalid", "2026-10-21"),
    ("2026-10-19", "2026-10-21"), ("2026-10-18", "2026-10-21"),
    ("2026-10-21", "2026-10-21"), ("2026-10-22", "2026-10-21"),
])
def test_served_t1(value, expected):
    assert bt._served_t1(bt._parse_dt("2026-10-19"), bt._parse_dt("2026-10-21"), {"t1": value}) == bt._parse_dt(expected)


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("served", ["2026-10-20", "2026-10-20T13:00:00+00:00"])
def test_run_backtest_served_window(tmp_path, monkeypatch, capsys, local, served):
    import json
    from simmer_sdk.backtest import tape as tp
    pytest.importorskip("duckdb", reason="requires the [backtest] extra")
    pytest.importorskip("fastapi", reason="requires the [backtest] extra")
    from simmer_sdk.backtest.replay import duckdb_store, harness

    (tmp_path / "run.py").write_text("")
    for name in ("markets.parquet", "quant.parquet"):
        (tmp_path / name).touch()
    (tmp_path / "manifest.json").write_text(json.dumps({"t1": served}))
    monkeypatch.setattr(tp, "fetch_tape", lambda *a, **k: str(tmp_path))
    class Store:
        def __init__(self, *args):
            pass
        def close(self):
            pass
    monkeypatch.setattr(duckdb_store, "DuckDBStore", Store)
    configs = []
    def replay(store, bundle, entrypoint, config, sdk_path, **kwargs):
        configs.append(config)
        return {"summary": {}}
    monkeypatch.setattr(harness, "replay_bundle", replay)
    report = run_backtest(str(tmp_path), entrypoint="run.py",
                          tape=str(tmp_path) if local else None,
                          t0="2026-10-19", t1="2026-10-21", candles=False)
    expected = bt._parse_dt("2026-10-21" if local else served)
    assert configs[0].t1 == expected
    assert report["replay_job"]["t1"] == expected.isoformat()
    err = capsys.readouterr().err
    if local:
        assert err == ""
    else:
        assert err == f"note: tape service served data through {expected.isoformat()}; window clamped from 2026-10-21T00:00:00+00:00\n"
