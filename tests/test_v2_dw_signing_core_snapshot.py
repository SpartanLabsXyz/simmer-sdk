"""Golden snapshot tests for _build_v2_dw_signed_order_core (SIM-2358).

These tests pin bit-identical SignedOrder output for _build_and_sign_order_v2_dw
pre- and post-the SIM-2358 core-extract refactor. They use a fixed private key
and mock build_order_payload_v2 to freeze the salt/timestamp, making the
eth_account RFC-6979 ECDSA output deterministic.

If any of these tests fail after a code change, the refactor changed the
signing math — investigate before relaxing the assertion.
"""

import os
import sys
from unittest.mock import patch, MagicMock

import pytest

try:
    import polynode  # noqa: F401
    POLYNODE_AVAILABLE = True
except ImportError:
    POLYNODE_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not POLYNODE_AVAILABLE,
    reason="polynode not installed — install simmer-sdk[polymarket] to run these tests",
)


# ── Fixed test fixtures ────────────────────────────────────────────────────────
# Standard Hardhat/Anvil test key #0. NOT used with real funds anywhere.
_FIXED_PRIV = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
_FIXED_EOA = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"  # Address derived from above
_FIXED_DW = "0x1234567890123456789012345678901234567890"
_FIXED_TOKEN_ID = "71321045679252212594626385532706912750332728571942532289631379312455583992563"

# Frozen payload — salt and timestamp are fixed so ECDSA output is deterministic.
_FROZEN_PAYLOAD_MESSAGE = {
    "salt": 42424242424242,
    "maker": _FIXED_DW,
    "signer": _FIXED_DW,
    "tokenId": int(_FIXED_TOKEN_ID),
    "makerAmount": 5000000,
    "takerAmount": 10000000,
    "side": 0,
    "signatureType": 3,
    "timestamp": 1716652800,
    "metadata": "0x" + "00" * 32,
    "builder": "0xed9222e433d100f617b2d2b125fd36f055ee6ebf792e44d2c522ed33e55697f8",
}
_FROZEN_PAYLOAD_DOMAIN = {
    "name": "Polymarket CTF Exchange",
    "version": "1",
    "chainId": 137,
    "verifyingContract": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
}
_ORDER_FIELD_TYPES = [
    {"name": "salt", "type": "uint256"},
    {"name": "maker", "type": "address"},
    {"name": "signer", "type": "address"},
    {"name": "tokenId", "type": "uint256"},
    {"name": "makerAmount", "type": "uint256"},
    {"name": "takerAmount", "type": "uint256"},
    {"name": "side", "type": "uint8"},
    {"name": "signatureType", "type": "uint8"},
    {"name": "timestamp", "type": "uint256"},
    {"name": "metadata", "type": "bytes32"},
    {"name": "builder", "type": "bytes32"},
]

# Golden signature captured from _build_and_sign_order_v2_dw with the frozen
# payload above. RFC-6979 determinism guarantees this is stable across runs.
_GOLDEN_SIGNATURE = "0xf8ed38f321bb4ef1e2b829e2d1442957c98e6fed1d360ee04987eb6584d38177027b6c635547a0092d6fdfa5cfec66ad0feec0ebacb24339fc4e47518a9ac9791c1a573e3617c78403b5b4b892827992f027b03d4eaf570048b8ee8cdd84d151beadc493745ff61e4cb256f694d7b1c2e6df84dbcbce6ab831a19971877ea28a8d4f726465722875696e743235362073616c742c61646472657373206d616b65722c61646472657373207369676e65722c75696e7432353620746f6b656e49642c75696e74323536206d616b6572416d6f756e742c75696e743235362074616b6572416d6f756e742c75696e743820736964652c75696e7438207369676e6174757265547970652c75696e743235362074696d657374616d702c62797465733332206d657461646174612c62797465733332206275696c6465722900ba"


def _make_frozen_mock_payload():
    mock_payload = MagicMock()
    mock_payload.domain = dict(_FROZEN_PAYLOAD_DOMAIN)
    mock_payload.types = {"Order": _ORDER_FIELD_TYPES}
    mock_payload.message = dict(_FROZEN_PAYLOAD_MESSAGE)
    return mock_payload


def _ensure_v2_enabled():
    os.environ["SIMMER_POLYMARKET_EXCHANGE_VERSION"] = "v2"
    import importlib
    import simmer_sdk.polymarket_contracts
    importlib.reload(simmer_sdk.polymarket_contracts)
    import simmer_sdk.signing
    importlib.reload(simmer_sdk.signing)


# ── Core structural invariants (independent of golden value) ──────────────────

def test_raw_key_wrapper_produces_317_byte_erc7739_sig():
    """Post-refactor raw-key wrapper produces a 317-byte ERC-7739-wrapped sig."""
    _ensure_v2_enabled()
    from simmer_sdk.signing import _build_and_sign_order_v2_dw

    with patch("polynode.trading.eip712.build_order_payload_v2",
               return_value=_make_frozen_mock_payload()):
        signed = _build_and_sign_order_v2_dw(
            private_key=_FIXED_PRIV,
            eoa_address=_FIXED_EOA,
            deposit_wallet_address=_FIXED_DW,
            token_id=_FIXED_TOKEN_ID,
            side="BUY",
            price=0.5,
            size=10.0,
            neg_risk=False,
            tick_size=0.01,
            order_type="GTC",
            builder_code=None,
            metadata=None,
        )

    sig_bytes = bytes.fromhex(signed.signature[2:])
    assert len(sig_bytes) == 317, (
        f"ERC-7739 wrap must be 317 bytes (innerSig(65) + appDomSep(32) + "
        f"contentsHash(32) + typeStr(186) + lenBytes(2)). Got {len(sig_bytes)}."
    )
    assert signed.signatureType == 3
    assert signed.maker.lower() == _FIXED_DW.lower()
    assert signed.signer.lower() == _FIXED_DW.lower()
    inner_v = sig_bytes[64]
    assert inner_v in (27, 28), (
        f"Inner ECDSA v must be 27 or 28 (Solady ecrecover). Got {inner_v}."
    )


def test_core_called_directly_produces_317_byte_sig():
    """_build_v2_dw_signed_order_core is callable directly with an injected
    sign_fn and produces the same 317-byte shape as the wrapper."""
    _ensure_v2_enabled()
    from simmer_sdk.signing import _build_v2_dw_signed_order_core
    from eth_account import Account

    account = Account.from_key(_FIXED_PRIV)

    def _sign_fn(tds):
        return bytes(Account.sign_typed_data(account.key, full_message=tds).signature)

    with patch("polynode.trading.eip712.build_order_payload_v2",
               return_value=_make_frozen_mock_payload()):
        signed = _build_v2_dw_signed_order_core(
            eoa_address=_FIXED_EOA,
            deposit_wallet_address=_FIXED_DW,
            token_id=_FIXED_TOKEN_ID,
            side="BUY",
            price=0.5,
            size=10.0,
            neg_risk=False,
            tick_size=0.01,
            order_type="GTC",
            builder_code=None,
            metadata=None,
            sign_typed_data_fn=_sign_fn,
        )

    sig_bytes = bytes.fromhex(signed.signature[2:])
    assert len(sig_bytes) == 317
    assert signed.signatureType == 3


# ── Bit-identity snapshot test ────────────────────────────────────────────────

def test_raw_key_wrapper_signature_bit_identical_to_golden():
    """Post-refactor _build_and_sign_order_v2_dw output is bit-identical to the
    pre-refactor golden snapshot captured 2026-06-16 (SIM-2358).

    Uses RFC-6979 deterministic ECDSA (eth_account default) + frozen payload to
    guarantee byte-level stability. If this fails, the refactor changed the
    signing math — do NOT relax without a full audit.
    """
    _ensure_v2_enabled()
    from simmer_sdk.signing import _build_and_sign_order_v2_dw

    with patch("polynode.trading.eip712.build_order_payload_v2",
               return_value=_make_frozen_mock_payload()):
        signed = _build_and_sign_order_v2_dw(
            private_key=_FIXED_PRIV,
            eoa_address=_FIXED_EOA,
            deposit_wallet_address=_FIXED_DW,
            token_id=_FIXED_TOKEN_ID,
            side="BUY",
            price=0.5,
            size=10.0,
            neg_risk=False,
            tick_size=0.01,
            order_type="GTC",
            builder_code=None,
            metadata=None,
        )

    # Non-signature fields must also be frozen.
    assert signed.salt == str(_FROZEN_PAYLOAD_MESSAGE["salt"])
    assert signed.tokenId == _FIXED_TOKEN_ID
    assert signed.makerAmount == str(_FROZEN_PAYLOAD_MESSAGE["makerAmount"])
    assert signed.takerAmount == str(_FROZEN_PAYLOAD_MESSAGE["takerAmount"])
    assert signed.maker.lower() == _FIXED_DW.lower()
    assert signed.signer.lower() == _FIXED_DW.lower()
    assert signed.signatureType == 3
    assert signed.exchange_version == "v2"

    # Signature byte-identity (the load-bearing assertion).
    assert signed.signature.lower() == _GOLDEN_SIGNATURE.replace(" ", "").lower(), (
        "Signature diverged from pre-refactor golden snapshot. "
        "The SIM-2358 core-extract changed the signing math — "
        "audit before relaxing this assertion."
    )


def test_core_and_wrapper_produce_identical_signature():
    """_build_v2_dw_signed_order_core and _build_and_sign_order_v2_dw produce
    byte-identical signatures for identical inputs + same private key.

    This is the structural correctness property of the SIM-2358 refactor:
    the wrapper is a thin shim; all math lives in the core.
    """
    _ensure_v2_enabled()
    from simmer_sdk.signing import (
        _build_and_sign_order_v2_dw,
        _build_v2_dw_signed_order_core,
    )
    from eth_account import Account

    account = Account.from_key(_FIXED_PRIV)

    def _sign_fn(tds):
        return bytes(Account.sign_typed_data(account.key, full_message=tds).signature)

    common_kwargs = dict(
        token_id=_FIXED_TOKEN_ID,
        side="BUY",
        price=0.5,
        size=10.0,
        neg_risk=False,
        tick_size=0.01,
        order_type="GTC",
        builder_code=None,
        metadata=None,
    )

    with patch("polynode.trading.eip712.build_order_payload_v2",
               return_value=_make_frozen_mock_payload()):
        via_wrapper = _build_and_sign_order_v2_dw(
            private_key=_FIXED_PRIV,
            eoa_address=_FIXED_EOA,
            deposit_wallet_address=_FIXED_DW,
            **common_kwargs,
        )

    with patch("polynode.trading.eip712.build_order_payload_v2",
               return_value=_make_frozen_mock_payload()):
        via_core = _build_v2_dw_signed_order_core(
            eoa_address=_FIXED_EOA,
            deposit_wallet_address=_FIXED_DW,
            sign_typed_data_fn=_sign_fn,
            **common_kwargs,
        )

    assert via_wrapper.signature == via_core.signature, (
        "Wrapper and core produced different signatures — the SIM-2358 "
        "refactor introduced a divergence between the two call paths."
    )
    assert via_wrapper.maker == via_core.maker
    assert via_wrapper.signer == via_core.signer
    assert via_wrapper.makerAmount == via_core.makerAmount
    assert via_wrapper.takerAmount == via_core.takerAmount
