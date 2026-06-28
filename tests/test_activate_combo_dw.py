"""Tests for client.activate_combo_dw() + the place_combo DW approval pre-check
(E3 — combo deposit-wallet publish).

Pure unit tests — no network, no real signing. Covers:
  - missing signing key raises before any network call
  - prepare is POSTed with {combo: true}, and the combo approval batch passes
    the client-side validator → sign → submit happy path
  - OWS signing follows the same typed-data path as base DW activation
  - per-agent routing hits the agent dw-approvals endpoints
  - place_combo's on-chain pre-check blocks an unapproved DW with a pointer to
    activate_combo_dw(), and the _combo_dw_approved RPC helper decodes allowance
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from eth_abi import encode as _abi_encode

from simmer_sdk.client import SimmerClient
from simmer_sdk.polymarket_contracts import (
    PUSD,
    COMBO_EXCHANGE,
    COMBO_POSITION_MANAGER,
)

API_BASE = "https://api.simmer.example.com/api"
FAKE_KEY = "sk_live_testkey"
FAKE_PRIVATE_KEY = "0x" + "ab" * 32
FAKE_DW = "0x4fBd7fcC42C9b393A95E059042E7c0553B124326"
_MAX = (1 << 256) - 1

FAKE_TYPED_DATA = {
    "domain": {"name": "DepositWallet", "version": "1", "chainId": 137},
    "types": {"DepositWalletBatch": [{"name": "nonce", "type": "uint256"}]},
    "primaryType": "DepositWalletBatch",
    "message": {"nonce": "1"},
}

# The real combo approval batch: pUSD.approve(COMBO_EXCHANGE) +
# COMBO_POSITION_MANAGER.setApprovalForAll(COMBO_EXCHANGE). Must pass the
# client-side validator (else the SDK would refuse to sign the honest batch).
_COMBO_CALLS = [
    {
        "target": PUSD,
        "value": "0",
        "data": "0x095ea7b3" + _abi_encode(["address", "uint256"], [COMBO_EXCHANGE, _MAX]).hex(),
    },
    {
        "target": COMBO_POSITION_MANAGER,
        "value": "0",
        "data": "0xa22cb465" + _abi_encode(["address", "bool"], [COMBO_EXCHANGE, True]).hex(),
    },
]

PREPARE_COMBO = {
    "already_set": False,
    "calls": _COMBO_CALLS,
    "typed_data": FAKE_TYPED_DATA,
    "nonce": "42",
    "deadline": 9999999999,
    "deposit_wallet_address": FAKE_DW,
}


def _make_client(private_key=FAKE_PRIVATE_KEY, *, dw=False, live=False):
    client = SimmerClient.__new__(SimmerClient)
    client._api_key = FAKE_KEY
    client._api_base = API_BASE
    client._private_key = private_key
    client._ows_wallet = None
    client._wallet_address = "0xEOA"
    client._session = MagicMock()
    client.live = live
    client._uses_deposit_wallet = dw
    client._deposit_wallet_address = FAKE_DW if dw else None
    client._clob_client = None
    return client


# --- activate_combo_dw -----------------------------------------------------


def test_requires_signing_key():
    client = _make_client(private_key=None)
    with pytest.raises(ValueError, match="requires a signing key"):
        client.activate_combo_dw()


def test_prepare_sends_combo_flag_and_submits():
    client = _make_client()
    fake_sig = MagicMock()
    fake_sig.signature.hex.return_value = "0xdeadbeef"
    calls_seen = []

    def fake_request(method, path, **kwargs):
        calls_seen.append((method, path, kwargs.get("json")))
        if path.endswith("prepare"):
            return PREPARE_COMBO
        return {"success": True, "all_set": True}

    with patch.object(client, "_request", side_effect=fake_request):
        with patch("eth_account.Account.sign_typed_data", return_value=fake_sig):
            result = client.activate_combo_dw()

    assert result == {"already_set": False, "calls_count": 2, "success": True}
    # prepare carried {combo: True} on the user-primary endpoint
    method, path, body = calls_seen[0]
    assert path == "/api/user/wallet/external/dw-approvals/prepare"
    assert body == {"combo": True}
    # then submit
    assert calls_seen[1][1].endswith("/dw-approvals/submit")


def test_ows_signing_supported_for_combo_activation():
    client = _make_client(private_key=None)
    client._ows_wallet = "herman-v3"
    submit_payload = {}

    def fake_request(method, path, **kwargs):
        if path.endswith("prepare"):
            return PREPARE_COMBO
        submit_payload.update(kwargs.get("json") or {})
        return {"success": True, "all_set": True}

    with patch.object(client, "_request", side_effect=fake_request):
        with patch("simmer_sdk.ows_utils.ows_sign_typed_data", return_value="cafebabe") as sign:
            result = client.activate_combo_dw()

    assert result == {"already_set": False, "calls_count": 2, "success": True}
    assert submit_payload["signature"] == "0xcafebabe"
    signed_wallet, signed_message = sign.call_args.args
    assert signed_wallet == "herman-v3"
    assert '"DepositWalletBatch"' in signed_message


def test_per_agent_routing():
    client = _make_client()
    fake_sig = MagicMock()
    fake_sig.signature.hex.return_value = "0xfeed"
    paths = []

    def fake_request(method, path, **kwargs):
        paths.append(path)
        if path.endswith("prepare"):
            return PREPARE_COMBO
        return {"success": True}

    with patch.object(client, "_request", side_effect=fake_request):
        with patch("eth_account.Account.sign_typed_data", return_value=fake_sig):
            client.activate_combo_dw(agent_id="abc-123")

    assert paths[0] == "/api/user/agent/abc-123/wallet/external/dw-approvals/prepare"
    assert paths[1].endswith("/agent/abc-123/wallet/external/dw-approvals/submit")


def test_already_set_short_circuits():
    client = _make_client()
    with patch.object(client, "_request", return_value={"already_set": True}) as mock_req:
        result = client.activate_combo_dw()
    assert result == {"already_set": True, "calls_count": 0, "success": True}
    mock_req.assert_called_once()


# --- place_combo DW pre-check ---------------------------------------------


def test_place_combo_dw_unapproved_blocks_with_activate_hint():
    client = _make_client(dw=True, live=True)
    with patch.object(client, "_combo_dw_approved", return_value=False):
        with pytest.raises(ValueError, match="activate_combo_dw"):
            client.place_combo(
                leg_position_ids=["111", "222"], size_usdc=2.0,
                dry_run=False,
            )


def test_place_combo_dw_precheck_skipped_when_allow_flag():
    """allow_deposit_wallet=True skips the pre-check entirely (no RPC read)."""
    client = _make_client(dw=True, live=True)
    with patch.object(client, "_combo_dw_approved", side_effect=AssertionError("should not be called")):
        # Will fail later at creds/network, but must NOT raise the pre-check
        # ValueError nor call _combo_dw_approved.
        with pytest.raises(Exception) as ei:
            client.place_combo(
                leg_position_ids=["111", "222"], size_usdc=2.0,
                dry_run=False, allow_deposit_wallet=True,
            )
        assert "activate_combo_dw" not in str(ei.value)


def test_place_combo_loads_per_agent_dw_state_for_sig3():
    """Regression (caught in E3 live verify): a per-agent API key carries its DW
    state on /api/sdk/agents/me, not /api/sdk/settings. place_combo must load it
    so the combo signs as sig_type 3 (maker=DW), not EOA sig0. Before the fix the
    dry-run plan resolved sig0 / maker=EOA."""
    client = _make_client(dw=False, live=True)  # settings says no DW (user-primary view)
    me_payload = {
        "per_agent_wallet_address": "0xEOA",
        "per_agent_deposit_wallet_address": FAKE_DW,
        "per_agent_dw_active": True,
    }
    with patch.object(client, "_request", return_value=me_payload):
        plan = client.place_combo(leg_position_ids=["111", "222"], size_usdc=1.0, dry_run=True)
    assert plan["identity"]["signature_type"] == 3
    assert plan["identity"]["maker_address"] == FAKE_DW


def test_combo_dw_approved_decodes_allowance():
    client = _make_client(dw=True)
    # Non-zero allowance → approved
    with patch.object(client, "_request", return_value={"result": "0x" + "f" * 64}):
        assert client._combo_dw_approved(FAKE_DW) is True
    # Zero allowance → not approved
    with patch.object(client, "_request", return_value={"result": "0x" + "0" * 64}):
        assert client._combo_dw_approved(FAKE_DW) is False
    # Empty / RPC hiccup → None (don't block)
    with patch.object(client, "_request", return_value={"result": "0x"}):
        assert client._combo_dw_approved(FAKE_DW) is None
    with patch.object(client, "_request", side_effect=RuntimeError("rpc down")):
        assert client._combo_dw_approved(FAKE_DW) is None
