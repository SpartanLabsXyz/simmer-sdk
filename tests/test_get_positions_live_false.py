"""Regression tests for live=False position receipt reads (SIM-2585)."""

from unittest.mock import MagicMock

from simmer_sdk.client import SimmerClient
from simmer_sdk.paper import PaperPortfolio


def _make_client(*, venue="polymarket", live=False) -> SimmerClient:
    client = SimmerClient.__new__(SimmerClient)
    client.live = live
    client.venue = venue
    client._paper_portfolio = PaperPortfolio() if not live else None
    client._position_holder_cache = {}
    client._position_holder_ts = 0.0
    client._request = MagicMock()
    client.get_market_context = MagicMock(return_value={
        "market": {
            "question": "Will it rain in Chicago?",
            "external_price_yes": 0.61,
            "status": "active",
        }
    })
    return client


def _api_positions_response() -> dict:
    return {
        "positions": [
            {
                "market_id": "pm-weather-1",
                "question": "Will it rain in Chicago?",
                "shares_yes": 7.5,
                "shares_no": 0.0,
                "current_value": 4.58,
                "pnl": -0.42,
                "status": "active",
                "venue": "polymarket",
                "holder_address": "0xabc",
            }
        ]
    }


def test_live_false_real_venue_empty_paper_portfolio_fetches_api_positions():
    client = _make_client(venue="polymarket", live=False)
    client._request.return_value = _api_positions_response()

    positions = client.get_positions()

    assert len(positions) == 1
    assert positions[0].market_id == "pm-weather-1"
    assert positions[0].venue == "polymarket"
    client._request.assert_called_once_with("GET", "/api/sdk/positions", params=None)


def test_live_false_real_venue_keeps_existing_paper_positions_local():
    client = _make_client(venue="polymarket", live=False)
    client._paper_portfolio.log_trade(
        "paper-market",
        side="yes",
        action="buy",
        shares=10.0,
        cost=5.0,
        price=0.5,
        venue="polymarket",
    )

    positions = client.get_positions()

    assert len(positions) == 1
    assert positions[0].market_id == "paper-market"
    assert positions[0].venue == "polymarket"
    client._request.assert_not_called()


def test_live_false_sim_venue_empty_paper_portfolio_stays_local():
    client = _make_client(venue="sim", live=False)

    positions = client.get_positions()

    assert positions == []
    client._request.assert_not_called()


def test_get_positions_venue_all_omits_backend_venue_param():
    client = _make_client(venue="polymarket", live=True)
    client._request.return_value = _api_positions_response()

    positions = client.get_positions(venue="all", source="sdk:weather")

    assert len(positions) == 1
    client._request.assert_called_once_with(
        "GET",
        "/api/sdk/positions",
        params={"source": "sdk:weather"},
    )
