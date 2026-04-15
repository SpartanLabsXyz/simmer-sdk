"""Tests for OWS utility module."""

import pytest
from unittest.mock import patch


def test_is_ows_available_returns_bool():
    """OWS availability check returns a boolean."""
    from simmer_sdk.ows_utils import is_ows_available
    result = is_ows_available()
    assert isinstance(result, bool)


def test_check_ows_false_when_not_installed():
    """OWS reports unavailable when package missing."""
    with patch.dict("sys.modules", {"ows": None}):
        from simmer_sdk.ows_utils import _check_ows
        assert _check_ows() is False


def test_get_ows_wallet_address():
    """get_ows_wallet_address returns EVM address for a wallet name."""
    pytest.importorskip("ows")
    from simmer_sdk.ows_utils import get_ows_wallet_address
    address = get_ows_wallet_address("test-polymarket")
    assert address.startswith("0x")
    assert len(address) == 42


def test_get_ows_wallet_address_missing():
    """get_ows_wallet_address raises ValueError for nonexistent wallet."""
    pytest.importorskip("ows")
    from simmer_sdk.ows_utils import get_ows_wallet_address
    with pytest.raises(ValueError, match="not found"):
        get_ows_wallet_address("nonexistent-wallet-xyz")


def test_get_ows_wallet_address_no_evm():
    """get_ows_wallet_address raises if wallet has no EVM account."""
    pytest.importorskip("ows")
    from simmer_sdk.ows_utils import get_ows_wallet_address
    with patch("ows.get_wallet", return_value={"accounts": [{"chain_id": "solana:mainnet", "address": "abc"}]}):
        with pytest.raises(ValueError, match="No EVM account"):
            get_ows_wallet_address("solana-only")


def test_ows_sign_typed_data():
    """ows_sign_typed_data returns a hex signature string."""
    pytest.importorskip("ows")
    from simmer_sdk.ows_utils import ows_sign_typed_data
    import json

    # Minimal valid EIP-712 typed data
    typed_data = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "Test": [
                {"name": "value", "type": "uint256"},
            ],
        },
        "primaryType": "Test",
        "domain": {
            "name": "Test",
            "version": "1",
            "chainId": 137,
        },
        "message": {
            "value": 42,
        },
    }

    sig = ows_sign_typed_data("test-polymarket", json.dumps(typed_data))
    assert isinstance(sig, str)
    assert len(sig) > 0


def test_ows_sign_message():
    """ows_sign_message returns a hex signature string."""
    pytest.importorskip("ows")
    from simmer_sdk.ows_utils import ows_sign_message

    sig = ows_sign_message("test-polymarket", "hello simmer")
    assert isinstance(sig, str)
    assert len(sig) > 0


def test_build_clob_auth_typed_data():
    """ClobAuth typed data has correct EIP-712 structure."""
    from simmer_sdk.ows_utils import _build_clob_auth_typed_data
    import json

    result = json.loads(_build_clob_auth_typed_data(
        "0xABCD1234abcd1234abcd1234abcd1234abcd1234", 1234567890, 0
    ))
    assert result["primaryType"] == "ClobAuth"
    assert "ClobAuth" in result["types"]
    assert result["domain"]["name"] == "ClobAuthDomain"
    assert result["domain"]["chainId"] == 137
    assert result["message"]["address"] == "0xABCD1234abcd1234abcd1234abcd1234abcd1234"
    assert result["message"]["timestamp"] == "1234567890"
    assert result["message"]["nonce"] == 0


def test_clob_auth_signature_recovers():
    """ClobAuth signature from OWS recovers to the wallet address."""
    pytest.importorskip("ows")
    pytest.importorskip("eth_account")
    from simmer_sdk.ows_utils import _build_clob_auth_typed_data, ows_sign_typed_data, get_ows_wallet_address
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    import json

    address = get_ows_wallet_address("test-polymarket")
    typed_data_json = _build_clob_auth_typed_data(address, 1234567890, 0)
    sig = ows_sign_typed_data("test-polymarket", typed_data_json)

    typed_data = json.loads(typed_data_json)
    message = encode_typed_data(full_message=typed_data)
    recovered = Account.recover_message(
        message, signature=bytes.fromhex(sig.replace("0x", ""))
    )
    assert recovered.lower() == address.lower()
