"""Unit tests for simmer_sdk.backtest.tape (SIM-3070 auto-fetch).

CI-safe: the pure helpers + the pre-network guards (bad window, past-coverage).
The actual HF fetch is network + minutes, so it's not exercised here.
"""

from datetime import datetime, timezone

import pytest

from simmer_sdk.backtest import tape as tp


def test_iso_parses_str_datetime_z_and_naive():
    assert tp._iso("2026-03-01") == datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert tp._iso("2026-03-01T00:00:00Z") == datetime(2026, 3, 1, tzinfo=timezone.utc)
    aware = datetime(2026, 3, 1, tzinfo=timezone.utc)
    assert tp._iso(aware) is aware
    naive = datetime(2026, 3, 1)
    assert tp._iso(naive) == aware


def test_cache_root_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SIMMER_TAPE_CACHE", str(tmp_path / "tapes"))
    assert tp.cache_root() == tmp_path / "tapes"
    assert tp.cache_root().is_dir()


def test_key_is_deterministic_and_param_sensitive():
    a = datetime(2026, 3, 1, tzinfo=timezone.utc)
    b = datetime(2026, 3, 8, tzinfo=timezone.utc)
    k = tp._key(a, b, 300, 1000.0)
    assert k == tp._key(a, b, 300, 1000.0)          # deterministic
    assert k != tp._key(a, b, 300, 2000.0)          # min_volume changes it
    assert k != tp._key(a, b, 100, 1000.0)          # max_markets changes it
    assert k != tp._key(a, datetime(2026, 3, 9, tzinfo=timezone.utc), 300, 1000.0)


def test_fetch_rejects_inverted_window(tmp_path):
    pytest.importorskip("duckdb", reason="requires the [backtest] extra")
    with pytest.raises(tp.TapeFetchError, match="must be after"):
        tp.fetch_tape("2026-03-08", "2026-03-01", cache_dir=str(tmp_path))


def test_fetch_rejects_window_past_dataset_coverage(tmp_path):
    pytest.importorskip("duckdb", reason="requires the [backtest] extra")
    # window starts after the dataset ends → clear error, no network attempted
    with pytest.raises(tp.TapeFetchError, match="ends"):
        tp.fetch_tape("2026-08-01", "2026-08-08", cache_dir=str(tmp_path))
