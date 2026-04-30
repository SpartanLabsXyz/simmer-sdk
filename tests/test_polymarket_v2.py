"""Unit tests for the V1/V2 Polymarket exchange-version flag behavior.

Covers the SDK surface added in 0.10.0 for the 2026-04-28 V2 cutover:
- `simmer_sdk.polymarket_contracts` — flag + address resolution
- `simmer_sdk.approvals` — V1 vs V2 approval tx sets
- `simmer_sdk.signing.SignedOrder` — shape-aware `to_dict()`
"""

import os
import importlib


def _reload(*mod_names):
    """Force re-import so module-level env reads pick up current state."""
    for name in mod_names:
        mod = importlib.import_module(name)
        importlib.reload(mod)


def _set_version(val):
    """Set or clear the SIMMER_POLYMARKET_EXCHANGE_VERSION env flag."""
    if val is None:
        os.environ.pop("SIMMER_POLYMARKET_EXCHANGE_VERSION", None)
    else:
        os.environ["SIMMER_POLYMARKET_EXCHANGE_VERSION"] = val
    _reload(
        "simmer_sdk.polymarket_contracts",
        "simmer_sdk.approvals",
    )


# ==================== polymarket_contracts ====================

def test_v1_flag_returns_three_spenders_usdce():
    _set_version("v1")
    from simmer_sdk.polymarket_contracts import (
        is_v2_enabled, active_spenders, collateral_token, exchange_version_str,
    )
    assert is_v2_enabled() is False
    assert exchange_version_str() == "v1"
    assert len(active_spenders()) == 3
    # USDC.e is the V1 collateral
    assert collateral_token().lower() == "0x2791bca1f2de4661ed88a30c99a7a9449aa84174"


def test_v2_explicit_returns_four_spenders_pusd():
    _set_version("v2")
    from simmer_sdk.polymarket_contracts import (
        is_v2_enabled, active_spenders, collateral_token, exchange_version_str,
    )
    assert is_v2_enabled() is True
    assert exchange_version_str() == "v2"
    # V2 adds a second NegRisk exchange
    assert len(active_spenders()) == 4
    # pUSD is the V2 collateral
    assert collateral_token().lower() == "0xc011a7e12a19f7b1f670d46f03b03f3342e82dfb"


# ==================== time-gated default (0.12.2+) ====================

def test_default_pre_cutover_signs_v1():
    """Before the cutover instant, the unset default returns V1."""
    _set_version(None)
    from datetime import datetime, timezone, timedelta
    from unittest.mock import patch
    import simmer_sdk.polymarket_contracts as pc

    one_min_before = pc.POLYMARKET_V2_CUTOVER_UTC - timedelta(minutes=1)

    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return one_min_before if tz is None else one_min_before.astimezone(tz)

    with patch.object(pc, "datetime", _FrozenDT):
        assert pc.is_v2_enabled() is False
        assert pc.exchange_version_str() == "v1"


def test_default_at_cutover_signs_v2():
    """At the exact cutover instant, the unset default returns V2."""
    _set_version(None)
    from datetime import datetime, timezone
    from unittest.mock import patch
    import simmer_sdk.polymarket_contracts as pc

    at_cutover = pc.POLYMARKET_V2_CUTOVER_UTC

    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return at_cutover if tz is None else at_cutover.astimezone(tz)

    with patch.object(pc, "datetime", _FrozenDT):
        assert pc.is_v2_enabled() is True
        assert pc.exchange_version_str() == "v2"


def test_default_post_cutover_signs_v2():
    """After the cutover instant, the unset default returns V2."""
    _set_version(None)
    from datetime import datetime, timezone, timedelta
    from unittest.mock import patch
    import simmer_sdk.polymarket_contracts as pc

    one_hour_after = pc.POLYMARKET_V2_CUTOVER_UTC + timedelta(hours=1)

    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return one_hour_after if tz is None else one_hour_after.astimezone(tz)

    with patch.object(pc, "datetime", _FrozenDT):
        assert pc.is_v2_enabled() is True


def test_env_override_wins_over_time_gate_v1():
    """Even post-cutover, SIMMER_POLYMARKET_EXCHANGE_VERSION=v1 forces V1."""
    _set_version("v1")
    from datetime import datetime, timezone, timedelta
    from unittest.mock import patch
    import simmer_sdk.polymarket_contracts as pc

    one_hour_after = pc.POLYMARKET_V2_CUTOVER_UTC + timedelta(hours=1)

    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return one_hour_after if tz is None else one_hour_after.astimezone(tz)

    with patch.object(pc, "datetime", _FrozenDT):
        assert pc.is_v2_enabled() is False


def test_env_override_wins_over_time_gate_v2():
    """Even pre-cutover, SIMMER_POLYMARKET_EXCHANGE_VERSION=v2 forces V2."""
    _set_version("v2")
    from datetime import datetime, timezone, timedelta
    from unittest.mock import patch
    import simmer_sdk.polymarket_contracts as pc

    one_hour_before = pc.POLYMARKET_V2_CUTOVER_UTC - timedelta(hours=1)

    class _FrozenDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return one_hour_before if tz is None else one_hour_before.astimezone(tz)

    with patch.object(pc, "datetime", _FrozenDT):
        assert pc.is_v2_enabled() is True


# ==================== approvals ====================

def test_v1_approvals_count_and_tokens():
    _set_version("v1")
    from simmer_sdk.approvals import get_approval_transactions
    txs = get_approval_transactions()
    # 3 spenders × (USDC + USDC.e + CTF) = 9 txs
    assert len(txs) == 9
    tokens = {tx["token"] for tx in txs}
    assert tokens == {"USDC", "USDC.e", "CTF"}


def test_v2_approvals_count_and_tokens():
    _set_version("v2")  # explicit V2 (default is time-gated as of 0.12.2)
    from simmer_sdk.approvals import get_approval_transactions
    txs = get_approval_transactions()
    # 4 spenders × (pUSD + CTF) = 8 txs
    assert len(txs) == 8
    tokens = {tx["token"] for tx in txs}
    assert tokens == {"pUSD", "CTF"}


def test_v2_get_required_approvals_tokens():
    _set_version("v2")
    from simmer_sdk.approvals import get_required_approvals
    approvals = get_required_approvals()
    tokens = {a["token"] for a in approvals}
    # V2 should never surface USDC or USDC.e as required
    assert "USDC" not in tokens
    assert "USDC.e" not in tokens
    assert tokens == {"pUSD", "CTF"}


# ==================== SignedOrder ====================

def test_signed_order_v1_shape_omits_v2_fields():
    from simmer_sdk.signing import SignedOrder
    order = SignedOrder(
        salt="1",
        maker="0x" + "aa" * 20,
        signer="0x" + "aa" * 20,
        tokenId="123",
        makerAmount="1000000",
        takerAmount="2000000",
        side="BUY",
        signatureType=0,
        signature="0xdead",
        taker="0x" + "00" * 20,
        nonce="0",
        feeRateBps="0",
        expiration="0",
        exchange_version="v1",
    )
    d = order.to_dict()
    # V1 fields present
    assert "taker" in d
    assert "nonce" in d
    assert "feeRateBps" in d
    # V2 fields absent
    assert "timestamp" not in d
    assert "metadata" not in d
    assert "builder" not in d


def test_signed_order_v2_shape_omits_v1_fields():
    from simmer_sdk.signing import SignedOrder
    order = SignedOrder(
        salt="2",
        maker="0x" + "bb" * 20,
        signer="0x" + "bb" * 20,
        tokenId="456",
        makerAmount="1000000",
        takerAmount="2000000",
        side="SELL",
        signatureType=0,
        signature="0xbeef",
        timestamp="1700000000000",
        metadata="0x" + "00" * 32,
        builder="0x" + "11" * 32,
        expiration="0",
        exchange_version="v2",
    )
    d = order.to_dict()
    # V2 fields present
    assert "timestamp" in d
    assert "metadata" in d
    assert "builder" in d
    # V1 fields absent
    assert "taker" not in d
    assert "nonce" not in d
    assert "feeRateBps" not in d


# ==================== V2 maker-precision regression ====================
# CLOB rejects FAK/FOK orders whose makerAmount has more than 2 decimals
# (raw not divisible by 10000). For tick=0.001 BUYs and many tick=0.01
# BUYs at non-cent-aligned prices, the OrderArgsV2/get_order_amounts path
# produces sub-cent maker. Routing FAK/FOK through MarketOrderArgsV2/
# build_market_order is the canonical pattern per Polymarket V2 docs.
# See _dev/active/_polymarket-rounding-precision/HISTORY.md.

_TEST_KEY = "0x" + "a" * 64
_TEST_TOKEN = "71321045679252212594626385532706912750332728571942532289631379312455583992563"


def _v2_signed(**kwargs):
    """Build a V2-signed order with a throwaway key. V2 forced via env."""
    _set_version("v2")
    from simmer_sdk.signing import build_and_sign_order
    return build_and_sign_order(
        private_key=_TEST_KEY,
        wallet_address="0x" + "11" * 20,
        token_id=_TEST_TOKEN,
        **kwargs,
    ).to_dict()


def test_v2_fak_buy_tick_0001_user_reported_case():
    """User TG report: \\$6.00 BUY on tick=0.001 NO market produced
    makerAmount=5.99767 → CLOB rejects (>2 dec). Fix routes through
    create_market_order so maker is exactly the user's intended USDC."""
    d = _v2_signed(
        side="BUY", price=0.949, size=6.0 / 0.949,
        tick_size=0.001, order_type="FAK", amount_usdc=6.0,
    )
    maker = int(d["makerAmount"])
    assert maker == 6_000_000, f"expected $6.00 maker, got {maker / 1e6}"
    assert maker % 10000 == 0, "maker must be 2-dec aligned for FAK/FOK"


def test_v2_fak_buy_tick_001_subcent_prices_round_to_cents():
    """Pre-fix, ~80% of FAK BUYs at non-cent prices produced sub-cent
    makerAmount on tick=0.01. Spot-check the worst offenders."""
    for amount, price in [(5, 0.47), (6, 0.19), (10, 0.33), (25, 0.89), (100, 0.68)]:
        d = _v2_signed(
            side="BUY", price=price, size=amount / price,
            tick_size=0.01, order_type="FAK", amount_usdc=float(amount),
        )
        maker = int(d["makerAmount"])
        assert maker == amount * 1_000_000, (
            f"amount=${amount} price={price}: expected {amount * 1_000_000} maker, got {maker}"
        )
        assert maker % 10000 == 0


def test_v2_fak_sell_maker_is_2dec_shares():
    """SELL FAK: maker=shares, must be 2-dec aligned (cents-of-share).
    Use an adversarial size with extra decimals to verify round_down kicks
    in — 10.5 alone is already 2-dec aligned and trivially passes."""
    d = _v2_signed(
        side="SELL", price=0.421, size=10.555,
        tick_size=0.001, order_type="FAK",
    )
    maker = int(d["makerAmount"])
    # round_down(10.555, 2) = 10.55 → 10_550_000 raw
    assert maker == 10_550_000, f"expected 10.55 shares maker, got {maker / 1e6}"
    assert maker % 10000 == 0


def test_v2_gtc_preserves_full_precision():
    """GTC must NOT post-round maker — CLOB validates maker = price × size
    exactly. Per HISTORY.md, that was the regression in attempts 1-4."""
    d = _v2_signed(
        side="BUY", price=0.421, size=5.68,
        tick_size=0.001, order_type="GTC",
    )
    maker = int(d["makerAmount"])
    taker = int(d["takerAmount"])
    # tick=0.001 keeps price.421 at 3 dec, so maker = 5.68 × 0.421 = 2.39128
    assert maker == 2_391_280
    assert taker == 5_680_000


def test_v2_invalid_order_type_rejected():
    import pytest
    with pytest.raises(ValueError, match="Invalid order_type"):
        _v2_signed(
            side="BUY", price=0.5, size=10,
            tick_size=0.01, order_type="GIBBERISH",
        )
