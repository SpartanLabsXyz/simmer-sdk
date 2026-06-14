from unittest.mock import MagicMock

from simmer_sdk.client import MakerRewardsStatus, SimmerClient, Market


CONDITION_ID = "0x" + "a" * 64


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _client() -> SimmerClient:
    client = SimmerClient.__new__(SimmerClient)
    client.get_market_by_id = MagicMock(return_value=None)
    client.get_market_context = MagicMock(return_value={"market": {"polymarket_id": CONDITION_ID}})
    return client


def _rewards_payload(**overrides):
    row = {
        "condition_id": CONDITION_ID,
        "market_id": "248849",
        "rewards_max_spread": 3,
        "rewards_min_size": 10,
        "market_competitiveness": 0.42,
        "rewards_config": [
            {
                "id": 1,
                "start_date": "2024-03-01",
                "end_date": "2500-12-31",
                "rate_per_day": 2,
                "total_rewards": 92,
            },
            {
                "id": 2,
                "start_date": "2024-03-01",
                "end_date": "2024-03-31",
                "rate_per_day": 99,
                "total_rewards": 3069,
            },
        ],
    }
    row.update(overrides)
    return {"limit": 100, "count": 1, "next_cursor": "LTE=", "data": [row]}


def test_maker_rewards_status_fetches_public_clob_market_rewards(monkeypatch):
    client = _client()
    get = MagicMock(return_value=_Response(_rewards_payload(b=1.5)))
    monkeypatch.setattr("simmer_sdk.client.requests.get", get)

    status = client.maker_rewards_status(CONDITION_ID)

    assert isinstance(status, MakerRewardsStatus)
    assert status.condition_id == CONDITION_ID
    assert status.market_id == "248849"
    assert status.eligible is True
    assert status.v == 3
    assert status.b == 1.5
    assert status.c == 3.0
    assert status.daily_pool == 2
    assert status.min_size == 10
    get.assert_called_once_with(
        f"https://clob.polymarket.com/rewards/markets/{CONDITION_ID}",
        params=None,
        headers={"Accept": "application/json"},
        timeout=10,
    )
    client.get_market_by_id.assert_not_called()


def test_maker_rewards_status_resolves_simmer_market_id(monkeypatch):
    client = _client()
    client.get_market_by_id.return_value = Market(
        id="sim-market",
        question="Will it happen?",
        status="active",
        current_probability=0.5,
        import_source="polymarket",
        polymarket_condition_id=CONDITION_ID,
    )
    get = MagicMock(return_value=_Response(_rewards_payload()))
    monkeypatch.setattr("simmer_sdk.client.requests.get", get)

    status = client.maker_rewards_status("sim-market", sponsored=True, timeout=3)

    assert status.condition_id == CONDITION_ID
    assert status.b is None
    assert status.market_competitiveness == 0.42
    client.get_market_by_id.assert_called_once_with("sim-market")
    client.get_market_context.assert_not_called()
    assert get.call_args.kwargs["params"] == {"sponsored": "true"}
    assert get.call_args.kwargs["timeout"] == 3


def test_maker_rewards_status_returns_ineligible_when_no_market_row(monkeypatch):
    client = _client()
    monkeypatch.setattr(
        "simmer_sdk.client.requests.get",
        MagicMock(return_value=_Response({"limit": 100, "count": 0, "data": []})),
    )

    status = client.maker_rewards_status(CONDITION_ID)

    assert status.eligible is False
    assert status.daily_pool == 0.0
    assert status.v is None
