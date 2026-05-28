from datetime import datetime, timezone

from simmer_sdk.guards.news_recency_veto import is_within_news_window, news_window_match


SCHEDULE = {
    "events": [
        {
            "id": "cpi-2026-06",
            "category": "CPI",
            "timestamp": "2026-06-10T12:30:00Z",
        }
    ]
}


def test_cpi_market_vetoes_inside_30s_window():
    market = {"id": "m1", "question": "Will CPI inflation be above 3.0% in June?"}
    now = datetime(2026, 6, 10, 12, 30, 5, tzinfo=timezone.utc)

    blocked, event = news_window_match(market, SCHEDULE, now=now)

    assert blocked is True
    assert event["id"] == "cpi-2026-06"


def test_cpi_market_does_not_veto_after_30s_window():
    market = {"id": "m1", "question": "Will CPI inflation be above 3.0% in June?"}
    now = datetime(2026, 6, 10, 12, 30, 35, tzinfo=timezone.utc)

    assert is_within_news_window(market, SCHEDULE, now=now) is False


def test_continuous_feed_crypto_updown_does_not_veto_inside_window():
    market = {"id": "m2", "question": "BTC Up or Down - Jun 10, 12:30PM-12:45PM ET"}
    now = datetime(2026, 6, 10, 12, 30, 5, tzinfo=timezone.utc)

    assert is_within_news_window(market, SCHEDULE, now=now) is False

