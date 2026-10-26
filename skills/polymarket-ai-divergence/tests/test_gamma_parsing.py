"""Offline coverage for Gamma payload normalization."""

import os
import sys

import pytest

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

import gamma_api  # noqa: E402


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail_urlopen(*args, **kwargs):
        pytest.fail("Gamma parsing must not call urlopen")

    monkeypatch.setattr(gamma_api, "urlopen", fail_urlopen)


@pytest.fixture
def client():
    return gamma_api.GammaClient()


@pytest.mark.parametrize(
    "value, expected",
    [(None, 0.0), ("0.75", 0.75), ("12", 12.0), ("junk", 0.0), ({}, 0.0)],
)
def test_safe_float(client, value, expected):
    assert client._safe_float(value) == expected


@pytest.mark.parametrize("value", [None, "junk", {}])
def test_safe_float_custom_default(client, value):
    assert client._safe_float(value, default=0.25) == 0.25


@pytest.mark.parametrize(
    "value, expected",
    [(' ["Yes", "No"] ', ["Yes", "No"]), ("invalid json", "invalid json"),
     (["Yes", "No"], ["Yes", "No"]), (None, None)],
)
def test_parse_json_field(client, value, expected):
    assert client._parse_json_field(value) == expected


def test_parse_market_json_fields(client):
    market = client._parse_market({
        "id": "market-1",
        "conditionId": "condition-1",
        "question": "Will it rain?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.7", "0.2"]',
        "clobTokenIds": '["token-yes", "token-no"]',
        "volume": "1200.5",
        "volume24hr": "100",
        "liquidity": "250.25",
    })

    assert market["id"] == "market-1"
    assert market["condition_id"] == "condition-1"
    assert market["question"] == "Will it rain?"
    assert market["outcomes"] == ["Yes", "No"]
    assert market["outcome_prices"] == [0.7, 0.2]
    assert market["yes_price"] == 0.7
    assert market["no_price"] == 0.2
    assert market["clob_token_ids"] == ["token-yes", "token-no"]
    assert market["volume"] == 1200.5
    assert market["volume_24h"] == 100.0
    assert market["liquidity"] == 250.25


@pytest.mark.parametrize(
    "payload, prices, yes_price, no_price",
    [
        ({"outcomePrices": ["0.7"]}, [0.7], 0.7, 0.3),
        ({}, [], 0.5, 0.5),
        ({"outcomePrices": []}, [], 0.5, 0.5),
        ({"outcomePrices": ["junk", "0.2"]}, [0.5, 0.2], 0.5, 0.2),
        ({"outcomePrices": ["0.7", "junk"]}, [0.7, 0.5], 0.7, 0.5),
    ],
)
def test_parse_market_price_fallbacks(client, payload, prices, yes_price, no_price):
    market = client._parse_market(payload)

    assert market["outcome_prices"] == prices
    assert market["yes_price"] == pytest.approx(yes_price)
    assert market["no_price"] == pytest.approx(no_price)


@pytest.mark.parametrize("alias", ["neg_risk", "negRisk", "enableNegRisk"])
@pytest.mark.parametrize("enabled", [True, False])
def test_parse_market_neg_risk_aliases(client, alias, enabled):
    assert client._parse_market({alias: enabled})["neg_risk"] is enabled


def test_parse_event_with_nested_markets(client):
    event = client._parse_event({
        "id": "event-1",
        "slug": "rain-forecast",
        "title": "Rain forecast",
        "volume": "1500",
        "volume24hr": "200.5",
        "liquidity": "300",
        "markets": [
            {"id": "market-1", "conditionId": "condition-1",
             "outcomePrices": '["0.7", "0.3"]'},
            {"id": "market-2", "conditionId": "condition-2"},
        ],
    })

    assert event["id"] == "event-1"
    assert event["slug"] == "rain-forecast"
    assert event["title"] == "Rain forecast"
    assert event["volume"] == 1500.0
    assert event["volume_24h"] == 200.5
    assert event["liquidity"] == 300.0
    assert event["market_count"] == 2
    first, second = event["markets"]
    assert first["id"] == "market-1"
    assert first["condition_id"] == "condition-1"
    assert first["outcome_prices"] == [0.7, 0.3]
    assert first["yes_price"] == 0.7
    assert first["no_price"] == 0.3
    assert second["id"] == "market-2"
    assert second["condition_id"] == "condition-2"
    assert second["outcome_prices"] == []
    assert second["yes_price"] == 0.5
    assert second["no_price"] == 0.5


@pytest.mark.parametrize("payload", [{}, {"markets": []}, {"markets": None}])
def test_parse_event_without_markets(client, payload):
    event = client._parse_event(payload)

    assert event["markets"] == []
    assert event["market_count"] == 0
