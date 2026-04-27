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
