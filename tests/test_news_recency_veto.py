from datetime import datetime, timezone

from simmer_sdk.guards.news_recency_veto import is_within_news_window, news_window_match


SCHEDULE = {
    "events": [
        {
            "id": "cpi-2026-06",
            "category": "CPI",
            "timestamp": "2026-06-10T12:30:00Z",
        },
        {
            "id": "earnings-2026-q2",
            "category": "QUARTERLY_EARNINGS",
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


def test_cpi_event_does_not_veto_earnings_market():
    market = {"id": "m3", "question": "Will ACME report positive earnings in Q2?"}
    cpi_only_schedule = {"events": [SCHEDULE["events"][0]]}
    now = datetime(2026, 6, 10, 12, 30, 5, tzinfo=timezone.utc)

    assert is_within_news_window(market, cpi_only_schedule, now=now) is False


def test_earnings_event_vetoes_earnings_market():
    market = {"id": "m4", "question": "Will ACME report positive EPS in Q2?"}
    earnings_only_schedule = {"events": [SCHEDULE["events"][1]]}
    now = datetime(2026, 6, 10, 12, 30, 5, tzinfo=timezone.utc)

    blocked, event = news_window_match(market, earnings_only_schedule, now=now)

    assert blocked is True
    assert event["id"] == "earnings-2026-q2"


def test_keyword_matching_uses_boundaries():
    market = {"id": "m5", "question": "Will Epstein files be released by Bigtumbls?"}
    now = datetime(2026, 6, 10, 12, 30, 5, tzinfo=timezone.utc)

    assert is_within_news_window(market, SCHEDULE, now=now) is False


def test_revenue_alone_does_not_classify_as_earnings_event():
    market = {"id": "m5b", "question": "Will annual revenue exceed $1B?"}
    earnings_only_schedule = {"events": [SCHEDULE["events"][1]]}
    now = datetime(2026, 6, 10, 12, 30, 5, tzinfo=timezone.utc)

    assert is_within_news_window(market, earnings_only_schedule, now=now) is False


def test_schedule_datetimes_must_include_timezone():
    market = {"id": "m6", "question": "Will CPI inflation be above 3.0% in June?"}
    schedule = {"events": [{"id": "cpi-naive", "category": "CPI", "timestamp": "2026-06-10T12:30:00"}]}
    now = datetime(2026, 6, 10, 12, 30, 5, tzinfo=timezone.utc)

    assert is_within_news_window(market, schedule, now=now) is False
