"""Unit tests for Hyperliquid HIP-4 order signing + venue adapter.

Ports the P0 spike validations into regression tests. All offline — no
funding, no network (venue transport is mocked). The keystone test is
``test_ows_path_bit_identical_to_raw_key``, which pins the spike's finding
that the OWS signing path produces a byte-for-byte identical signature to the
raw-key path (so a refactor of either can't silently diverge).

Requires the ``[hyperliquid]`` extra (hyperliquid-python-sdk).
"""

import json

import pytest

pytest.importorskip("hyperliquid", reason="requires the [hyperliquid] extra")

from eth_account import Account
from eth_account.messages import encode_typed_data

from simmer_sdk.hyperliquid_signing import (
    OUTCOME_ASSET_BASE,
    OwsHyperliquidSigner,
    RawKeyHyperliquidSigner,
    _split_signature,
    build_cancel_action,
    build_order_action,
    outcome_asset_id,
)

# Deterministic test key (throwaway — never funded).
TEST_KEY = "0x" + "11" * 32
TEST_ACCT = Account.from_key(TEST_KEY)


# --------------------------------------------------------------------------
# Asset id math
# --------------------------------------------------------------------------

def test_outcome_asset_id_yes_no():
    # Argentina WC champion = outcome 173 (live example from the spike).
    assert outcome_asset_id(173, "yes") == OUTCOME_ASSET_BASE + 1730
    assert outcome_asset_id(173, "no") == OUTCOME_ASSET_BASE + 1731
    assert outcome_asset_id(173, "YES") == OUTCOME_ASSET_BASE + 1730  # case-insensitive


# --------------------------------------------------------------------------
# Order / cancel action wire shape
# --------------------------------------------------------------------------

def test_build_order_action_wire_shape():
    asset = outcome_asset_id(173, "yes")
    action = build_order_action(asset, is_buy=True, limit_px=0.05, sz=10.0)
    assert action["type"] == "order"
    assert action["grouping"] == "na"
    (wire,) = action["orders"]
    assert wire["a"] == asset
    assert wire["b"] is True            # is_buy
    assert wire["p"] == "0.05"          # float_to_wire
    assert wire["s"] == "10"            # trailing zeros stripped
    assert wire["r"] is False           # reduce_only
    assert wire["t"] == {"limit": {"tif": "Gtc"}}


def test_build_cancel_action_shape():
    asset = outcome_asset_id(173, "yes")
    action = build_cancel_action(asset, oid=12345)
    assert action == {"type": "cancel", "cancels": [{"a": asset, "o": 12345}]}


# --------------------------------------------------------------------------
# Signature splitting
# --------------------------------------------------------------------------

def test_split_signature_normalizes_v():
    r = "ab" * 32
    s = "cd" * 32
    assert _split_signature("0x" + r + s + "1b") == {"r": "0x" + r, "s": "0x" + s, "v": 27}
    # recovery-id form (0/1) normalized to 27/28
    assert _split_signature("0x" + r + s + "00")["v"] == 27
    assert _split_signature("0x" + r + s + "01")["v"] == 28


def test_split_signature_rejects_bad_length():
    with pytest.raises(ValueError):
        _split_signature("0xdeadbeef")


# --------------------------------------------------------------------------
# Raw-key signer: signature recovers to the signer (the spike's submit check)
# --------------------------------------------------------------------------

def test_raw_key_signer_recovers():
    from hyperliquid.utils.signing import recover_agent_or_user_from_l1_action

    signer = RawKeyHyperliquidSigner(TEST_KEY)
    assert signer.address == TEST_ACCT.address

    asset = outcome_asset_id(173, "yes")
    action = build_order_action(asset, is_buy=True, limit_px=0.05, sz=10.0)
    nonce = 1_700_000_000_000
    sig = signer.sign_l1_action(action, nonce, is_mainnet=False)

    recovered = recover_agent_or_user_from_l1_action(
        action, sig, None, nonce, None, False
    )
    assert recovered == TEST_ACCT.address


# --------------------------------------------------------------------------
# KEYSTONE: OWS path is bit-identical to the raw-key path
# --------------------------------------------------------------------------

def _fake_ows_sign(wallet_name, typed_data_json):
    """Stand-in for the OWS binary: runs the real uint-coercion, then signs
    the typed data with eth_account using TEST_KEY (same key as the raw path),
    returning a packed 65-byte hex signature — exactly what OWS returns."""
    from simmer_sdk.ows_utils import _coerce_typed_data_uints

    coerced = _coerce_typed_data_uints(typed_data_json)
    signed = TEST_ACCT.sign_message(encode_typed_data(full_message=json.loads(coerced)))
    return signed.signature.hex()


def test_ows_path_bit_identical_to_raw_key(monkeypatch):
    # Patch the OWS binary touchpoints: address lookup + the signer.
    monkeypatch.setattr(
        "simmer_sdk.ows_utils.get_ows_wallet_address", lambda name: TEST_ACCT.address
    )
    monkeypatch.setattr("simmer_sdk.ows_utils.ows_sign_typed_data", _fake_ows_sign)

    raw = RawKeyHyperliquidSigner(TEST_KEY)
    ows = OwsHyperliquidSigner("test-wallet")
    assert ows.address == raw.address

    asset = outcome_asset_id(173, "yes")
    action = build_order_action(asset, is_buy=True, limit_px=0.05, sz=10.0)
    nonce = 1_700_000_000_000

    sig_raw = raw.sign_l1_action(action, nonce, is_mainnet=True)
    sig_ows = ows.sign_l1_action(action, nonce, is_mainnet=True)

    # Bit-identical r/s/v — the spike's central finding, now pinned.
    assert int(sig_ows["r"], 16) == int(sig_raw["r"], 16)
    assert int(sig_ows["s"], 16) == int(sig_raw["s"], 16)
    assert sig_ows["v"] == sig_raw["v"]


_APPROVE_AGENT_TYPES = [
    {"name": "hyperliquidChain", "type": "string"},
    {"name": "agentAddress", "type": "address"},
    {"name": "agentName", "type": "string"},
    {"name": "nonce", "type": "uint64"},
]


def test_user_signed_action_bit_identical_ows_vs_raw(monkeypatch):
    """The user-signed (approveAgent) path must also match raw vs OWS.

    Pins both fixed bugs: the primaryType must be
    'HyperliquidTransaction:ApproveAgent', and the OWS path must mutate the
    action in place so it submits signatureChainId + hyperliquidChain like the
    raw path. A mismatch here means HL would reject the OWS signature.
    """
    monkeypatch.setattr(
        "simmer_sdk.ows_utils.get_ows_wallet_address", lambda name: TEST_ACCT.address
    )
    monkeypatch.setattr("simmer_sdk.ows_utils.ows_sign_typed_data", _fake_ows_sign)

    nonce = 1_700_000_000_000

    def _action():
        return {
            "type": "approveAgent",
            "agentAddress": "0x" + "ab" * 20,
            "agentName": "",
            "nonce": nonce,
        }

    raw_action = _action()
    sig_raw = RawKeyHyperliquidSigner(TEST_KEY).sign_user_action(
        raw_action, _APPROVE_AGENT_TYPES, "HyperliquidTransaction:ApproveAgent", True
    )
    ows_action = _action()
    sig_ows = OwsHyperliquidSigner("test-wallet").sign_user_action(
        ows_action, _APPROVE_AGENT_TYPES, "HyperliquidTransaction:ApproveAgent", True
    )

    assert sig_ows == sig_raw
    # Both paths injected the domain fields in place → both submit them.
    for a in (raw_action, ows_action):
        assert a["signatureChainId"] == "0x66eee"
        assert a["hyperliquidChain"] == "Mainnet"


def test_ows_connection_id_is_hex_encoded(monkeypatch):
    """Regression for the P0 gotcha: connectionId is raw bytes32 and must be
    hex-encoded before JSON serialization, or json.dumps raises TypeError."""
    captured = {}

    def _capture(wallet_name, typed_data_json):
        captured["payload"] = json.loads(typed_data_json)
        signed = TEST_ACCT.sign_message(encode_typed_data(full_message=captured["payload"]))
        return signed.signature.hex()

    monkeypatch.setattr(
        "simmer_sdk.ows_utils.get_ows_wallet_address", lambda name: TEST_ACCT.address
    )
    monkeypatch.setattr("simmer_sdk.ows_utils.ows_sign_typed_data", _capture)

    ows = OwsHyperliquidSigner("test-wallet")
    action = build_order_action(outcome_asset_id(173, "yes"), True, 0.05, 10.0)
    ows.sign_l1_action(action, 1_700_000_000_000, is_mainnet=True)

    conn = captured["payload"]["message"]["connectionId"]
    assert isinstance(conn, str) and conn.startswith("0x") and len(conn) == 66
