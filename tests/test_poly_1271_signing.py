"""Unit tests for POLY_1271 (signature_type=3) order signing in V2.

Covers the SDK surface added in 0.15.0 for Polymarket deposit-wallet users
(SIM-1521 / parent SIM-1515). The signing path delegates to polynode for
ERC-7739 TypedDataSign wrapping; these tests pin down the call shape so a
future polynode upgrade or refactor can't silently break the contract.

Pattern mirrors `test_polymarket_v2.py`.
"""

import os
import secrets

import pytest


def _ensure_v2_enabled():
    """V2 is the default in 0.15.0 but tests run with whatever env they're
    invoked under. Force V2 explicitly for these tests."""
    os.environ["SIMMER_POLYMARKET_EXCHANGE_VERSION"] = "v2"
    import importlib
    import simmer_sdk.polymarket_contracts
    importlib.reload(simmer_sdk.polymarket_contracts)
    import simmer_sdk.signing
    importlib.reload(simmer_sdk.signing)


def _make_eoa():
    from eth_account import Account

    priv = "0x" + secrets.token_bytes(32).hex()
    return priv, Account.from_key(priv).address


def _derive_dw(eoa: str) -> str:
    from polynode.trading import derive_deposit_wallet_address

    return derive_deposit_wallet_address(eoa)


# ============================================================================
# Happy path: sig type 3 produces a correctly-shaped V2 DW order
# ============================================================================


def test_dw_order_signature_type_is_3():
    _ensure_v2_enabled()
    from simmer_sdk.signing import build_and_sign_order

    priv, eoa = _make_eoa()
    dw = _derive_dw(eoa)

    signed = build_and_sign_order(
        private_key=priv,
        wallet_address=eoa,
        token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
        side="BUY",
        price=0.5,
        size=10.0,
        signature_type=3,
        deposit_wallet_address=dw,
        order_type="GTC",
    )
    assert signed.signatureType == 3, (
        f"Expected signatureType=3 (POLY_1271), got {signed.signatureType}"
    )


def test_dw_order_maker_is_dw_signer_is_eoa():
    """Per server-side `polymarket_v2_signing.py`: 'EOA stays the signer
    but deposit_wallet_address is the maker/funder.' Pin this via test —
    the PolyNode docs say 'maker AND signer = DW' which contradicts the
    actual SDK output. The CLOB validates ERC-1271 by calling
    `isValidSignature` on the contract at `maker`; `signer` is for
    physical-signature attribution. Both must be set as below or the CLOB
    rejects with "invalid signature."
    """
    _ensure_v2_enabled()
    from simmer_sdk.signing import build_and_sign_order

    priv, eoa = _make_eoa()
    dw = _derive_dw(eoa)

    signed = build_and_sign_order(
        private_key=priv,
        wallet_address=eoa,
        token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
        side="BUY",
        price=0.5,
        size=10.0,
        signature_type=3,
        deposit_wallet_address=dw,
        order_type="GTC",
    )
    assert signed.maker.lower() == dw.lower(), (
        f"DW order maker must be the deposit wallet. Expected {dw}, got {signed.maker}"
    )
    assert signed.signer.lower() == eoa.lower(), (
        f"DW order signer must be the EOA (the address producing the actual "
        f"EIP-712 signature). Expected {eoa}, got {signed.signer}"
    )


def test_dw_order_signature_is_erc7739_wrapped():
    """ERC-7739 TypedDataSign envelope: 65 (innerSig) + 32 (appDomainSeparator)
    + 32 (contentsHash) + 186 (orderTypeString) + 2 (typeStringLength) =
    317 bytes. As hex with `0x` prefix that's 636 chars total (634 hex + 2
    for `0x`). PolyNode docs state this layout; we pin the length so a
    polynode upgrade that silently switches to a different envelope would
    fail this test rather than fail at the CLOB.
    """
    _ensure_v2_enabled()
    from simmer_sdk.signing import build_and_sign_order

    priv, eoa = _make_eoa()
    dw = _derive_dw(eoa)

    signed = build_and_sign_order(
        private_key=priv,
        wallet_address=eoa,
        token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
        side="BUY",
        price=0.5,
        size=10.0,
        signature_type=3,
        deposit_wallet_address=dw,
        order_type="GTC",
    )
    sig = signed.signature
    assert sig.startswith("0x"), f"signature must be 0x-prefixed hex, got {sig[:6]}"
    assert len(sig) == 636, (
        f"ERC-7739-wrapped signature must be 636 chars (0x + 634 hex = 317 bytes). "
        f"Got {len(sig)} chars. If polynode changed the wrapper layout, this test "
        f"is the canary — investigate before relaxing the assertion."
    )


def test_dw_order_dict_has_v2_shape():
    """V2 order shape: drops taker/nonce/feeRateBps; adds timestamp/metadata/
    builder. Identical to the EOA V2 shape — just sig type and maker/funder
    differ.
    """
    _ensure_v2_enabled()
    from simmer_sdk.signing import build_and_sign_order

    priv, eoa = _make_eoa()
    dw = _derive_dw(eoa)

    signed = build_and_sign_order(
        private_key=priv,
        wallet_address=eoa,
        token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
        side="BUY",
        price=0.5,
        size=10.0,
        signature_type=3,
        deposit_wallet_address=dw,
        order_type="GTC",
    )
    assert signed.exchange_version == "v2"
    d = signed.to_dict()
    # V2-specific fields present
    assert "timestamp" in d, "V2 order missing 'timestamp'"
    assert "metadata" in d, "V2 order missing 'metadata'"
    assert "builder" in d, "V2 order missing 'builder'"
    assert d["signatureType"] == 3
    # taker is None on the polynode-returned dict; SignedOrder.to_dict() omits None
    # fields, so taker should NOT be in the dict for the DW path.
    # (The EOA V2 path keeps taker as zero address — but DW doesn't need it.)


# ============================================================================
# Error paths: missing kwargs, invalid sig types
# ============================================================================


def test_sig_type_3_without_dw_address_raises():
    _ensure_v2_enabled()
    from simmer_sdk.signing import build_and_sign_order

    priv, eoa = _make_eoa()
    with pytest.raises(ValueError) as exc:
        build_and_sign_order(
            private_key=priv,
            wallet_address=eoa,
            token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
            side="BUY",
            price=0.5,
            size=10.0,
            signature_type=3,
            deposit_wallet_address=None,  # <-- missing
            order_type="GTC",
        )
    msg = str(exc.value).lower()
    assert "deposit_wallet_address" in msg, (
        "Error message must point at the missing kwarg by name so callers "
        "fix their call without spelunking the source."
    )


def test_invalid_sig_type_in_v2_raises():
    _ensure_v2_enabled()
    from simmer_sdk.signing import build_and_sign_order

    priv, eoa = _make_eoa()
    with pytest.raises(ValueError) as exc:
        build_and_sign_order(
            private_key=priv,
            wallet_address=eoa,
            token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
            side="BUY",
            price=0.5,
            size=10.0,
            signature_type=99,  # <-- nonsense
            order_type="GTC",
        )
    assert "signature_type" in str(exc.value).lower()


def test_v2_sig_type_2_safe_still_unsupported():
    """Sanity: we extended V2 to allow 0 OR 3, not 2. Sig type 2 (Safe)
    still raises — Safe wallets aren't a path we offer in V2.
    """
    _ensure_v2_enabled()
    from simmer_sdk.signing import build_and_sign_order

    priv, eoa = _make_eoa()
    with pytest.raises(ValueError):
        build_and_sign_order(
            private_key=priv,
            wallet_address=eoa,
            token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
            side="BUY",
            price=0.5,
            size=10.0,
            signature_type=2,  # <-- Safe, V2-unsupported
            order_type="GTC",
        )


# ============================================================================
# Regression: sig type 0 still works after the sig-type-3 changes
# ============================================================================


def test_sig_type_0_v2_eoa_still_works():
    """The pre-0.15 path stays alive — adding sig type 3 must not break the
    EOA path that 100% of users were on yesterday.
    """
    _ensure_v2_enabled()
    from simmer_sdk.signing import build_and_sign_order

    priv, eoa = _make_eoa()
    signed = build_and_sign_order(
        private_key=priv,
        wallet_address=eoa,
        token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
        side="BUY",
        price=0.5,
        size=10.0,
        signature_type=0,
        order_type="GTC",
    )
    assert signed.signatureType == 0
    assert signed.maker.lower() == eoa.lower()
    assert signed.signer.lower() == eoa.lower()
    assert signed.exchange_version == "v2"
