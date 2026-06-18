from simmer_sdk.client import SimmerClient


def test_parse_market_preserves_resolution_outcome():
    market = SimmerClient._parse_market({
        "id": "m1",
        "question": "Did YES win?",
        "status": "resolved",
        "current_probability": 1.0,
        "outcome": True,
    })

    assert market.outcome is True


def test_parse_market_preserves_false_outcome_and_quote_age():
    market = SimmerClient._parse_market({
        "id": "m2",
        "question": "Did NO win?",
        "status": "resolved",
        "current_probability": 0.0,
        "outcome": False,
        "quote_ts": 1780000000.0,
        "quote_age_seconds": 2.5,
    })

    assert market.outcome is False
    assert market.quote_ts == 1780000000.0
    assert market.quote_age_seconds == 2.5


def test_parse_market_defaults_outcome_to_none_for_pending_markets():
    market = SimmerClient._parse_market({
        "id": "m3",
        "question": "Pending?",
        "current_probability": 0.42,
    })

    assert market.outcome is None
