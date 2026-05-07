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
    # Use $10 at price 0.7 → ~14.28 shares (above MIN_ORDER_SIZE_SHARES).
    signed = build_and_sign_order(
        private_key=priv,
        wallet_address=eoa,
        token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
        side="BUY",
        price=0.7,
        size=14.286,
        signature_type=3,
        deposit_wallet_address=dw,
        order_type="FAK",
        amount_usdc=10.0,
    )
    maker_micros = int(signed.makerAmount)
    taker_micros = int(signed.takerAmount)
    assert maker_micros == 10_000_000, f"maker should be $10.00 = 10_000_000 micros, got {maker_micros}"
    # Effective bid = maker/taker should be >= 0.7 (the requested price).
    effective_bid = maker_micros / taker_micros
    assert effective_bid >= 0.7, (
        f"Effective bid {effective_bid} < requested price 0.7. Taker was not "
        f"floored properly — round-to-nearest can put effective bid below "
        f"ask, zero-filling."
    )


def test_dw_min_order_size_enforced():
    """V1 path raises locally on sub-MIN_ORDER_SIZE orders. The DW path
    must do the same — without this guard, sub-minimum orders sign cleanly
    here only to fail later at the CLOB with a generic error. Codex P2.
    """
    _ensure_v2_enabled()
    from simmer_sdk.signing import build_and_sign_order, MIN_ORDER_SIZE_SHARES

    priv, eoa = _make_eoa()
    dw = _derive_dw(eoa)
    # Smallest possible sub-min: 1 share at any price
    with pytest.raises(ValueError, match="below minimum"):
        build_and_sign_order(
            private_key=priv,
            wallet_address=eoa,
            token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
            side="BUY",
            price=0.5,
            size=1.0,  # below MIN_ORDER_SIZE_SHARES
            signature_type=3,
            deposit_wallet_address=dw,
            order_type="GTC",
        )


def test_dw_builder_code_bare_hex_gets_0x_prefix():
    """If POLY_BUILDER_CODE env (or caller-passed kwarg) is a 64-char bare
    hex string (no 0x prefix), the DW path must normalize it before signing.
    Otherwise downstream `msg["builder"][2:]` slices the first hex char
    instead of the prefix — silently mishashing the builder attribution.
    Codex P2. EOA V2 path already does this; DW path mirrors it now.
    """
    _ensure_v2_enabled()
    import os
    from simmer_sdk.signing import build_and_sign_order

    priv, eoa = _make_eoa()
    dw = _derive_dw(eoa)
    # Bare hex builder code (no 0x prefix). The corruption from improper
    # slicing would either fail Eip712Payload validation or produce a
    # different signature; either way we shouldn't see a successful sign.
    bare_hex = "ab" * 32  # 64 hex chars, no 0x
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
        builder_code=bare_hex,
    )
    # SignedOrder.builder must be 0x-prefixed (66 chars total).
    assert signed.builder.startswith("0x"), (
        f"DW path must normalize bare-hex builder_code to 0x-prefix. "
        f"Got builder={signed.builder!r}"
    )
    assert len(signed.builder) == 66, (
        f"Normalized builder must be 0x + 64 hex = 66 chars, got "
        f"{len(signed.builder)} chars: {signed.builder!r}"
    )
    assert signed.builder.lower() == "0x" + bare_hex.lower()


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


# ============================================================================
# Regression: GTC/GTD BUY taker precision (SIM-1620)
# ============================================================================


def test_dw_gtc_buy_taker_divisible_by_tick_precision_tick_001():
    """GTC BUY on tick=0.001: takerAmount (shares) must be divisible by 10.

    compute_amounts does round(size * 1e6) which can produce 6dp amounts
    (e.g. 5547576 for size=5.547576...) — CLOB and Simmer pre-submit
    validation require shares divisible by taker_divisor=10 for tick=0.001.

    Reported by rjreyes: 'takerAmount 5.553236 exceeds max 5 decimal
    precision (tick_size=0.001)'. SIM-1620.
    """
    _ensure_v2_enabled()
    from simmer_sdk.signing import build_and_sign_order

    priv, eoa = _make_eoa()
    dw = _derive_dw(eoa)

    # amount / price = 3.09 / 0.557 = 5.547576... (6dp float) — the exact
    # class of input that triggered the bug.
    amount = 3.09
    price = 0.557
    size = amount / price  # float division, as client.py computes it

    signed = build_and_sign_order(
        private_key=priv,
        wallet_address=eoa,
        token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
        side="BUY",
        price=price,
        size=size,
        signature_type=3,
        deposit_wallet_address=dw,
        tick_size=0.001,
        order_type="GTC",
    )
    taker_raw = int(signed.takerAmount)
    taker_divisor = 10  # 10^(6-5) for tick=0.001
    assert taker_raw % taker_divisor == 0, (
        f"GTC BUY takerAmount {taker_raw} ({taker_raw/1e6} shares) is not "
        f"divisible by {taker_divisor} — max 5 decimal precision for "
        f"tick=0.001. Simmer server validation and CLOB will reject this. "
        f"SIM-1620 regression."
    )


def test_dw_gtc_sell_maker_divisible_by_tick_precision_tick_001():
    """GTC SELL on tick=0.001: makerAmount (shares) must be divisible by 10.

    Mirrors the BUY case: compute_amounts uses round(size * 1e6) for the
    maker (shares sold), which can produce 6dp amounts.
    """
    _ensure_v2_enabled()
    from simmer_sdk.signing import build_and_sign_order

    priv, eoa = _make_eoa()
    dw = _derive_dw(eoa)

    # Use a position size that would be derived from a prior imprecise BUY,
    # producing non-divisible-by-10 shares.
    size = 5.547576  # 6dp shares — what the BUG path produced before fix

    signed = build_and_sign_order(
        private_key=priv,
        wallet_address=eoa,
        token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
        side="SELL",
        price=0.557,
        size=size,
        signature_type=3,
        deposit_wallet_address=dw,
        tick_size=0.001,
        order_type="GTC",
    )
    maker_raw = int(signed.makerAmount)
    taker_divisor = 10  # same divisor applies to shares field
    assert maker_raw % taker_divisor == 0, (
        f"GTC SELL makerAmount {maker_raw} ({maker_raw/1e6} shares) is not "
        f"divisible by {taker_divisor} — max 5 decimal precision for "
        f"tick=0.001. SIM-1620 regression."
    )


@pytest.mark.parametrize("tick_size,price,amount,expected_divisor", [
    # tick=0.1 → amount_decimals=3, share_divisor=1000
    (0.1,  0.3,  2.47, 1000),
    # tick=0.01 → amount_decimals=4, share_divisor=100
    (0.01, 0.33, 1.23, 100),
    # tick=0.001 → amount_decimals=5, share_divisor=10 (the reported case)
    (0.001, 0.557, 3.09, 10),
    # tick=0.0001 → amount_decimals=6, share_divisor=1 (trivially satisfied)
    (0.0001, 0.5555, 3.09, 1),
])
def test_dw_gtc_buy_taker_precision_all_tick_sizes(tick_size, price, amount, expected_divisor):
    """GTC BUY: takerAmount (shares) must be divisible by the tick-derived
    share_divisor = 10^(6-amount_decimals) for every supported tick_size.

    compute_amounts() does round(size * 1e6) with no tick rounding, which
    can produce shares not divisible by share_divisor for any tick.
    Trinity code review (SIM-1620) flagged tick=0.01 and tick=0.1 coverage
    as missing in the initial regression test.
    """
    _ensure_v2_enabled()
    from simmer_sdk.signing import build_and_sign_order

    priv, eoa = _make_eoa()
    dw = _derive_dw(eoa)

    size = amount / price  # float division as in client.py

    signed = build_and_sign_order(
        private_key=priv,
        wallet_address=eoa,
        token_id="71321045679252212594626385532706912750332728571942532289631379312455583992563",
        side="BUY",
        price=price,
        size=size,
        signature_type=3,
        deposit_wallet_address=dw,
        tick_size=tick_size,
        order_type="GTC",
    )
    taker_raw = int(signed.takerAmount)
    assert taker_raw % expected_divisor == 0, (
        f"GTC BUY takerAmount {taker_raw} ({taker_raw/1e6} shares) not "
        f"divisible by {expected_divisor} for tick_size={tick_size}. "
        f"SIM-1620 regression."
    )
