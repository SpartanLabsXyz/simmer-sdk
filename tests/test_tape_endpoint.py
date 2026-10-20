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
        "markets_requested": 50,
        "markets_matching_filter": 12,
        "markets_served": 12,
        "max_markets_served": 50,
        "truncated": False,
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
        if str(dest).endswith("manifest.json"):
            with open(dest, "w") as fh:
                json.dump({"markets_requested": 999, "markets": 12}, fh)
        else:
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


def test_fetch_tape_sends_q_filter(cache, monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _Resp(200, _ok_body())

    monkeypatch.setattr(tp.requests, "post", fake_post)
    _patch_download(monkeypatch)

    tp.fetch_tape("2026-08-17", "2026-09-16", max_markets=3000,
                  min_volume=0, q="temperature", base_url="http://localhost:8000")
    assert captured["json"] == {
        "t0": "2026-08-17",
        "t1": "2026-09-16",
        "max_markets": 3000,
        "min_volume": 0.0,
        "q": "temperature",
    }


def test_fetch_tape_omits_q_when_not_given(cache, monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["json"] = json
        return _Resp(200, _ok_body())

    monkeypatch.setattr(tp.requests, "post", fake_post)
    _patch_download(monkeypatch)

    tp.fetch_tape("2026-03-01", "2026-03-08", base_url="http://localhost:8000")
    assert "q" not in captured["json"]


def test_fetch_tape_coverage_success(cache, monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        return _Resp(200, {
            "dataset_rev": "rev1",
            "coverage_t1": "2026-05-05",
            "canonical_coverage_t1": "2026-05-05",
            "source": "replay-tapes:rev1",
        })

    monkeypatch.setattr(tp.requests, "get", fake_get)

    coverage = tp.fetch_tape_coverage(base_url="http://localhost:8000")

    assert captured["url"] == "http://localhost:8000/api/backtest/tape/coverage"
    assert captured["headers"]["Authorization"] == "Bearer sk_live_test"
    assert coverage["coverage_t1"] == "2026-05-05"


def test_fetch_tape_coverage_requires_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("SIMMER_TAPE_CACHE", str(tmp_path))
    monkeypatch.delenv("SIMMER_API_KEY", raising=False)
    with pytest.raises(tp.TapeFetchError, match="API key"):
        tp.fetch_tape_coverage(base_url="http://x")


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


def test_fetch_tape_stamps_manifest_counts_from_current_response(cache, monkeypatch):
    responses = [
        _ok_body(key="shared"),
        {**_ok_body(key="shared"), "markets_requested": 3000, "cached": True},
    ]

    monkeypatch.setattr(tp.requests, "post", lambda *a, **k: _Resp(200, responses.pop(0)))
    _patch_download(monkeypatch)

    out = tp.fetch_tape("2026-03-01", "2026-03-08", base_url="http://x")
    manifest_path = os.path.join(out, "manifest.json")
    assert json.load(open(manifest_path))["markets_requested"] == 50

    tp.fetch_tape("2026-03-01", "2026-03-08", base_url="http://x")
    assert json.load(open(manifest_path))["markets_requested"] == 3000


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


def test_fetch_tape_coverage_404_falls_back_to_dataset_end(cache, monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        assert url == "http://localhost:8000/api/backtest/tape/coverage"
        return _Resp(404, {"detail": "Not Found"})

    monkeypatch.setattr(tp.requests, "get", fake_get)
    coverage = tp.fetch_tape_coverage(base_url="http://localhost:8000")
    assert coverage["coverage_t1"] == tp.DATASET_END
    assert coverage["source"] == "fallback:dataset_end"


def test_concurrent_manifest_stamps_publish_valid_json(cache, monkeypatch):
    manifest_path = cache / "manifest.json"
    manifest_path.write_text('{"markets": 12}')
    original_dump = tp.json.dump
    overlapping = False

    def overlapping_dump(manifest, fh, **kwargs):
        nonlocal overlapping
        if overlapping:
            return original_dump(manifest, fh, **kwargs)
        overlapping = True
        encoded = json.dumps(manifest, **kwargs)
        fh.write(encoded[:1])
        fh.flush()
        tp._stamp_manifest_from_response(cache, {"markets_requested": 10 ** 100})
        fh.write(encoded[1:])

    monkeypatch.setattr(tp.json, "dump", overlapping_dump)
    tp._stamp_manifest_from_response(cache, {"markets_requested": 1})

    assert json.loads(manifest_path.read_text())["markets_requested"] == 1
    assert not list(cache.glob("*.part"))


@pytest.mark.parametrize("contents", ['{"t0": ', '[]'])
def test_stamp_manifest_repairs_corrupt_manifest(cache, contents):
    manifest_path = cache / "manifest.json"
    manifest_path.write_text(contents)
    body = _ok_body()

    tp._stamp_manifest_from_response(cache, body)

    manifest = json.loads(manifest_path.read_text())
    for key in ("t0", "t1", "markets", "quant_rows", "dataset_rev",
                "markets_requested", "markets_matching_filter", "markets_served",
                "max_markets_served", "truncated"):
        assert manifest[key] == body[key]


@pytest.mark.parametrize("contents", ['{"t0": ', '[]'])
def test_dataset_rev_tolerates_corrupt_manifest(cache, contents):
    from simmer_sdk.backtest import _dataset_rev

    (cache / "manifest.json").write_text(contents)
    assert _dataset_rev(str(cache)) == "unknown"


@pytest.mark.parametrize("payload", [None, [], {}, {"coverage_t1": 20260505},
                                     {"coverage_t1": "not-a-date"}, {"coverage_t1": ""}])
def test_fetch_tape_coverage_malformed_raises_tape_fetch_error(cache, monkeypatch, payload):
    response = _Resp(200, payload)
    if payload is None:
        def invalid_json():
            return json.loads("not json")
        monkeypatch.setattr(response, "json", invalid_json)
    monkeypatch.setattr(tp.requests, "get", lambda *a, **k: response)

    with pytest.raises(tp.TapeFetchError, match="malformed tape coverage response"):
        tp.fetch_tape_coverage(base_url="http://localhost:8000")


def test_fetch_tape_full_iso_end_repairs_corrupt_manifest(cache, monkeypatch):
    body = {**_ok_body(), "t1": "2026-10-20T13:00:00+00:00"}
    monkeypatch.setattr(tp.requests, "post", lambda *a, **k: _Resp(200, body))
    def download(url, dest, timeout=300):
        with open(dest, "wb") as fh:
            fh.write(b'{"t0": ' if str(dest).endswith("manifest.json") else b"PAR1")
    monkeypatch.setattr(tp, "_download", download)
    out = tp.fetch_tape("2026-10-19", "2026-10-21", base_url="http://x")
    with open(os.path.join(out, "manifest.json")) as fh:
        assert json.load(fh)["t1"] == body["t1"]
