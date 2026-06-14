"""client.get_candles — data-plane consumption contract (SIM-3070 1.5C)."""

from unittest.mock import patch

from simmer_sdk.client import SimmerClient


def _client():
    return SimmerClient(api_key="sk_test", venue="polymarket")


def test_complete_response_returns_candles_no_fallback():
    c = _client()
    payload = {"complete": True, "symbol": "BTCUSDT",
               "candles": [{"open_time": "t", "close": 0.5}]}
    with patch.object(c, "_request", return_value=payload) as req, \
         patch.object(SimmerClient, "_live_binance_tail") as tail:
        out = c.get_candles("BTCUSDT", "2026-04-15T12:00:00", "2026-04-15T16:00:00")
    assert out == payload["candles"]
    tail.assert_not_called()
    assert req.call_args[0][1] == "/api/replay-data/candles"


def test_incomplete_response_fetches_tail_from_served_through():
    c = _client()
    payload = {"complete": False, "symbol": "BTCUSDT",
               "served_through": "2026-06-01T00:00:00+00:00",
               "candles": [{"open_time": "head"}]}
    with patch.object(c, "_request", return_value=payload), \
         patch.object(SimmerClient, "_live_binance_tail",
                      return_value=[{"open_time": "tail"}]) as tail:
        out = c.get_candles("BTCUSDT", "2026-05-30T00:00:00", "2026-06-02T00:00:00")
    assert [x["open_time"] for x in out] == ["head", "tail"]
    # tail starts where the server's coverage ended, not at the user's start
    assert tail.call_args[0][2] == "2026-06-01T00:00:00+00:00"


def test_fallback_disabled_returns_archived_head_only():
    c = _client()
    payload = {"complete": False, "served_through": "x", "candles": [{"open_time": "head"}]}
    with patch.object(c, "_request", return_value=payload), \
         patch.object(SimmerClient, "_live_binance_tail") as tail:
        out = c.get_candles("BTCUSDT", "a", "b", allow_live_fallback=False)
    assert out == [{"open_time": "head"}]
    tail.assert_not_called()


def test_live_tail_drops_in_progress_candle():
    """The client-side closed-candle rule: a candle closing in the future is
    look-ahead and must be dropped."""
    import json
    from io import BytesIO
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    closed = [int((now - timedelta(minutes=5)).timestamp() * 1000), "1", "2", "0.5", "1.5",
              "100", int((now - timedelta(minutes=4)).timestamp() * 1000) - 1]
    in_progress = [int(now.timestamp() * 1000), "1", "2", "0.5", "1.5",
                   "100", int((now + timedelta(minutes=1)).timestamp() * 1000)]
    body = json.dumps([closed, in_progress]).encode()

    class FakeResp(BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch("urllib.request.urlopen", return_value=FakeResp(body)):
        out = SimmerClient._live_binance_tail(
            "BTCUSDT", "1m",
            (now - timedelta(minutes=10)).isoformat(), now.isoformat())
    assert len(out) == 1
    assert out[0]["volume"] == 100.0


def test_live_tail_errors_degrade_to_empty():
    with patch("urllib.request.urlopen", side_effect=OSError("net down")):
        assert SimmerClient._live_binance_tail("BTCUSDT", "1m", "2026-06-01", "2026-06-02") == []
