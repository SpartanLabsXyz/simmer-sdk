"""Tests for the SDK ext+DW redemption helper + client dispatch (0.17.0).

Covers:
  - simmer_sdk/dw_redeem.py: prepare HTTP shape, signing branch selection,
    submit HTTP shape, eoa_fallback signal handling, error wrapping.
  - simmer_sdk/client.py:redeem(): cohort detection from /agents/me cache,
    routing of external+DW callers through `_redeem_external_dw`, fallback
    to legacy /api/sdk/redeem when server is older than 0.17.0 (404 on
    prepare).

Pure unit tests — no network, no signing. The signing branch is exercised
via patching `Account.sign_typed_data` so we don't need a real key.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from simmer_sdk.client import SimmerClient
from simmer_sdk.dw_redeem import (
    DwRedeemError,
    DwRedeemPrepareError,
    DwRedeemSubmitError,
    prepare_dw_redeem,
    sign_dw_redeem_typed_data,
    submit_dw_redeem,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

API_BASE = "https://api.simmer.example.com/api"
HEADERS = {"Authorization": "Bearer test_key"}

# A representative typed_data payload — shape matches what the server's
# `dw_redeem_external.build_redeem_batch` returns. Real payload would have
# more fields populated; tests only need the structural shape.
SAMPLE_TYPED_DATA = {
    "domain": {
        "name": "DepositWallet",
        "version": "1",
        "chainId": 137,
        "verifyingContract": "0x9300000000000000000000000000000000b42a00",
    },
    "types": {
        "EIP712Domain": [
            {"name": "name", "type": "string"},
            {"name": "version", "type": "string"},
            {"name": "chainId", "type": "uint256"},
            {"name": "verifyingContract", "type": "address"},
        ],
        "Batch": [{"name": "calls", "type": "Call[]"}],
        "Call": [
            {"name": "target", "type": "address"},
            {"name": "value", "type": "uint256"},
            {"name": "data", "type": "bytes"},
        ],
    },
    "primaryType": "Batch",
    "message": {"calls": []},
}


SAMPLE_PREPARE_RESPONSE = {
    "calls": [
        {
            "target": "0xAdA100Db00Ca00073811820692005400218FcE1f",
            "value": "0",
            "data": "0x01b7037c" + "0" * 504,
        }
    ],
    "typed_data": SAMPLE_TYPED_DATA,
    "nonce": "12345",
    "deadline": "1750000000",
    "deposit_wallet_address": "0x9300000000000000000000000000000000b42a00",
    "condition_id": "0x" + "ab" * 32,
    "outcome": "no",
    "negative_risk": False,
    "is_cancelled": False,
}


def _mock_response(status_code: int, json_body=None) -> MagicMock:
    res = MagicMock(spec=requests.Response)
    res.ok = 200 <= status_code < 300
    res.status_code = status_code
    res.json.return_value = json_body or {}
    return res


# ===========================================================================
# prepare_dw_redeem
# ===========================================================================


def test_prepare_dw_redeem_posts_market_id_and_side():
    """Prepare must POST {market_id, side} to /api/sdk/dw-redeem/prepare."""
    with patch("simmer_sdk.dw_redeem.requests.post") as post:
        post.return_value = _mock_response(200, SAMPLE_PREPARE_RESPONSE)
        result = prepare_dw_redeem(
            api_url=API_BASE,
            headers=HEADERS,
            market_id="m-123",
            side="no",
        )
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == f"{API_BASE}/sdk/dw-redeem/prepare"
    body = json.loads(kwargs["data"])
    assert body == {"market_id": "m-123", "side": "no"}
    assert kwargs["headers"]["Authorization"] == "Bearer test_key"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    # Typed data round-trips verbatim — caller signs it as-is.
    assert result["typed_data"] == SAMPLE_TYPED_DATA
    assert result["nonce"] == "12345"


def test_prepare_dw_redeem_raises_on_4xx_with_detail():
    """Server detail string (string OR list-of-objects) bubbles into the
    exception message verbatim — important for surfacing things like
    'Market not closed yet'."""
    with patch("simmer_sdk.dw_redeem.requests.post") as post:
        post.return_value = _mock_response(
            400, {"detail": "Polymarket hasn't finalized yet."}
        )
        with pytest.raises(DwRedeemPrepareError) as exc_info:
            prepare_dw_redeem(
                api_url=API_BASE, headers=HEADERS,
                market_id="m-123", side="no",
            )
    assert "Polymarket hasn't finalized yet." in str(exc_info.value)
    assert exc_info.value.status_code == 400
    assert not exc_info.value.eoa_fallback


def test_prepare_dw_redeem_raises_eoa_fallback_when_server_signals():
    """SIM-1645 — server sets eoa_fallback=true when DW=0 + EOA>0. Helper
    surfaces this as a typed exception so the caller can route to the
    legacy unsigned-tx path explicitly rather than parsing response shape.
    """
    with patch("simmer_sdk.dw_redeem.requests.post") as post:
        post.return_value = _mock_response(
            200,
            {"eoa_fallback": True, "condition_id": "0x" + "ab" * 32, "outcome": "no"},
        )
        with pytest.raises(DwRedeemPrepareError) as exc_info:
            prepare_dw_redeem(
                api_url=API_BASE, headers=HEADERS,
                market_id="m-123", side="no",
            )
    assert exc_info.value.eoa_fallback is True
    assert exc_info.value.status_code == 200


def test_prepare_dw_redeem_raises_on_404_with_status_code():
    """A 404 means the server doesn't have the dw-redeem endpoints (older
    than 0.17.0). Caller (`_redeem_external_dw`) uses status_code=404 to
    fall back to the legacy /api/sdk/redeem path."""
    with patch("simmer_sdk.dw_redeem.requests.post") as post:
        post.return_value = _mock_response(404, {"detail": "Not Found"})
        with pytest.raises(DwRedeemPrepareError) as exc_info:
            prepare_dw_redeem(
                api_url=API_BASE, headers=HEADERS,
                market_id="m-123", side="no",
            )
    assert exc_info.value.status_code == 404


# ===========================================================================
# sign_dw_redeem_typed_data
# ===========================================================================


def test_sign_dw_redeem_requires_exactly_one_signing_material():
    """Either private_key or ows_wallet — not both, not neither. Defends
    against ambiguous calls where the SDK would silently pick one."""
    with pytest.raises(DwRedeemError, match="exactly one"):
        sign_dw_redeem_typed_data(SAMPLE_TYPED_DATA)
    with pytest.raises(DwRedeemError, match="exactly one"):
        sign_dw_redeem_typed_data(
            SAMPLE_TYPED_DATA, private_key="0xab", ows_wallet="my-wallet"
        )


def test_sign_dw_redeem_uses_eth_account_for_private_key():
    """The raw-key branch must call `Account.sign_typed_data(key,
    full_message=typed_data)` — `full_message` so the dict's primaryType is
    honored (matters for batch redemptions where primaryType wraps Batch).
    """
    fake_signed = MagicMock()
    fake_signed.signature.hex.return_value = "0x" + "ab" * 65
    with patch("eth_account.Account.sign_typed_data", return_value=fake_signed) as sign:
        sig = sign_dw_redeem_typed_data(
            SAMPLE_TYPED_DATA, private_key="0x" + "11" * 32
        )
    sign.assert_called_once_with("0x" + "11" * 32, full_message=SAMPLE_TYPED_DATA)
    assert sig.startswith("0x")
    assert len(sig) == 2 + 130  # 0x + 65 bytes hex


def test_sign_dw_redeem_uses_ows_for_ows_wallet():
    """The OWS branch must serialise typed_data to JSON and call
    `ows_sign_typed_data(wallet_name, json_str)`."""
    with patch(
        "simmer_sdk.ows_utils.ows_sign_typed_data", return_value="0xdead"
    ) as ows_sign:
        sig = sign_dw_redeem_typed_data(
            SAMPLE_TYPED_DATA, ows_wallet="my-vault"
        )
    ows_sign.assert_called_once()
    args, _ = ows_sign.call_args
    assert args[0] == "my-vault"
    # 2nd arg is JSON string of the typed_data — round-trip to confirm.
    assert json.loads(args[1]) == SAMPLE_TYPED_DATA
    assert sig.startswith("0x")


# ===========================================================================
# submit_dw_redeem
# ===========================================================================


def test_submit_dw_redeem_posts_signed_batch():
    """Submit must POST {market_id, side, signature, nonce, deadline, calls}
    using `nonce/deadline/calls` verbatim from prepare (the server validates
    them; callers should not edit)."""
    with patch("simmer_sdk.dw_redeem.requests.post") as post:
        post.return_value = _mock_response(
            200,
            {
                "success": True,
                "tx_hash": "0xfeedface",
                "tx_id": "tx-1",
                "payout_pusd": 5.56,
                "calls_executed": 1,
            },
        )
        result = submit_dw_redeem(
            api_url=API_BASE,
            headers=HEADERS,
            market_id="m-123",
            side="no",
            signature="0x" + "cd" * 65,
            prepared=SAMPLE_PREPARE_RESPONSE,
        )
    args, kwargs = post.call_args
    assert args[0] == f"{API_BASE}/sdk/dw-redeem/submit"
    body = json.loads(kwargs["data"])
    assert body["market_id"] == "m-123"
    assert body["side"] == "no"
    assert body["signature"] == "0x" + "cd" * 65
    assert body["nonce"] == SAMPLE_PREPARE_RESPONSE["nonce"]
    assert body["deadline"] == SAMPLE_PREPARE_RESPONSE["deadline"]
    assert body["calls"] == SAMPLE_PREPARE_RESPONSE["calls"]
    assert result["tx_hash"] == "0xfeedface"
    assert result["payout_pusd"] == 5.56


def test_submit_dw_redeem_503_suggests_re_prepare():
    """503 from the server typically means stale nonce after a prior failure
    — caller should re-prepare for a fresh signature. Helper surfaces a
    re-prepare-friendly message when no detail is provided."""
    with patch("simmer_sdk.dw_redeem.requests.post") as post:
        post.return_value = _mock_response(503, {})  # no detail
        with pytest.raises(DwRedeemSubmitError) as exc_info:
            submit_dw_redeem(
                api_url=API_BASE, headers=HEADERS,
                market_id="m-123", side="no",
                signature="0x00", prepared=SAMPLE_PREPARE_RESPONSE,
            )
    assert "try again" in str(exc_info.value).lower()
    assert exc_info.value.status_code == 503


# ===========================================================================
# Client dispatch — _refresh_cohort_cache + redeem() routing
# ===========================================================================


def _make_client(**kwargs) -> SimmerClient:
    """Construct a SimmerClient without hitting the network in __init__."""
    defaults = dict(
        api_key="test_key",
        venue="polymarket",
        live=False,  # paper mode — no auto risk-alerts call
        base_url="https://api.simmer.example.com",
    )
    defaults.update(kwargs)
    return SimmerClient(**defaults)


def test_refresh_cohort_cache_populates_fields_from_agents_me():
    """Cache miss → GET /api/sdk/agents/me → populates auto_redeem_enabled
    + wallet_ownership + wallet_uses_deposit_wallet. Hit within TTL → no-op.
    """
    client = _make_client()
    with patch.object(client, "_request") as req:
        req.return_value = {
            "auto_redeem_enabled": True,
            "wallet_ownership": "external",
            "wallet_uses_deposit_wallet": True,
            "deposit_wallet_address": "0x9300000000000000000000000000000000b42a00",
        }
        client._refresh_cohort_cache()
        client._refresh_cohort_cache()  # second call should be cache hit
    assert req.call_count == 1
    assert client._wallet_ownership == "external"
    assert client._wallet_uses_deposit_wallet is True
    assert client._auto_redeem_enabled is True


def test_refresh_cohort_cache_handles_older_server_without_fields():
    """Older servers (< 0.17.0) don't return cohort fields. SDK falls back
    to None / False — this routes the user to the legacy redeem flow,
    which now returns an actionable upgrade error from the server-side
    gate (added in the same release)."""
    client = _make_client()
    with patch.object(client, "_request") as req:
        req.return_value = {"auto_redeem_enabled": True}  # old server payload
        client._refresh_cohort_cache()
    assert client._wallet_ownership is None
    assert client._wallet_uses_deposit_wallet is False


def test_redeem_routes_external_dw_to_helper():
    """Cohort cache says external+DW → `redeem()` calls `_redeem_external_dw`
    directly, NOT `/api/sdk/redeem`."""
    client = _make_client()
    client._wallet_ownership = "external"
    client._wallet_uses_deposit_wallet = True
    client._cohort_fetched_at = time.time()  # pretend cache is fresh
    with patch.object(client, "_redeem_external_dw") as ext_dw, \
         patch.object(client, "_request") as req:
        ext_dw.return_value = {"success": True, "tx_hash": "0xfeed"}
        result = client.redeem("m-123", "no")
    ext_dw.assert_called_once_with("m-123", "no")
    # /api/sdk/redeem must NOT be called for ext+DW — old behavior bubbled
    # the (Phase 2) error through this call.
    for call in req.call_args_list:
        endpoint = call.args[1] if len(call.args) > 1 else call.kwargs.get("endpoint", "")
        assert "/api/sdk/redeem" not in endpoint or "/dw-redeem/" in endpoint


def test_redeem_managed_skips_dw_helper():
    """Cohort cache says native (managed) → `redeem()` follows the legacy
    `/api/sdk/redeem` path. Server signs + submits, returns tx_hash, no
    unsigned_tx in response."""
    client = _make_client()
    client._wallet_ownership = "native"
    client._wallet_uses_deposit_wallet = False
    client._cohort_fetched_at = time.time()
    with patch.object(client, "_redeem_external_dw") as ext_dw, \
         patch.object(client, "_request") as req:
        req.return_value = {"success": True, "tx_hash": "0xmanaged"}
        result = client.redeem("m-123", "no")
    ext_dw.assert_not_called()
    assert result["tx_hash"] == "0xmanaged"


def test_redeem_external_no_dw_skips_helper():
    """Cohort cache says external but NO DW (Cohort A external) → legacy
    unsigned-tx flow (the existing /api/sdk/redeem path that signs locally
    and broadcasts). The new helper is only for external+DW combo.
    """
    client = _make_client()
    client._wallet_ownership = "external"
    client._wallet_uses_deposit_wallet = False
    client._cohort_fetched_at = time.time()
    with patch.object(client, "_redeem_external_dw") as ext_dw, \
         patch.object(client, "_request") as req:
        # Legacy unsigned-tx response shape — managed-success branch returns
        # early without unsigned_tx; we just need to assert the dispatcher
        # didn't go to the new helper.
        req.return_value = {"success": True, "tx_hash": "0xext-direct"}
        client.redeem("m-123", "no")
    ext_dw.assert_not_called()


def test_redeem_external_dw_falls_back_to_legacy_on_404():
    """Server < 0.17.0 → /api/sdk/dw-redeem/prepare returns 404 →
    `_redeem_external_dw` clears the DW flag and recurses through `redeem()`
    so the call lands on the legacy /api/sdk/redeem path. There, the
    pre-0.17.0 server still has the old ctf_redemption.py (Phase 2) error.
    Acceptable — old SDK + old server == old behavior, no regression.

    Refactor note (codex P2 follow-up): we now dispatch via
    `_redeem_via_legacy_path` directly instead of recursing through
    `redeem()`, so cohort state is NOT mutated. Same end-state for
    callers, simpler control flow, no risk of an infinite loop if
    cohort detection misfires later.
    """
    client = _make_client(api_key="k", )
    # Set up cohort as ext+DW so the helper actually fires.
    client._wallet_ownership = "external"
    client._wallet_uses_deposit_wallet = True
    client._cohort_fetched_at = time.time()
    # Give the helper a private key so the signing-material check passes
    # before the prepare call.
    client._private_key = "0x" + "11" * 32

    with patch("simmer_sdk.dw_redeem.requests.post") as post, \
         patch.object(client, "_request") as req:
        # First call: prepare → 404 (old server)
        post.return_value = _mock_response(404, {"detail": "Not Found"})
        # After 404, _redeem_via_legacy_path is called directly. Mock the
        # /api/sdk/redeem response (managed-shape so the helper short-
        # circuits without touching the unsigned-tx broadcast path).
        req.return_value = {
            "success": False, "error": "External-wallet redemption …"
        }
        result = client.redeem("m-123", "no")

    # Exactly one prepare attempt (then 404 → fallback)
    post.assert_called_once()
    # Legacy path was hit
    assert any(
        len(c.args) > 1 and "/api/sdk/redeem" in c.args[1]
        for c in req.call_args_list
    )
    # Cohort state must be PRESERVED — _redeem_via_legacy_path bypasses
    # the cohort check, so we don't need to mutate _wallet_uses_deposit_wallet.
    # If we mutated it, the next /redeem call would skip the new ext+DW
    # path until the next cache refresh (5 min later).
    assert client._wallet_uses_deposit_wallet is True
    assert client._wallet_ownership == "external"


def test_redeem_external_dw_falls_back_to_legacy_on_eoa_fallback_signal():
    """SIM-1645 — server detects DW=0 + EOA>0 (position lives on EOA from
    sig-type-0 trade path) and returns `eoa_fallback=True`. SDK must
    recurse into the legacy /api/sdk/redeem flow which has the same
    SIM-1645 probe and routes through the unsigned-tx EOA broadcast path.

    Earlier draft (pre-codex P2) returned an error string telling the
    user to use the dashboard. Codex flagged that the existing legacy
    /api/sdk/redeem path already handles this case end-to-end — SDK
    should just recurse there. Server-side gate that previously blocked
    this recursion was removed in the same change."""
    client = _make_client()
    client._wallet_ownership = "external"
    client._wallet_uses_deposit_wallet = True
    client._cohort_fetched_at = time.time()
    client._private_key = "0x" + "11" * 32

    with patch("simmer_sdk.dw_redeem.requests.post") as post, \
         patch.object(client, "_request") as req:
        # Prepare returns the eoa_fallback signal (200 OK with the special
        # response body — handler raises DwRedeemPrepareError(eoa_fallback=True)).
        post.return_value = _mock_response(
            200,
            {"eoa_fallback": True, "condition_id": "0x" + "ab" * 32, "outcome": "no"},
        )
        # Legacy path mock — managed-success shape (no unsigned_tx) so the
        # broadcast branch isn't exercised. We're testing the dispatch.
        req.return_value = {"success": True, "tx_hash": "0xeoa-fallback"}
        result = client.redeem("m-123", "no")

    post.assert_called_once()
    # Legacy /api/sdk/redeem was called via _redeem_via_legacy_path
    assert any(
        len(c.args) > 1 and c.args[1] == "/api/sdk/redeem"
        for c in req.call_args_list
    ), "Legacy /api/sdk/redeem must be called on eoa_fallback signal"
    # Result is whatever the legacy path returned — NOT a "use dashboard" error
    assert result.get("tx_hash") == "0xeoa-fallback"
    assert "eoa_fallback" not in result, (
        "Should not surface eoa_fallback to caller — legacy path handled it."
    )
    # Cohort preserved (so future redemptions on this client still try ext+DW first)
    assert client._wallet_uses_deposit_wallet is True
