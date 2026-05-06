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


def test_dw_order_maker_equals_signer_equals_dw():
    """For POLY_1271, `maker == signer == deposit_wallet`. NOT signer=EOA.

    Verified empirically against working on-chain trade
    0x05bd47c5248ee082d77e99288d95b1ed416c2dc8aca7ac6b11ec45e05cfe6d47
    (decoded 2026-05-05): calldata word[8] (maker) == word[9] (signer) ==
    deposit wallet. The CLOB error "the order signer address has to be
    the address of the API KEY" is misleading — the actual constraint is
    that maker and signer match the funder for ERC-1271 paths.

    The PolyNode docs spell this out correctly: "Set maker and signer to
    the deposit wallet address (not the EOA)." A previous SDK iteration
    used signer=EOA based on what polynode's helper returned when called
    with signer=EOA — that was a self-inflicted bug that this test pins
    against regression.
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
    assert signed.signer.lower() == dw.lower(), (
        f"DW order signer must equal maker (deposit wallet) for POLY_1271. "
        f"Expected {dw}, got {signed.signer}. If this test fails after a "
        f"polynode upgrade, the EOA-as-signer regression is back — see "
        f"docstring."
    )


def test_dw_order_inner_sig_v_is_27_or_28():
    """Solady's ecrecover in the deposit-wallet contract returns 0x0 for
    v ∈ {0, 1} and rejects the signature. polynode 0.10.3's
    create_signed_order_v2 normalizes v=27/28 down to 0/1 internally —
    we hand-roll the wrap to keep v un-normalized.

    Inner sig is the first 65 bytes of the wrapped signature. v is the
    last byte (byte 64 in 0-indexed). Pin v ∈ {27, 28} so any future
    refactor that reintroduces normalization fails this canary rather
    than the CLOB.
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
    sig_bytes = bytes.fromhex(signed.signature[2:])  # strip 0x
    inner_v = sig_bytes[64]  # innerSig is bytes 0..64; v is byte 64
    assert inner_v in (27, 28), (
        f"Inner ECDSA v must be 27 or 28 (Solady ecrecover requirement). "
        f"Got v={inner_v}. If this is 0 or 1, polynode's v-normalization "
        f"is back in our path — investigate _build_and_sign_order_v2_dw."
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


def test_dw_fak_buy_maker_amount_is_cent_aligned():
    """V2 CLOB rejects FAK/FOK orders whose maker (USDC for BUY) has more
    than 2 decimals: e.g. $5.00 BUY at price 0.53 with raw compute_amounts
    produces makerAmount=4_999_999 ($4.999999), which CLOB rejects with
    'invalid amount for market BUY.'

    The fix: Decimal-quantize amount_usd to cents BEFORE deriving maker.
    For amount_usdc=$5 at price 0.53: maker should be exactly 5_000_000.

    Codex P1 catch on the polynode-only path. Pinned here.
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
        price=0.53,
        size=9.4339622,  # ≈ 5.00 / 0.53; size derived by caller is approximate
        signature_type=3,
        deposit_wallet_address=dw,
        order_type="FAK",
        amount_usdc=5.0,  # caller's intent — used over size×price drift
    )
    maker_micros = int(signed.makerAmount)
    assert maker_micros == 5_000_000, (
        f"FAK BUY makerAmount must round to cents: 5.00 USDC = 5_000_000 "
        f"micros. Got {maker_micros}. CLOB will reject anything with sub-cent "
        f"precision."
    )
    # Maker mod 10_000 == 0 is the cent-alignment invariant.
    assert maker_micros % 10_000 == 0, (
        f"FAK BUY makerAmount must be a multiple of 10_000 micros (cent-"
        f"aligned). Got {maker_micros} which is {maker_micros % 10_000} "
        f"micros over the nearest cent."
    )


def test_dw_fak_buy_taker_floored_at_tick_precision():
    """For market BUY, effective bid = maker / taker. To ensure orders
    can fill against asks at the requested price, taker (shares) must be
    FLOORED at tick-derived precision (NOT rounded to nearest), so
    effective bid >= requested price. Round-to-nearest can land taker
    just above maker/p, putting effective bid below the ask and
    zero-filling. Codex pass 2 [P1] from the server-side rationale.
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
        price=0.7,
        size=1.4286,
        signature_type=3,
        deposit_wallet_address=dw,
        order_type="FAK",
        amount_usdc=1.0,
    )
    maker_micros = int(signed.makerAmount)
    taker_micros = int(signed.takerAmount)
    assert maker_micros == 1_000_000, f"maker should be $1.00 = 1_000_000 micros, got {maker_micros}"
    # Effective bid = maker/taker should be >= 0.7 (the requested price).
    effective_bid = maker_micros / taker_micros
    assert effective_bid >= 0.7, (
        f"Effective bid {effective_bid} < requested price 0.7. Taker was not "
        f"floored properly — round-to-nearest can put effective bid below "
        f"ask, zero-filling."
    )


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
