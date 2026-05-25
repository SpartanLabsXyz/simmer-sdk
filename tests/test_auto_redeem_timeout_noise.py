"""SIM-2458: auto_redeem positions timeouts should not become alert noise."""

import logging
from unittest.mock import MagicMock, patch

import requests

from simmer_sdk.client import SimmerClient


def _make_client() -> SimmerClient:
    client = SimmerClient.__new__(SimmerClient)
    client._auto_redeem_enabled = True
    client._refresh_cohort_cache = MagicMock()
    client._request = MagicMock()
    return client


def test_auto_redeem_positions_timeout_logs_info_not_warning(caplog):
    client = _make_client()
    client._request.side_effect = requests.exceptions.ReadTimeout(
        "HTTPSConnectionPool(host='api.simmer.markets', port=443): Read timed out."
    )

    with patch("time.sleep"), caplog.at_level(logging.INFO, logger="simmer_sdk.client"):
        result = client.auto_redeem()

    assert result == []
    assert client._request.call_count == 2
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == []
    messages = [r.getMessage() for r in caplog.records]
    assert any("auto_redeem_warning: positions fetch timed out" in m for m in messages)
    assert any("retryable=true" in m and "non_fatal=true" in m for m in messages)


def test_auto_redeem_positions_non_timeout_still_warns(caplog):
    client = _make_client()
    client._request.side_effect = RuntimeError("server returned malformed JSON")

    with caplog.at_level(logging.WARNING, logger="simmer_sdk.client"):
        result = client.auto_redeem()

    assert result == []
    assert client._request.call_count == 1
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "auto_redeem: could not fetch positions" in warnings[0]
