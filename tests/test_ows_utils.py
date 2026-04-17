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


def _fake_ows_module(**method_returns):
    """Build a minimal stub `ows` module for tests where the real package isn't installed."""
    import types
    from unittest.mock import MagicMock

    fake = types.ModuleType("ows")
    for name, return_value in method_returns.items():
        setattr(fake, name, MagicMock(return_value=return_value))
    return fake


def test_ows_sign_transaction_mocked():
    """ows_sign_transaction passes args correctly and returns dict with signature + recovery_id."""
    fake_ows = _fake_ows_module(sign_transaction={"signature": "abcd1234", "recovery_id": 1})
    with patch.dict("sys.modules", {"ows": fake_ows}):
        from simmer_sdk.ows_utils import ows_sign_transaction
        result = ows_sign_transaction("test-wallet", "0xdeadbeef")

    fake_ows.sign_transaction.assert_called_once_with(
        wallet="test-wallet", chain="polygon", tx_hex="0xdeadbeef"
    )
    assert result == {"signature": "abcd1234", "recovery_id": 1}


def test_ows_send_transaction_mocked_default_rpc():
    """ows_send_transaction passes args correctly and returns dict with tx_hash."""
    tx_hash = "0x" + "ab" * 32
    fake_ows = _fake_ows_module(sign_and_send={"tx_hash": tx_hash})
    with patch.dict("sys.modules", {"ows": fake_ows}):
        from simmer_sdk.ows_utils import ows_send_transaction
        result = ows_send_transaction("test-wallet", "0xdeadbeef")

    fake_ows.sign_and_send.assert_called_once_with(
        wallet="test-wallet", chain="polygon", tx_hex="0xdeadbeef", rpc_url=None
    )
    assert result == {"tx_hash": tx_hash}


def test_ows_send_transaction_mocked_custom_rpc():
    """ows_send_transaction forwards custom rpc_url to OWS."""
    fake_ows = _fake_ows_module(sign_and_send={"tx_hash": "0xfeedface"})
    with patch.dict("sys.modules", {"ows": fake_ows}):
        from simmer_sdk.ows_utils import ows_send_transaction
        result = ows_send_transaction("w", "0x00", rpc_url="https://custom.rpc/v1")

    fake_ows.sign_and_send.assert_called_once_with(
        wallet="w", chain="polygon", tx_hex="0x00", rpc_url="https://custom.rpc/v1"
    )
    assert result["tx_hash"] == "0xfeedface"


def test_ows_sign_typed_tx_assembles_signed_envelope():
    """ows_sign_typed_tx produces a recoverable EIP-1559 signed envelope using OWS's signature."""
    from eth_account import Account
    import rlp

    # Build a sample EIP-1559 tx dict
    tx = {
        "chainId": 137, "nonce": 7,
        "maxFeePerGas": 100_000_000_000, "maxPriorityFeePerGas": 30_000_000_000,
        "gas": 100000, "to": "0x" + "11" * 20, "value": 0, "data": b"\xab\xcd",
        "type": 2,
    }

    # Sign with a real key first to capture what OWS would return for this tx
    real_acc = Account.create()
    real_signed = Account.sign_transaction(tx, real_acc.key)
    real_raw = bytes(real_signed.raw_transaction)
    real_decoded = rlp.decode(real_raw[1:])
    # The "OWS-returned" signature: r || s || v (65 bytes)
    real_r = real_decoded[-2].rjust(32, b"\x00")
    real_s = real_decoded[-1].rjust(32, b"\x00")
    real_v = real_decoded[-3] or b"\x00"  # rlp encodes 0 as empty bytes
    fake_signature_hex = (real_r + real_s + real_v).hex()

    fake_ows = _fake_ows_module(sign_transaction={"signature": fake_signature_hex, "recovery_id": real_v[0]})
    with patch.dict("sys.modules", {"ows": fake_ows}):
        from simmer_sdk.ows_utils import ows_sign_typed_tx
        signed_hex = ows_sign_typed_tx("test-wallet", tx)

    # Should produce the same signed envelope as the real key would have
    assert signed_hex == "0x" + real_raw.hex()
    # And OWS was called once with the unsigned envelope (0x02 prefix + 9-field RLP)
    fake_ows.sign_transaction.assert_called_once()
    call_kwargs = fake_ows.sign_transaction.call_args.kwargs
    assert call_kwargs["wallet"] == "test-wallet"
    assert call_kwargs["chain"] == "polygon"
    assert call_kwargs["tx_hex"].startswith("0x02")


def test_ows_sign_typed_tx_strips_leading_zeros_in_rs():
    """Regression: r/s with leading zero bytes must be encoded canonically (no leading zeros).

    Polygon RPC rejects non-canonical RLP with: "rlp: non-canonical integer
    (leading zero bytes) for *big.Int". Caught in live test 2026-04-17.
    """
    import rlp
    # Build a fake OWS signature where r has 2 leading zero bytes.
    # If the code doesn't strip, rlp.encode will produce a 32-byte field where
    # canonical encoding requires 30 bytes. The decoded RLP will differ.
    fake_r = bytes(2) + bytes.fromhex("aa" * 30)  # 32 bytes, 2 leading zeros
    fake_s = bytes.fromhex("bb" * 32)  # 32 bytes, no leading zeros
    fake_v = bytes([1])
    fake_sig = (fake_r + fake_s + fake_v).hex()

    fake_ows = _fake_ows_module(sign_transaction={"signature": fake_sig, "recovery_id": 1})
    with patch.dict("sys.modules", {"ows": fake_ows}):
        from simmer_sdk.ows_utils import ows_sign_typed_tx
        signed_hex = ows_sign_typed_tx("test-wallet", {
            "chainId": 137, "nonce": 1,
            "maxFeePerGas": 100_000_000_000, "maxPriorityFeePerGas": 30_000_000_000,
            "gas": 100000, "to": "0x" + "11" * 20, "value": 0, "data": b"", "type": 2,
        })

    # Decode the resulting envelope and verify r is encoded canonically (30 bytes, not 32)
    signed_bytes = bytes.fromhex(signed_hex[2:])
    assert signed_bytes[0] == 0x02
    decoded = rlp.decode(signed_bytes[1:])
    assert len(decoded) == 12, f"expected 12 fields, got {len(decoded)}"
    encoded_r = decoded[-2]
    assert len(encoded_r) == 30, f"r should be canonical 30 bytes after lstrip, got {len(encoded_r)}"
    assert encoded_r == bytes.fromhex("aa" * 30)


def test_ows_sign_typed_tx_rejects_legacy():
    """ows_sign_typed_tx raises if the tx isn't EIP-1559 type 2."""
    fake_ows = _fake_ows_module(sign_transaction={"signature": "00" * 65, "recovery_id": 0})
    with patch.dict("sys.modules", {"ows": fake_ows}):
        from simmer_sdk.ows_utils import ows_sign_typed_tx
        legacy_tx = {
            "chainId": 137, "nonce": 1, "gasPrice": 100_000_000_000,
            "gas": 21000, "to": "0x" + "11" * 20, "value": 0, "data": b"",
            # no type field → eth_account treats as legacy
        }
        with pytest.raises(ValueError, match="EIP-1559"):
            ows_sign_typed_tx("w", legacy_tx)


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
