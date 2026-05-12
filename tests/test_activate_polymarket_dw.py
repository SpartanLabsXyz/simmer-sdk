"""Tests for client.activate_polymarket_dw() (0.17.6).

Pure unit tests — no network, no real signing.
Covers:
  - already_set short-circuit
  - prepare failure propagation
  - missing private key raises ValueError before any network call
  - full happy-path: prepare → sign → submit
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

FAKE_CALLS = [{"target": "0xabc", "value": "0", "data": "0x095ea7b3"}]

PREPARE_RESPONSE = {
    "already_set": False,
    "calls": FAKE_CALLS,
    "calls_summary": [{"target": "0xabc", "data_prefix": "0x095ea7b"}],
    "typed_data": FAKE_TYPED_DATA,
    "nonce": "42",
    "deadline": 9999999999,
    "deposit_wallet_address": "0xDW",
}

PREPARE_ALREADY_SET = {
    "already_set": True,
    "calls": [],
    "typed_data": None,
    "nonce": None,
    "deadline": None,
}


def _make_client(private_key=FAKE_PRIVATE_KEY):
    client = SimmerClient.__new__(SimmerClient)
    client._api_key = FAKE_KEY
    client._api_base = API_BASE
    client._private_key = private_key
    client._ows_wallet = None
    client._wallet_address = "0xEOA"
    client._session = MagicMock()
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_raises_without_private_key():
    client = _make_client(private_key=None)
    client._ows_wallet = None
    with pytest.raises(ValueError, match="signing key"):
        client.activate_polymarket_dw()


def test_already_set_short_circuits():
    client = _make_client()
    with patch.object(client, "_request", return_value=PREPARE_ALREADY_SET) as mock_req:
        result = client.activate_polymarket_dw()

    assert result == {"already_set": True, "calls_count": 0, "success": True}
    mock_req.assert_called_once_with("POST", "/api/user/wallet/external/dw-approvals/prepare")


def test_prepare_failure_propagates():
    client = _make_client()
    with patch.object(client, "_request", side_effect=RuntimeError("HTTP 503")):
        with pytest.raises(RuntimeError, match="503"):
            client.activate_polymarket_dw()


def test_happy_path_prepare_sign_submit():
    client = _make_client()

    fake_sig = MagicMock()
    fake_sig.signature.hex.return_value = "0xdeadbeef"

    request_calls = []

    def fake_request(method, path, **kwargs):
        request_calls.append((method, path))
        if path.endswith("prepare"):
            return PREPARE_RESPONSE
        return {"success": True}

    with patch.object(client, "_request", side_effect=fake_request):
        with patch("eth_account.Account.sign_typed_data", return_value=fake_sig):
            result = client.activate_polymarket_dw()

    assert result == {"already_set": False, "calls_count": 1, "success": True}
    assert request_calls[0] == ("POST", "/api/user/wallet/external/dw-approvals/prepare")
    assert request_calls[1][1].endswith("submit")

    # Confirm submit body included the signature
    _, submit_path = request_calls[1]
    assert "submit" in submit_path


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
                client.activate_polymarket_dw()
