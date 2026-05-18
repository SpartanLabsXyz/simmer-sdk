"""
Tests for set_approvals() / ensure_approvals() managed-wallet friendly-result
short-circuit (SIM-1976).

Managed-wallet users (Simmer custodies the key) have no local private_key to
provide, so the old `raise ValueError("No wallet configured. Initialize
client with private_key.")` told them to do something they can't. Both
methods now probe `/api/sdk/settings`; when `wallet_ownership == "native"`,
they return a structured `{managed: True, ...}` response.

External-wallet users with no key configured must still get the existing
`ValueError` — no behavior change for them.
"""

from unittest.mock import MagicMock

import pytest


def _make_no_wallet_client():
    """SimmerClient with no local wallet — simulates a managed user OR
    an external user who forgot to set WALLET_PRIVATE_KEY. The branch is
    decided by what /api/sdk/settings returns."""
    from simmer_sdk.client import SimmerClient

    client = SimmerClient.__new__(SimmerClient)
    client._wallet_address = None
    client._private_key = None
    client._ows_wallet = None
    client._uses_deposit_wallet = False
    client._deposit_wallet_address = None
    return client


def _settings_for(ownership: str, wallet: str = "0xAAAA000000000000000000000000000000000001",
                  uses_dw: bool = False) -> dict:
    return {
        "sdk_real_trading_enabled": True,
        "wallet_address": wallet,
        "wallet_ownership": ownership,
        "polymarket_allowances_set": True,
        "wallet_uses_deposit_wallet": uses_dw,
        "deposit_wallet_address": None,
    }


class TestSetApprovalsManagedShortCircuit:
    def test_managed_returns_structured_response(self):
        """Managed user — set_approvals returns dict, not raise."""
        client = _make_no_wallet_client()
        client._request = MagicMock(return_value=_settings_for("native"))
        result = client.set_approvals()
        assert isinstance(result, dict)

    def test_managed_marks_managed_true(self):
        client = _make_no_wallet_client()
        client._request = MagicMock(return_value=_settings_for("native"))
        assert client.set_approvals().get("managed") is True

    def test_managed_counts_are_zero(self):
        client = _make_no_wallet_client()
        client._request = MagicMock(return_value=_settings_for("native"))
        result = client.set_approvals()
        assert result["set"] == 0
        assert result["skipped"] == 0
        assert result["failed"] == 0

    def test_managed_message_points_at_server(self):
        client = _make_no_wallet_client()
        client._request = MagicMock(return_value=_settings_for("native"))
        msg = client.set_approvals().get("message", "")
        # Don't pin exact wording, but the user should see the structural
        # facts: server-handled, no SDK action, dashboard fallback.
        assert "managed" in msg.lower()
        assert "server" in msg.lower()
        assert "simmer.markets" in msg.lower()

    def test_managed_no_tx_submitted(self):
        """Confirm we never call _send_tx / _request to /api/tx/* or
        try to import eth-account on the managed path."""
        client = _make_no_wallet_client()
        # If anything other than /api/sdk/settings is requested, fail loudly
        client._request = MagicMock(side_effect=lambda method, path, **kw:
            _settings_for("native") if path == "/api/sdk/settings"
            else (_ for _ in ()).throw(AssertionError(f"unexpected request: {method} {path}")))
        # No exception — only the settings probe should have run
        client.set_approvals()


class TestSetApprovalsExternalNoKeyStillRaises:
    def test_external_no_key_raises_value_error(self):
        """External user with no local key — must still raise (their
        misconfiguration is real). SIM-1976 only widens the managed path."""
        client = _make_no_wallet_client()
        client._request = MagicMock(return_value=_settings_for("external"))
        with pytest.raises(ValueError, match="No wallet configured"):
            client.set_approvals()

    def test_settings_probe_failure_falls_through_to_raise(self):
        """If /api/sdk/settings throws (network blip, server down),
        fall through to the legacy raise — conservative, no silent no-op."""
        client = _make_no_wallet_client()
        client._request = MagicMock(side_effect=RuntimeError("network down"))
        with pytest.raises(ValueError, match="No wallet configured"):
            client.set_approvals()

    def test_settings_missing_ownership_falls_through_to_raise(self):
        """Older server that doesn't return wallet_ownership at all —
        treat as unknown and raise."""
        client = _make_no_wallet_client()
        client._request = MagicMock(return_value={
            "sdk_real_trading_enabled": True,
            "wallet_address": "0xAAAA000000000000000000000000000000000001",
        })
        with pytest.raises(ValueError, match="No wallet configured"):
            client.set_approvals()


class TestEnsureApprovalsManagedShortCircuit:
    def test_managed_returns_ready_true(self):
        """ensure_approvals on managed user — ready=True, no missing_transactions."""
        client = _make_no_wallet_client()
        client._request = MagicMock(return_value=_settings_for("native"))
        result = client.ensure_approvals()
        assert result.get("ready") is True
        assert result.get("missing_transactions") == []
        assert result.get("managed") is True

    def test_managed_guide_explains(self):
        client = _make_no_wallet_client()
        client._request = MagicMock(return_value=_settings_for("native"))
        guide = client.ensure_approvals().get("guide", "")
        assert "managed" in guide.lower()
        assert "server" in guide.lower()

    def test_external_no_key_still_raises(self):
        client = _make_no_wallet_client()
        client._request = MagicMock(return_value=_settings_for("external"))
        with pytest.raises(ValueError, match="No wallet configured"):
            client.ensure_approvals()
