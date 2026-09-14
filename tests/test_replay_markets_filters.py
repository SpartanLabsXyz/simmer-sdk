"""Pin replay's tags/status 422 and the q= discovery path (#368).

Skill discovery under backtest uses GET /api/sdk/markets?q=temperature
because MarketMeta has no tags — accepting tags/status would silently
ignore them (the 0.25.3/0.25.4 fail-loud contract). These tests hit the
vendored replay app with an in-memory store; no live Polymarket, no tape.

Skipped when the [backtest] extra (fastapi) is not installed — same gate
as tests/test_backtest_run.py.
"""
from datetime import datetime, timezone

import pytest

fastapi = pytest.importorskip("fastapi", reason="requires the [backtest] extra")
from fastapi.testclient import TestClient  # noqa: E402

from simmer_sdk.backtest.replay.server import ReplaySession, create_app  # noqa: E402
from simmer_sdk.backtest.replay.simstate import SimState  # noqa: E402
from simmer_sdk.backtest.replay.store import MarketMeta  # noqa: E402


NOW = datetime(2026, 4, 30, tzinfo=timezone.utc)


class _MemStore:
    def __init__(self, markets):
        self._markets = markets

    def markets(self, at, *, limit=500, order_by="volume", **filters):
        return self._markets[:limit]

    def price(self, market_id, at):
        return None

    def prices(self, market_id, t0, t1):
        return []

    def trades(self, market_id, t0, t1):
        return []

    def resolution(self, market_id):
        return None


def _meta(market_id, question, slug, volume=1000.0):
    return MarketMeta(
        id=market_id,
        question=question,
        slug=slug,
        condition_id=f"cond-{market_id}",
        answer1="Yes",
        answer2="No",
        created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 5, 10, tzinfo=timezone.utc),
        volume=volume,
    )


def _client(markets=None):
    store = _MemStore(markets or [
        _meta("wx1", "Highest temperature in NYC on April 30",
              "highest-temperature-in-nyc-on-april-30"),
        _meta("btc1", "Will Bitcoin hit 100k?", "will-bitcoin-hit-100k", volume=9e6),
    ])
    session = ReplaySession(store=store, sim=SimState(starting_balance=1000.0), now=NOW)
    return TestClient(create_app(session))


def test_replay_rejects_tags_with_422():
    r = _client().get("/api/sdk/markets", params={"tags": "weather", "limit": 10})
    assert r.status_code == 422
    assert "tags" in r.json()["detail"]


def test_replay_rejects_status_with_422():
    r = _client().get("/api/sdk/markets", params={"status": "active", "limit": 10})
    assert r.status_code == 422
    assert "status" in r.json()["detail"]


def test_replay_q_temperature_uses_existing_question_slug_filter():
    r = _client().get("/api/sdk/markets", params={"q": "temperature", "limit": 10})
    assert r.status_code == 200
    ids = [m["id"] for m in r.json()["markets"]]
    assert ids == ["wx1"]


def test_replay_q_miss_is_empty_200_not_422():
    """HF volume slices with 0 weather markets are an empty tape, not a 422."""
    only_btc = [_meta("btc1", "Will Bitcoin hit 100k?", "will-bitcoin-hit-100k")]
    r = _client(only_btc).get("/api/sdk/markets", params={"q": "temperature", "limit": 10})
    assert r.status_code == 200
    assert r.json()["markets"] == []
