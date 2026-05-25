import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


FASTLOOP_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "polymarket-fast-loop"
    / "fastloop_trader.py"
)


def load_fastloop():
    spec = importlib.util.spec_from_file_location("fastloop_trader_under_test", FASTLOOP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_binance_momentum_falls_back_to_us_endpoint(monkeypatch):
    fastloop = load_fastloop()
    calls = []

    def fake_api_request(url, *args, **kwargs):
        calls.append(url)
        if "api.binance.com" in url:
            return {"error": "Service unavailable from a restricted location", "status_code": 451}
        return [
            [1, "100.0", "101.0", "99.0", "100.5", "10.0"],
            [2, "100.5", "102.0", "100.0", "101.0", "20.0"],
        ]

    monkeypatch.setattr(fastloop, "_api_request", fake_api_request)

    signal = fastloop.get_binance_momentum("BTCUSDT", 2)

    assert len(calls) == 2
    assert "api.binance.us" in calls[1]
    assert signal["price_now"] == 101.0
    assert signal["momentum_pct"] == pytest.approx(1.0)
    assert signal["endpoint"] == "https://api.binance.us"
    assert signal["fallback_attempts"][0]["status_code"] == 451


def test_binance_momentum_raises_structured_failure(monkeypatch):
    fastloop = load_fastloop()

    def fake_api_request(url, *args, **kwargs):
        if "api.binance.com" in url:
            return {"error": "geo blocked", "status_code": 451}
        return {"error": "rate limited", "status_code": 429}

    monkeypatch.setattr(fastloop, "_api_request", fake_api_request)

    with pytest.raises(fastloop.SignalFetchError) as exc:
        fastloop.get_binance_momentum("BTCUSDT", 2)

    assert "Binance kline fetch failed" in str(exc.value)
    assert exc.value.failures == [
        {
            "endpoint": "https://api.binance.com",
            "url": "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=2",
            "status_code": 451,
            "error": "geo blocked",
        },
        {
            "endpoint": "https://api.binance.us",
            "url": "https://api.binance.us/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=2",
            "status_code": 429,
            "error": "rate limited",
        },
    ]


def test_signal_failure_emits_automaton_skip_reason(monkeypatch, capsys):
    fastloop = load_fastloop()

    class DummyClient:
        venue = "sim"
        live = False

        def auto_redeem(self):
            return []

    market = {
        "question": "Bitcoin Up or Down - May 25, 12:35PM ET",
        "market_id": "m1",
        "end_time": datetime.now(timezone.utc) + timedelta(minutes=4),
        "clob_token_ids": ["yes", "no"],
        "fee_rate_bps": 1,
    }

    monkeypatch.setenv("AUTOMATON_MANAGED", "1")
    monkeypatch.setattr(fastloop, "_automaton_reported", False)
    monkeypatch.setattr(fastloop, "get_client", lambda live=True: DummyClient())
    monkeypatch.setattr(fastloop, "discover_fast_market_markets", lambda asset, window: [market])
    monkeypatch.setattr(fastloop, "get_positions", lambda: [])
    monkeypatch.setattr(fastloop, "fetch_live_prices", lambda clob_tokens: 0.5)

    failures = [{"endpoint": "https://api.binance.com", "status_code": 451, "error": "geo blocked"}]

    def fail_signal(*args, **kwargs):
        raise fastloop.SignalFetchError("Binance kline fetch failed", failures)

    monkeypatch.setattr(fastloop, "get_momentum", fail_signal)

    fastloop.run_fast_market_strategy(dry_run=True)

    automaton_lines = [
        line for line in capsys.readouterr().out.splitlines()
        if line.startswith('{"automaton"')
    ]
    assert len(automaton_lines) == 1
    report = json.loads(automaton_lines[0])["automaton"]
    assert report["skip_reason"] == "signal_fetch_failed"
    assert report["signal_source"] == fastloop.SIGNAL_SOURCE
    assert report["signal_failures"] == failures
