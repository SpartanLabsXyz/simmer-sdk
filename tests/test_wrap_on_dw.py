"""Tests for client.wrap_on_dw() (0.17.7).

Pure unit tests — no network, no real signing.
Covers:
  - no-op short-circuit when amount_units == 0
  - missing private key raises ValueError before any network call
  - prepare failure propagation
  - full happy-path: prepare → sign → submit (private-key path)
  - full happy-path: OWS path
  - submit failure propagation
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from simmer_sdk.client import SimmerClient


API_BASE = "https://api.simmer.example.com/api"
FAKE_KEY = "sk_live_testkey"
FAKE_PRIVATE_KEY = "0x" + "ab" * 32

FAKE_TYPED_DATA = {
    "domain": {"name": "DepositWallet", "version": "1", "chainId": 137},
    "types": {
        "DepositWalletBatch": [
            {"name": "calls", "type": "DepositWalletCall[]"},
            {"name": "nonce", "type": "uint256"},
            {"name": "deadline", "type": "uint256"},
        ],
        "DepositWalletCall": [
            {"name": "target", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
        ],
    },
    "primaryType": "DepositWalletBatch",
    "message": {"calls": [], "nonce": "1", "deadline": 9999999999},
}

FAKE_CALLS = [{"target": "0xOnramp", "value": "0", "data": "0x12345678"}]

PREPARE_RESPONSE = {
    "wrapped": False,
    "calls": FAKE_CALLS,
    "typed_data": FAKE_TYPED_DATA,
    "nonce": "77",
    "deadline": "9999999999",
    "amount_units": 5_000_000,  # $5.00
    "amount_usd": 5.0,
    "needs_approve": False,
    "deposit_wallet_address": "0xDW",
    "eoa_address": "0xEOA",
}

PREPARE_NO_OP = {
    "wrapped": False,
    "amount_units": 0,
    "deposit_wallet_address": "0xDW",
    "message": "No USDC.e on deposit wallet to wrap.",
}


def _make_client(private_key=FAKE_PRIVATE_KEY, ows_wallet=None):
    client = SimmerClient.__new__(SimmerClient)
    client._api_key = FAKE_KEY
    client._api_base = API_BASE
    client._private_key = private_key
    client._ows_wallet = ows_wallet
    client._wallet_address = "0xEOA"
    client._session = MagicMock()
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_raises_without_private_key():
    client = _make_client(private_key=None, ows_wallet=None)
    with pytest.raises(ValueError, match="signing key"):
        client.wrap_on_dw()


def test_no_op_when_amount_units_zero():
    client = _make_client()
    with patch.object(client, "_request", return_value=PREPARE_NO_OP) as mock_req:
        result = client.wrap_on_dw()

    assert result == {"wrapped": False, "amount_units": 0, "calls_count": 0, "success": True}
    mock_req.assert_called_once_with("POST", "/api/user/wallet/wrap-on-dw/external/prepare")


def test_prepare_failure_propagates():
    client = _make_client()
    with patch.object(client, "_request", side_effect=RuntimeError("HTTP 503")):
        with pytest.raises(RuntimeError, match="503"):
            client.wrap_on_dw()


def test_happy_path_private_key():
    client = _make_client()

    fake_sig = MagicMock()
    fake_sig.signature.hex.return_value = "0xdeadbeef"

    request_calls = []
    submit_kwargs = {}

    def fake_request(method, path, **kwargs):
        request_calls.append((method, path))
        if path.endswith("prepare"):
            return PREPARE_RESPONSE
        submit_kwargs.update(kwargs)
        return {"wrapped": True, "tx_hash": "0xabc"}

    with patch.object(client, "_request", side_effect=fake_request):
        with patch("eth_account.Account.sign_typed_data", return_value=fake_sig):
            result = client.wrap_on_dw()

    assert result == {"wrapped": True, "amount_units": 5_000_000, "calls_count": 1, "success": True}
    assert request_calls[0] == ("POST", "/api/user/wallet/wrap-on-dw/external/prepare")
    assert request_calls[1][1].endswith("submit")

    # Submit body must echo calls, nonce, deadline, amount_units, signature
    body = submit_kwargs.get("json", {})
    assert body["amount_units"] == 5_000_000
    assert body["calls"] == FAKE_CALLS
    assert body["nonce"] == "77"
    assert body["signature"].startswith("0x")


def test_happy_path_ows():
    client = _make_client(private_key=None, ows_wallet="my-agent-wallet")

    request_calls = []

    def fake_request(method, path, **kwargs):
        request_calls.append((method, path))
        if path.endswith("prepare"):
            return PREPARE_RESPONSE
        return {"wrapped": True, "tx_hash": "0xabc"}

    with patch.object(client, "_request", side_effect=fake_request):
        with patch("simmer_sdk.ows_utils.ows_sign_typed_data", return_value="0xcafebabe"):
            result = client.wrap_on_dw()

    assert result["wrapped"] is True
    assert result["success"] is True
    assert request_calls[1][1].endswith("submit")


def test_submit_failure_propagates():
    client = _make_client()
    fake_sig = MagicMock()
    fake_sig.signature.hex.return_value = "0xdeadbeef"

    def fake_request(method, path, **kwargs):
        if path.endswith("prepare"):
            return PREPARE_RESPONSE
        raise RuntimeError("relayer rejected")

    with patch.object(client, "_request", side_effect=fake_request):
        with patch("eth_account.Account.sign_typed_data", return_value=fake_sig):
            with pytest.raises(RuntimeError, match="relayer rejected"):
                client.wrap_on_dw()
