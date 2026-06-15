"""Tests for find_markets server-side keyword filtering (SIM-3241).

find_markets must push the query to the server ``q`` filter, which is applied
BEFORE the result window, so matches are found across the full active
catalogue -- not just the most-recent browse slice. Previously it fetched an
unfiltered window and filtered client-side, silently missing older-but-active
markets (e.g. World Cup markets outside the newest-N window).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from simmer_sdk.client import SimmerClient


def _client() -> SimmerClient:
    return SimmerClient.__new__(SimmerClient)


def _market(question: str):
    return SimpleNamespace(question=question)


def test_find_markets_pushes_query_to_server_q():
    """A >=2-char query is forwarded as the server-side ``q`` filter."""
    client = _client()
    client.get_markets = MagicMock(
        return_value=[_market("Will Brazil win the World Cup?")]
    )

    results = client.find_markets("world cup")

    client.get_markets.assert_called_once_with(q="world cup", limit=100)
    assert len(results) == 1


def test_find_markets_still_filters_client_side():
    """The client-side substring filter still narrows the server result set."""
    client = _client()
    client.get_markets = MagicMock(
        return_value=[
            _market("Will Brazil win the World Cup?"),
            _market("Will it rain in Dallas?"),
        ]
    )

    results = client.find_markets("world cup")

    assert [m.question for m in results] == ["Will Brazil win the World Cup?"]


def test_find_markets_short_query_falls_back_to_unfiltered():
    """Server ``q`` needs >= 2 chars; a 1-char query must not be pushed as ``q``."""
    client = _client()
    client.get_markets = MagicMock(return_value=[_market("A market")])

    client.find_markets("a")

    client.get_markets.assert_called_once_with(limit=100)
