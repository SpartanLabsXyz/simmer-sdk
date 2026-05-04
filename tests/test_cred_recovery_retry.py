"""Tests for the external-wallet stale-creds auto-recovery added in 0.14.1.

When Polymarket rejects a trade with Unauthorized / Invalid api key, the SDK
should reset its registered-creds cache, re-derive locally, register with
the server, and retry the trade once.
"""

import logging
from unittest.mock import MagicMock

from simmer_sdk.client import SimmerClient


def _make_external_client(venue="polymarket"):
    """SimmerClient with external private key — exercises the retry path."""
    client = SimmerClient.__new__(SimmerClient)
    client._agent_id = "test-agent"
    client._ows_wallet = None
    client._wallet_address = "0x1234567890abcdef1234567890abcdef12345678"
    client._private_key = "0x" + "a" * 64
    client.base_url = "https://api.simmer.markets"
    client.live = True
    client.venue = venue
    client._held_markets_cache = None
    client._clob_creds_registered = True  # simulate already-registered state
    client._request = MagicMock()
    client._get_held_markets = MagicMock(return_value={})
    client._ensure_clob_credentials = MagicMock()
    client._ensure_wallet_linked = MagicMock()
    client._warn_approvals_once = MagicMock()
    client._build_signed_order = MagicMock(return_value=None)  # skip signing in tests
    client._is_agent_wallet_registered = MagicMock(return_value=False)
    return client


def test_retries_on_unauthorized_and_succeeds(caplog):
    """Polymarket → Unauthorized; SDK re-derives + retries; second call succeeds."""
    client = _make_external_client()
    client._request.side_effect = [
        {"success": False, "market_id": "m1", "side": "yes", "error": "Unauthorized/Invalid api key"},
        {"success": True, "market_id": "m1", "side": "yes",
         "shares_bought": 5.0, "cost": 2.0, "fill_status": "filled"},
    ]

    with caplog.at_level(logging.WARNING, logger="simmer_sdk.client"):
        result = client.trade("m1", "yes", amount=10.0)

    # Reset + re-derive happened
    assert client._clob_creds_registered is False
    client._ensure_clob_credentials.assert_called_once()
    # Two POST attempts — original + retry
    assert client._request.call_count == 2
    # Final result is the retry's success
    assert result.success is True
    assert result.shares_bought == 5.0
    # Warning logged for the recovery attempt
    msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("re-deriving" in m for m in msgs)


def test_retries_on_unauthorized_and_retry_also_fails():
    """Both attempts fail with Unauthorized — only one retry, then surface the error."""
    client = _make_external_client()
    client._request.side_effect = [
        {"success": False, "market_id": "m1", "side": "yes", "error": "Unauthorized"},
        {"success": False, "market_id": "m1", "side": "yes", "error": "Unauthorized"},
    ]

    result = client.trade("m1", "yes", amount=10.0)

    assert client._request.call_count == 2  # exactly one retry, no infinite loop
    assert result.success is False
    assert "unauthorized" in (result.error or "").lower()


def test_does_not_retry_on_non_auth_error():
    """A liquidity / balance / signature error should NOT trigger the retry path."""
    client = _make_external_client()
    client._request.return_value = {
        "success": False, "market_id": "m1", "side": "yes",
        "error": "FAK order not filled at price 0.40 — no liquidity at this price.",
    }

    result = client.trade("m1", "yes", amount=10.0)

    assert client._request.call_count == 1
    client._ensure_clob_credentials.assert_not_called()
    assert client._clob_creds_registered is True  # unchanged
    assert result.success is False


def test_does_not_retry_for_managed_wallet():
    """Managed-wallet clients (no private_key, no ows_wallet) skip this retry —
    the server handles their re-derive on its own end."""
    client = _make_external_client()
    client._private_key = None  # managed
    client._ows_wallet = None
    client._request.return_value = {
        "success": False, "market_id": "m1", "side": "yes",
        "error": "Unauthorized/Invalid api key",
    }

    result = client.trade("m1", "yes", amount=10.0)

    assert client._request.call_count == 1
    client._ensure_clob_credentials.assert_not_called()
    assert result.success is False


def test_does_not_retry_for_sim_venue():
    """Sim trades can't hit CLOB-creds errors; if they somehow returned an
    Unauthorized string, the retry path should still skip them."""
    client = _make_external_client(venue="sim")
    client._request.return_value = {
        "success": False, "market_id": "m1", "side": "yes",
        "error": "Unauthorized",
    }

    result = client.trade("m1", "yes", amount=10.0)

    assert client._request.call_count == 1
    client._ensure_clob_credentials.assert_not_called()


def test_retry_swallows_exception_in_re_derive():
    """If _ensure_clob_credentials throws during the retry attempt, the
    original failure is surfaced — we don't propagate the recovery exception."""
    client = _make_external_client()
    client._request.return_value = {
        "success": False, "market_id": "m1", "side": "yes",
        "error": "Unauthorized",
    }
    client._ensure_clob_credentials.side_effect = RuntimeError("derive blocked")

    result = client.trade("m1", "yes", amount=10.0)

    assert client._request.call_count == 1  # retry never fired
    assert result.success is False
    assert "unauthorized" in (result.error or "").lower()
