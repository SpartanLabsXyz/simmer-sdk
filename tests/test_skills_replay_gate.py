"""Binance-trio replay gate: under SIMMER_REPLAY=1, a failing/absent data
plane must produce an honest no-signal — NEVER a direct Binance call (that
would be future data relative to the frozen tick). Refs SIM-3070 1.5C."""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

SKILLS = Path(__file__).parent.parent / "skills"


def _load(slug, module_file, name):
    spec = importlib.util.spec_from_file_location(name, SKILLS / slug / module_file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def replay_env(monkeypatch):
    monkeypatch.setenv("SIMMER_REPLAY", "1")
    monkeypatch.setenv("SIMMER_API_KEY", "sk_replay")


def test_fastloop_replay_no_plane_raises_never_binance(replay_env):
    m = _load("polymarket-fast-loop", "fastloop_trader.py", "_fl_gate")
    m.get_client = MagicMock(side_effect=RuntimeError("no plane"))
    m._api_request = MagicMock()
    with pytest.raises(m.SignalFetchError):
        m.get_binance_momentum("BTCUSDT", 5)
    m._api_request.assert_not_called()


def test_fastscaler_replay_no_plane_returns_none_never_binance(replay_env):
    m = _load("polymarket-fast-scaler", "fast_scaler.py", "_fs_gate")
    m.get_client = MagicMock(side_effect=RuntimeError("no plane"))
    m._api_request = MagicMock()
    assert m.get_binance_1m_momentum("BTC") is None
    m._api_request.assert_not_called()


def test_btcupdown_replay_no_plane_returns_none_never_binance(replay_env):
    m = _load("polymarket-btc-up-down-trader", "strategy.py", "_bud_gate")
    m.get_client = MagicMock(side_effect=RuntimeError("no plane"))
    m._api_request = MagicMock()
    assert m.fetch_btc_momentum(30) == (None, None, None)
    m._api_request.assert_not_called()


def test_fastloop_live_falls_back_when_plane_missing(monkeypatch):
    monkeypatch.delenv("SIMMER_REPLAY", raising=False)
    m = _load("polymarket-fast-loop", "fastloop_trader.py", "_fl_live")
    m.get_client = MagicMock(side_effect=RuntimeError("old server"))
    # legacy path runs: 5 fake klines [open_time, open, high, low, close, volume, ...]
    klines = [[0, "100", "101", "99", "100.5", "10", 0]] * 5
    m._api_request = MagicMock(return_value=klines)
    out = m.get_binance_momentum("BTCUSDT", 5)
    assert out["endpoint"].startswith("https://api.binance")
    m._api_request.assert_called()


def test_plane_used_when_available(monkeypatch):
    monkeypatch.delenv("SIMMER_REPLAY", raising=False)
    monkeypatch.setenv("SIMMER_API_KEY", "sk_test")  # guard: no key → plane skipped
    m = _load("polymarket-fast-scaler", "fast_scaler.py", "_fs_plane")
    candles = [{"open": 100.0, "close": 100.3, "volume": 5.0},
               {"open": 100.3, "close": 100.1, "volume": 7.0}]
    client = MagicMock()
    client.get_candles.return_value = candles
    m.get_client = MagicMock(return_value=client)
    m._api_request = MagicMock()
    out = m.get_binance_1m_momentum("BTC")
    assert out["price_now"] == 100.1  # LAST CLOSED candle, not index -2
    m._api_request.assert_not_called()
