"""Tests for dual-wallet position listing and trade routing (SIM-1646).

For users with `wallet_uses_deposit_wallet=True` who have pre-migration
positions on their owner EOA:

1. get_positions() returns ALL positions (EOA + DW), each tagged with
   `holder_address` as returned by the server.
2. _build_signed_order() routes by holder:
   - holder == EOA → sig-type-0 (V2 EOA-direct)
   - holder == DW  → sig-type-3 (POLY_1271 batch)

The server (/api/sdk/positions) already merges both addresses and emits
`holder_address` per position (SIM-1646 backend). These tests verify the
SDK correctly surfaces the field and uses it for sell routing — without
any live signing or network I/O.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from simmer_sdk.client import SimmerClient, Position


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

EOA = "0x98a0000000000000000000000000000000000dfb"
DW  = "0x9300000000000000000000000000000000b42a00"

MARKET_A = "market-aaa"  # pre-migration position on EOA
MARKET_B = "market-bbb"  # post-migration position on DW


def _server_response(include_holder: bool = True) -> dict:
    """Fake /api/sdk/positions response with two Polymarket positions."""
    def _ha(addr):
        return addr if include_holder else None

    return {
        "positions": [
            {
                "market_id": MARKET_A,
                "question": "Will team A win?",
                "shares_yes": 10.0,
                "shares_no": 0.0,
                "current_value": 8.0,
                "pnl": -2.0,
                "status": "active",
                "venue": "polymarket",
                "holder_address": _ha(EOA),
            },
            {
                "market_id": MARKET_B,
                "question": "Will team B win?",
                "shares_yes": 5.0,
                "shares_no": 0.0,
                "current_value": 4.5,
                "pnl": -0.5,
                "status": "active",
                "venue": "polymarket",
                "holder_address": _ha(DW),
            },
        ]
    }


def _make_client(uses_dw: bool = True) -> SimmerClient:
    """Construct a minimal SimmerClient wired for unit tests (no network)."""
    client = SimmerClient.__new__(SimmerClient)
    client._agent_id = "test-agent"
    client._ows_wallet = None
    client._private_key = "0x" + "aa" * 32  # dummy private key (never used in routing tests)
    client._wallet_address = EOA
    client.base_url = "https://api.simmer.markets"
    client.live = True
    client.venue = "polymarket"
    client._paper_portfolio = None  # forces live path in get_positions()
    client._held_markets_cache = None
    client._held_markets_ts = 0.0
    client._position_holder_cache = {}
    client._position_holder_ts = 0.0
    client._market_data_cache = {}
    client._request = MagicMock()
    client._uses_deposit_wallet = uses_dw
    client._deposit_wallet_address = DW if uses_dw else None
    return client


# ---------------------------------------------------------------------------
# 1. get_positions() — dual-wallet surface tests
# ---------------------------------------------------------------------------


def test_list_positions_dual_wallet_returns_all_positions():
    """DW user: get_positions() returns positions from both EOA and DW."""
    client = _make_client(uses_dw=True)
    client._request.return_value = _server_response()

    positions = client.get_positions()

    assert len(positions) == 2, f"Expected 2 positions, got {len(positions)}"
    market_ids = {p.market_id for p in positions}
    assert MARKET_A in market_ids, "Pre-migration EOA position missing"
    assert MARKET_B in market_ids, "Post-migration DW position missing"


def test_list_positions_dual_wallet_tags_holder_address():
    """Each position carries the address that holds the on-chain CTF tokens."""
    client = _make_client(uses_dw=True)
    client._request.return_value = _server_response()

    positions = client.get_positions()
    by_id = {p.market_id: p for p in positions}

    assert by_id[MARKET_A].holder_address == EOA, (
        f"EOA-held position must have holder_address={EOA}, "
        f"got {by_id[MARKET_A].holder_address}"
    )
    assert by_id[MARKET_B].holder_address == DW, (
        f"DW-held position must have holder_address={DW}, "
        f"got {by_id[MARKET_B].holder_address}"
    )


def test_list_positions_dual_wallet_populates_holder_cache():
    """get_positions() side-effect: _position_holder_cache is populated for sell routing."""
    client = _make_client(uses_dw=True)
    client._request.return_value = _server_response()

    client.get_positions()

    assert client._position_holder_cache.get(f"{MARKET_A}:yes") == EOA, (
        "EOA-held YES position should be cached for sell routing"
    )
    assert client._position_holder_cache.get(f"{MARKET_B}:yes") == DW, (
        "DW-held YES position should be cached for sell routing"
    )


def test_list_positions_dual_wallet_cache_timestamp_updated():
    """get_positions() stamps _position_holder_ts so _get_holder_address() sees fresh data."""
    client = _make_client(uses_dw=True)
    client._request.return_value = _server_response()

    before = time.time()
    client.get_positions()
    after = time.time()

    assert before <= client._position_holder_ts <= after + 0.1, (
        "_position_holder_ts must be set to current time by get_positions()"
    )


def test_list_positions_non_dw_user_unaffected():
    """Non-DW users: holder_address may be None (server returns None/omits it).
    The Position object is created fine with holder_address=None."""
    client = _make_client(uses_dw=False)
    response = _server_response(include_holder=False)  # server omits holder_address
    client._request.return_value = response

    positions = client.get_positions()

    assert len(positions) == 2
    for pos in positions:
        assert pos.holder_address is None, (
            f"Non-DW user: holder_address should be None, got {pos.holder_address}"
        )


# ---------------------------------------------------------------------------
# 2. _get_holder_address() — cache behaviour
# ---------------------------------------------------------------------------


def test_get_holder_address_returns_from_fresh_cache():
    """_get_holder_address() returns from cache without a network call when fresh."""
    client = _make_client(uses_dw=True)
    client._position_holder_cache = {f"{MARKET_A}:yes": EOA}
    client._position_holder_ts = time.time()  # fresh

    result = client._get_holder_address(MARKET_A, "yes")
    assert result == EOA
    client._request.assert_not_called()


def test_get_holder_address_fetches_on_cache_miss():
    """Cache miss (ts=0): _get_holder_address() fetches positions and rebuilds cache."""
    client = _make_client(uses_dw=True)
    # Cache is empty and stale (ts=0)
    client._position_holder_cache = {}
    client._position_holder_ts = 0.0
    client._request.return_value = _server_response()

    result = client._get_holder_address(MARKET_A, "yes")

    assert result == EOA
    # get_positions(venue="polymarket") calls _request with venue param
    assert client._request.called, "_request must be called to refresh holder cache"
    call_args = client._request.call_args
    assert call_args[0] == ("GET", "/api/sdk/positions")


def test_get_holder_address_returns_none_for_non_dw_user():
    """Non-DW user: _get_holder_address() returns None immediately (no routing override)."""
    client = _make_client(uses_dw=False)

    result = client._get_holder_address(MARKET_A, "yes")

    assert result is None
    client._request.assert_not_called()


# ---------------------------------------------------------------------------
# 3. _build_signed_order() routing — sig type selection
# ---------------------------------------------------------------------------


def _fake_signed_order(sig_type: int) -> MagicMock:
    """Fake SignedOrder returned from build_and_sign_order mock."""
    order = MagicMock()
    order.signatureType = sig_type
    order.to_dict.return_value = {
        "signatureType": sig_type,
        "maker": EOA if sig_type == 0 else DW,
        "signer": EOA if sig_type == 0 else DW,
        "tokenId": "token123",
        "makerAmount": "1000000",
        "takerAmount": "2000000",
        "side": "SELL",
        "orderType": "FAK",
        "timestamp": "999999",
        "nonce": "0",
        "feeRateBps": "0",
        "signature": "0x" + "ab" * 32,
        "exchange_version": "v2",
    }
    return order


def _make_market_data(market_id: str) -> dict:
    return {
        "market_id": market_id,
        "polymarket_token_id": "token123",
        "polymarket_no_token_id": "token456",
        "tick_size": 0.01,
        "fee_rate_bps": 0,
        "polymarket_neg_risk": False,
        "external_price_yes": 0.6,
    }


def test_build_signed_order_eoa_holder_uses_sig_type_0():
    """A sell where holder_address == EOA must sign as sig-type-0 (EOA-direct)."""
    client = _make_client(uses_dw=True)
    client._market_data_cache[MARKET_A] = _make_market_data(MARKET_A)

    with patch("simmer_sdk.signing.build_and_sign_order") as mock_sign:
        mock_sign.return_value = _fake_signed_order(sig_type=0)

        client._build_signed_order(
            MARKET_A, "yes",
            amount=0, shares=10.0,
            action="sell", order_type="FAK",
            price=0.6,
            holder_address=EOA,  # pre-migration position on EOA
        )

        assert mock_sign.called, "build_and_sign_order should have been called"
        call_kwargs = mock_sign.call_args[1]
        assert call_kwargs["signature_type"] == 0, (
            f"EOA-held position must use sig-type-0. Got {call_kwargs['signature_type']}. "
            "Possible regression: holder_address routing in _build_signed_order."
        )
        assert call_kwargs.get("deposit_wallet_address") is None, (
            "sig-type-0 path must NOT pass deposit_wallet_address to builder"
        )


def test_build_signed_order_dw_holder_uses_sig_type_3():
    """A sell where holder_address == DW must sign as sig-type-3 (POLY_1271)."""
    client = _make_client(uses_dw=True)
    client._market_data_cache[MARKET_B] = _make_market_data(MARKET_B)

    with patch("simmer_sdk.signing.build_and_sign_order") as mock_sign:
        mock_sign.return_value = _fake_signed_order(sig_type=3)

        client._build_signed_order(
            MARKET_B, "yes",
            amount=0, shares=5.0,
            action="sell", order_type="FAK",
            price=0.7,
            holder_address=DW,  # post-migration position on DW
        )

        assert mock_sign.called, "build_and_sign_order should have been called"
        call_kwargs = mock_sign.call_args[1]
        assert call_kwargs["signature_type"] == 3, (
            f"DW-held position must use sig-type-3 (POLY_1271). Got {call_kwargs['signature_type']}."
        )
        assert call_kwargs.get("deposit_wallet_address") == DW, (
            "sig-type-3 path must pass deposit_wallet_address to builder"
        )


def test_build_signed_order_no_holder_dw_user_defaults_to_sig_type_3():
    """DW user, no holder_address provided → fallback to sig-type-3 (DW default)."""
    client = _make_client(uses_dw=True)
    client._market_data_cache[MARKET_B] = _make_market_data(MARKET_B)

    with patch("simmer_sdk.signing.build_and_sign_order") as mock_sign:
        mock_sign.return_value = _fake_signed_order(sig_type=3)

        client._build_signed_order(
            MARKET_B, "yes",
            amount=0, shares=5.0,
            action="sell", order_type="FAK",
            price=0.7,
            holder_address=None,  # no override — use session-level DW
        )

        assert mock_sign.called
        call_kwargs = mock_sign.call_args[1]
        assert call_kwargs["signature_type"] == 3, (
            "DW user with no holder override must default to sig-type-3"
        )


def test_build_signed_order_non_dw_user_uses_sig_type_0():
    """Non-DW user: sig-type-0 regardless of holder_address (should be None anyway)."""
    client = _make_client(uses_dw=False)
    client._market_data_cache[MARKET_A] = _make_market_data(MARKET_A)

    with patch("simmer_sdk.signing.build_and_sign_order") as mock_sign:
        mock_sign.return_value = _fake_signed_order(sig_type=0)

        client._build_signed_order(
            MARKET_A, "yes",
            amount=0, shares=10.0,
            action="sell", order_type="FAK",
            price=0.6,
            holder_address=None,
        )

        assert mock_sign.called
        call_kwargs = mock_sign.call_args[1]
        assert call_kwargs["signature_type"] == 0, (
            "Non-DW user must always use sig-type-0"
        )
