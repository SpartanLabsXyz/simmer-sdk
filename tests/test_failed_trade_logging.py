"""Tests for the failed-trade WARNING log added in 0.14.0.

Bots that don't check `result.success` previously looped silently when the
upstream venue rejected orders. The SDK now emits `logger.warning` whenever
`client.trade()` returns `success=False` with an `error` set, so any bot
using stdlib logging gets a stderr signal on the first failure.
"""

import logging
from unittest.mock import MagicMock

from simmer_sdk.client import SimmerClient


def _make_client(venue="polymarket"):
    """Build a SimmerClient with mocked _request for trade() unit tests."""
    client = SimmerClient.__new__(SimmerClient)
    client._agent_id = "test-agent"
    client._ows_wallet = None
    client._wallet_address = None
    client._private_key = None
    client.base_url = "https://api.simmer.markets"
    client.live = True
    client.venue = venue
    client._held_markets_cache = None
    client._request = MagicMock()
    client._get_held_markets = MagicMock(return_value={})
    return client


def test_logs_warning_on_failed_real_trade(caplog):
    """A failed Polymarket trade emits one WARNING log with the venue + error."""
    client = _make_client(venue="polymarket")
    client._request.return_value = {
        "success": False,
        "market_id": "test-market",
        "side": "yes",
        "error": "Unauthorized/Invalid api key",
    }

    with caplog.at_level(logging.WARNING, logger="simmer_sdk.client"):
        result = client.trade("test-market", "yes", amount=10.0)

    assert result.success is False
    assert result.error == "Unauthorized/Invalid api key"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "polymarket" in warnings[0].getMessage()
    assert "Unauthorized/Invalid api key" in warnings[0].getMessage()


def test_does_not_log_on_successful_trade(caplog):
    """A successful trade emits no WARNING."""
    client = _make_client(venue="polymarket")
    client._request.return_value = {
        "success": True,
        "market_id": "test-market",
        "side": "yes",
        "shares_bought": 5.0,
        "cost": 2.5,
        "fill_status": "filled",
    }

    with caplog.at_level(logging.WARNING, logger="simmer_sdk.client"):
        result = client.trade("test-market", "yes", amount=10.0)

    assert result.success is True
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == []


def test_does_not_log_when_failure_has_no_error(caplog):
    """If the server returns success=False without an error string, suppress
    the log — there's nothing useful to print, and we don't want to fire on
    any default-False response shape."""
    client = _make_client(venue="polymarket")
    client._request.return_value = {
        "success": False,
        "market_id": "test-market",
        "side": "yes",
        "error": None,
    }

    with caplog.at_level(logging.WARNING, logger="simmer_sdk.client"):
        result = client.trade("test-market", "yes", amount=10.0)

    assert result.success is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == []
