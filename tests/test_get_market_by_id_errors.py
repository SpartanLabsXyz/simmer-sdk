"""get_market_by_id error semantics (SIM-4025, 0.23.0).

Before 0.23.0 the method swallowed every exception and returned None, so a
network timeout or transient 5xx was indistinguishable from "market not
found". A user polling a resolved market saw its status/outcome fields
"intermittently go null" when calls silently failed. Since 0.23.0:

- HTTP 404               -> None (market genuinely doesn't exist)
- timeout / 5xx / 429    -> raises (caller can retry)
- 200 with market body   -> parsed Market
"""

import pytest
import requests
from unittest.mock import MagicMock

from simmer_sdk.client import SimmerClient


def _make_client():
    client = SimmerClient.__new__(SimmerClient)
    client.base_url = "https://api.simmer.markets"
    client._request = MagicMock()
    return client


def _http_error(status_code):
    response = MagicMock()
    response.status_code = status_code
    return requests.exceptions.HTTPError(response=response)


def test_returns_market_on_success():
    client = _make_client()
    client._request.return_value = {
        "market": {
            "id": "m1",
            "question": "Resolved?",
            "status": "resolved",
            "current_probability": 0.0,
            "outcome": False,
            "resolves_at": "2026-06-30 01:00:00Z",
        }
    }

    market = client.get_market_by_id("m1")

    assert market is not None
    assert market.status == "resolved"
    assert market.outcome is False
    assert market.resolves_at == "2026-06-30 01:00:00Z"


def test_returns_none_on_404():
    client = _make_client()
    client._request.side_effect = _http_error(404)

    assert client.get_market_by_id("missing") is None


def test_raises_on_server_error():
    client = _make_client()
    client._request.side_effect = _http_error(500)

    with pytest.raises(requests.exceptions.HTTPError):
        client.get_market_by_id("m1")


def test_raises_on_rate_limit():
    client = _make_client()
    client._request.side_effect = _http_error(429)

    with pytest.raises(requests.exceptions.HTTPError):
        client.get_market_by_id("m1")


def test_raises_on_timeout():
    client = _make_client()
    client._request.side_effect = requests.exceptions.Timeout("timed out")

    with pytest.raises(requests.exceptions.Timeout):
        client.get_market_by_id("m1")


def test_returns_none_on_empty_market_body():
    client = _make_client()
    client._request.return_value = {"market": None}

    assert client.get_market_by_id("m1") is None


def test_condition_id_resolver_falls_back_to_context_on_transport_error():
    """_resolve_polymarket_condition_id keeps its second source when the
    market fetch fails transiently."""
    client = _make_client()
    client.get_market_by_id = MagicMock(
        side_effect=requests.exceptions.ConnectionError("boom")
    )
    client.get_market_context = MagicMock(
        return_value={"market": {"polymarket_condition_id": "0x" + "ab" * 32}}
    )

    condition_id = client._resolve_polymarket_condition_id("m1")

    assert condition_id == "0x" + "ab" * 32
