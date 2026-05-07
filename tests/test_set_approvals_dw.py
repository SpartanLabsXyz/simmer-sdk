"""
Tests for set_approvals() deposit-wallet short-circuit (SIM-1613).

When _uses_deposit_wallet is True, set_approvals() must return immediately with
a structured response that:
  - preserves the set/skipped/failed/details keys (backward-compat)
  - adds deposit_wallet_user=True so callers can branch
  - includes a human-readable message pointing to the dashboard
  - never submits any transaction or calls eth-account
"""

import pytest
from unittest.mock import MagicMock, patch


def _make_dw_client():
    """Return a minimally-configured SimmerClient in deposit-wallet mode."""
    from simmer_sdk.client import SimmerClient

    client = SimmerClient.__new__(SimmerClient)
    # Minimal attributes set_approvals() reads before the DW guard
    client._wallet_address = "0xb0d4000000000000000000000000000000005550"
    client._private_key = "0x" + "aa" * 32
    client._ows_wallet = None
    client._uses_deposit_wallet = True
    client._deposit_wallet_address = "0xDEAD000000000000000000000000000000000001"
    return client


def _make_eoa_client():
    """Return a minimally-configured SimmerClient NOT in deposit-wallet mode."""
    from simmer_sdk.client import SimmerClient

    client = SimmerClient.__new__(SimmerClient)
    client._wallet_address = "0xAAAA000000000000000000000000000000000001"
    client._private_key = "0x" + "bb" * 32
    client._ows_wallet = None
    client._uses_deposit_wallet = False
    client._deposit_wallet_address = None
    return client


class TestSetApprovalsDWShortCircuit:
    def test_returns_structured_response(self):
        """DW client gets a structured dict back, not an exception."""
        client = _make_dw_client()
        result = client.set_approvals()
        assert isinstance(result, dict)

    def test_backward_compat_keys_present(self):
        """set/skipped/failed/details keys must always be present."""
        result = _make_dw_client().set_approvals()
        for key in ("set", "skipped", "failed", "details"):
            assert key in result, f"missing key: {key}"

    def test_counts_are_zero(self):
        """No approvals should be registered as set/skipped/failed."""
        result = _make_dw_client().set_approvals()
        assert result["set"] == 0
        assert result["skipped"] == 0
        assert result["failed"] == 0
        assert result["details"] == []

    def test_deposit_wallet_user_flag(self):
        """deposit_wallet_user=True signals the DW path to sophisticated callers."""
        result = _make_dw_client().set_approvals()
        assert result.get("deposit_wallet_user") is True

    def test_message_present(self):
        """Human-readable message must be non-empty."""
        result = _make_dw_client().set_approvals()
        assert "message" in result
        assert len(result["message"]) > 0

    def test_message_references_dashboard(self):
        """Message must point toward the dashboard activate-trading flow."""
        result = _make_dw_client().set_approvals()
        msg = result["message"].lower()
        assert "dashboard" in msg or "activate trading" in msg.lower()

    def test_no_eth_account_import_attempted(self):
        """eth-account must not be imported — no transactions are signed."""
        client = _make_dw_client()
        with patch.dict("sys.modules", {"eth_account": None}):
            # Would raise ImportError if the import block were reached
            result = client.set_approvals()
        assert result["deposit_wallet_user"] is True

    def test_no_http_calls_made(self):
        """No network calls should be made for the DW short-circuit path."""
        client = _make_dw_client()
        client._request = MagicMock(side_effect=AssertionError("_request must not be called for DW users"))
        result = client.set_approvals()
        assert result["deposit_wallet_user"] is True
        client._request.assert_not_called()

    def test_eoa_client_not_short_circuited(self):
        """Non-DW clients must NOT hit the early-return path."""
        client = _make_eoa_client()
        # The function will proceed past the DW guard and eventually fail
        # because the test client has no _request method — that's fine, it
        # proves the guard was not triggered.
        with pytest.raises(Exception):
            client.set_approvals()
