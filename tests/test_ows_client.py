"""Tests for OWS wallet integration in SimmerClient."""

import pytest
from unittest.mock import patch, MagicMock


def test_client_ows_wallet_param():
    """Client accepts ows_wallet parameter and resolves address."""
    with patch("simmer_sdk.ows_utils.get_ows_wallet_address", return_value="0xABCD1234abcd1234abcd1234abcd1234abcd1234"):
        from simmer_sdk.client import SimmerClient
        client = SimmerClient(api_key="sk_test_123", ows_wallet="my-wallet")
        assert client._ows_wallet == "my-wallet"
        assert client._wallet_address == "0xABCD1234abcd1234abcd1234abcd1234abcd1234"


def test_client_ows_wallet_env_var():
    """Client auto-detects OWS_WALLET env var."""
    with patch("simmer_sdk.ows_utils.get_ows_wallet_address", return_value="0xABCD1234abcd1234abcd1234abcd1234abcd1234"):
        with patch.dict("os.environ", {"OWS_WALLET": "env-wallet"}, clear=False):
            from simmer_sdk.client import SimmerClient
            client = SimmerClient(api_key="sk_test_123")
            assert client._ows_wallet == "env-wallet"


def test_client_ows_wallet_priority_over_env():
    """ows_wallet param takes priority over OWS_WALLET env var."""
    with patch("simmer_sdk.ows_utils.get_ows_wallet_address", return_value="0xABCD1234abcd1234abcd1234abcd1234abcd1234"):
        with patch.dict("os.environ", {"OWS_WALLET": "env-wallet"}, clear=False):
            from simmer_sdk.client import SimmerClient
            client = SimmerClient(api_key="sk_test_123", ows_wallet="param-wallet")
            assert client._ows_wallet == "param-wallet"


def test_client_ows_wallet_priority_over_private_key():
    """OWS wallet takes priority over WALLET_PRIVATE_KEY env var."""
    with patch("simmer_sdk.ows_utils.get_ows_wallet_address", return_value="0xABCD1234abcd1234abcd1234abcd1234abcd1234"):
        with patch.dict("os.environ", {"WALLET_PRIVATE_KEY": "0x" + "a" * 64}, clear=False):
            from simmer_sdk.client import SimmerClient
            client = SimmerClient(api_key="sk_test_123", ows_wallet="my-wallet")
            assert client._ows_wallet == "my-wallet"
            assert client._private_key is None  # OWS takes precedence


def test_client_ows_wallet_not_found():
    """Client raises ValueError if OWS wallet doesn't exist."""
    with patch("simmer_sdk.ows_utils.get_ows_wallet_address", side_effect=ValueError("not found")):
        from simmer_sdk.client import SimmerClient
        with pytest.raises(ValueError, match="OWS wallet error"):
            SimmerClient(api_key="sk_test_123", ows_wallet="nonexistent")


def test_client_ows_not_installed():
    """Client falls back gracefully if OWS package not installed."""
    with patch("simmer_sdk.ows_utils.get_ows_wallet_address", side_effect=ImportError("no ows")):
        # Patch at the import site inside client.py
        with patch.dict("os.environ", {"OWS_WALLET": "env-wallet"}, clear=False):
            from simmer_sdk.client import SimmerClient
            # Should not raise — just warns and falls through
            client = SimmerClient(api_key="sk_test_123")
            assert client._ows_wallet is None


def test_client_no_ows_no_key():
    """Client works without OWS or private key (sim venue)."""
    # Make sure OWS_WALLET is not set
    with patch.dict("os.environ", {}, clear=False):
        import os
        os.environ.pop("OWS_WALLET", None)
        os.environ.pop("WALLET_PRIVATE_KEY", None)
        os.environ.pop("SIMMER_PRIVATE_KEY", None)
        from simmer_sdk.client import SimmerClient
        client = SimmerClient(api_key="sk_test_123")
        assert client._ows_wallet is None
        assert client._private_key is None
