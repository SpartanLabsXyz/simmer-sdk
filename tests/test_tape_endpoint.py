"""Tests for the endpoint-backed tape fetcher (SIM-3070 slice 5).

Mocks the backend tape service (POST /api/backtest/tape) + the presigned
downloads — no network, no bucket. Verifies request shape, caching, and error
mapping.
"""

import json
import os

import pytest

from simmer_sdk.backtest import tape as tp


class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise AssertionError("raise_for_status on error")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMMER_TAPE_CACHE", str(tmp_path))
    monkeypatch.setenv("SIMMER_API_KEY", "sk_live_test")
    return tmp_path


def _ok_body(key="abc123"):
    return {
        "key": key,
        "dataset_rev": "rev1",
        "t0": "2026-03-01", "t1": "2026-03-08",
        "markets": 12, "quant_rows": 3456,
        "expires_in": 3600, "cached": False,
        "urls": {
            "markets": "https://bucket/slices/%s/markets.parquet" % key,
            "quant": "https://bucket/slices/%s/quant.parquet" % key,
            "manifest": "https://bucket/slices/%s/manifest.json" % key,
        },
    }


def _patch_download(monkeypatch):
    """_download just writes a stub file so cache/existence logic is exercised."""
    def fake(url, dest, timeout=300):
        with open(dest, "wb") as fh:
            fh.write(b"PAR1")
    monkeypatch.setattr(tp, "_download", fake)


def test_fetch_tape_success_and_request_shape(cache, monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp(200, _ok_body())

    monkeypatch.setattr(tp.requests, "post", fake_post)
    _patch_download(monkeypatch)

    out = tp.fetch_tape("2026-03-01", "2026-03-08", max_markets=50, min_volume=2000,
                        base_url="http://localhost:8000")
    assert captured["url"] == "http://localhost:8000/api/backtest/tape"
    assert captured["json"] == {"t0": "2026-03-01", "t1": "2026-03-08",
                                "max_markets": 50, "min_volume": 2000.0}
    assert captured["headers"]["Authorization"] == "Bearer sk_live_test"
    assert os.path.exists(os.path.join(out, "markets.parquet"))
    assert os.path.exists(os.path.join(out, "quant.parquet"))


def test_fetch_tape_requires_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMMER_TAPE_CACHE", str(tmp_path))
    monkeypatch.delenv("SIMMER_API_KEY", raising=False)
    with pytest.raises(tp.TapeFetchError, match="API key"):
        tp.fetch_tape("2026-03-01", "2026-03-08", base_url="http://x")


def test_fetch_tape_maps_401(cache, monkeypatch):
    monkeypatch.setattr(tp.requests, "post",
                        lambda *a, **k: _Resp(401, {"detail": "invalid api key"}))
    with pytest.raises(tp.TapeFetchError, match="SIMMER_API_KEY|api key"):
        tp.fetch_tape("2026-03-01", "2026-03-08", base_url="http://x")


def test_fetch_tape_maps_429(cache, monkeypatch):
    monkeypatch.setattr(tp.requests, "post",
                        lambda *a, **k: _Resp(429, {"detail": "rate limit reached"}))
    with pytest.raises(tp.TapeFetchError, match="rate limit"):
        tp.fetch_tape("2026-03-01", "2026-03-08", base_url="http://x")


def test_fetch_tape_cache_hit_skips_redownload(cache, monkeypatch):
    monkeypatch.setattr(tp.requests, "post", lambda *a, **k: _Resp(200, _ok_body()))
    calls = {"n": 0}

    def fake(url, dest, timeout=300):
        calls["n"] += 1
        with open(dest, "wb") as fh:
            fh.write(b"PAR1")
    monkeypatch.setattr(tp, "_download", fake)

    tp.fetch_tape("2026-03-01", "2026-03-08", base_url="http://x")
    first = calls["n"]
    assert first >= 2  # markets + quant downloaded
    tp.fetch_tape("2026-03-01", "2026-03-08", base_url="http://x")
    assert calls["n"] == first  # cached → no further downloads


def test_fetch_tape_maps_422(cache, monkeypatch):
    monkeypatch.setattr(tp.requests, "post",
                        lambda *a, **k: _Resp(422, {"detail": "window exceeds the 365-day ceiling"}))
    with pytest.raises(tp.TapeFetchError, match="365-day"):
        tp.fetch_tape("2024-01-01", "2026-01-01", base_url="http://x")


def test_fetch_tape_maps_503(cache, monkeypatch):
    monkeypatch.setattr(tp.requests, "post",
                        lambda *a, **k: _Resp(503, {"detail": "tape service is not configured"}))
    with pytest.raises(tp.TapeFetchError, match="not configured"):
        tp.fetch_tape("2026-03-01", "2026-03-08", base_url="http://x")


def test_fetch_tape_malformed_response(cache, monkeypatch):
    monkeypatch.setattr(tp.requests, "post", lambda *a, **k: _Resp(200, {"key": "x"}))  # no urls
    with pytest.raises(tp.TapeFetchError, match="malformed"):
        tp.fetch_tape("2026-03-01", "2026-03-08", base_url="http://x")


def test_base_url_resolution_prefers_env(monkeypatch):
    monkeypatch.delenv("SIMMER_API_URL", raising=False)
    assert tp._resolve_base_url(None) == "https://api.simmer.markets"
    monkeypatch.setenv("SIMMER_API_URL", "http://localhost:9000/")
    assert tp._resolve_base_url(None) == "http://localhost:9000"
    assert tp._resolve_base_url("http://explicit:1/") == "http://explicit:1"
