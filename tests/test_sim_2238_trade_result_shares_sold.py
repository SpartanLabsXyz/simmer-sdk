"""SIM-2238 regression: TradeResult must expose shares_sold for sells.

Before SIM-2238 the dataclass only carried `shares_bought`, so a SIM sell
that filled 18.16 shares came back as `shares_bought=0, shares_sold=0,
shares_requested=0` — leaving agents no clean field to read filled shares
from. The server already returned `shares_sold` correctly; the SDK parser
just ignored it.
"""

from __future__ import annotations

import pytest

from simmer_sdk.client import SimmerClient, TradeResult


def _make_client(venue: str = "sim") -> SimmerClient:
    """Live-mode client with network calls stubbed out."""
    client = SimmerClient.__new__(SimmerClient)
    client.live = True
    client.venue = venue
    client._private_key = None
    client._ows_wallet = None
    client._solana_private_key = None
    client._held_markets_cache = None
    client._approvals_warned = False
    client.ORDER_TYPES = {"FAK", "FOK", "GTC", "GTD"}
    client.VENUES = {"sim", "polymarket", "kalshi", "simmer"}
    return client


def test_trade_result_dataclass_has_shares_sold():
    """Defensive: the field must exist on the dataclass with default 0."""
    result = TradeResult(success=True)
    assert hasattr(result, "shares_sold")
    assert result.shares_sold == 0


def test_sim_sell_response_populates_shares_sold():
    """Repro the Herman ticket fixture: SDK trade(action='sell') on sim must
    return TradeResult.shares_sold > 0 when the server returns shares_sold."""
    client = _make_client(venue="sim")

    def fake_request(method, path, **kwargs):
        # Mirror the actual server response shape from local_dev_server.py
        # for a successful SIM sell (line ~23999 onwards).
        return {
            "success": True,
            "trade_id": "fa8ecd64-7996-4f88-a8de-0e734cfb3cc7",
            "market_id": "58ed726b-a2ec-4223-872c-f6093bc62f35",
            "side": "no",
            "shares_sold": 18.163220084084266,  # server emits positive
            "shares_requested": 18.14477,
            "cost": 10.020337752821433,  # proceeds for a sell
            "new_price": 0.448878689513781,
            "position": {"sim_balance": 9990.02},
            "fill_status": "filled",
        }

    client._request = fake_request
    result = client.trade(
        market_id="58ed726b-a2ec-4223-872c-f6093bc62f35",
        side="no",
        action="sell",
        shares=18.14477,
    )
    assert result.success
    assert result.shares_sold == pytest.approx(18.163220084084266)
    assert result.shares_bought == 0
    assert result.cost == pytest.approx(10.020337752821433)


def test_shares_filled_property_works_for_sells():
    """The shares_filled property returns the relevant filled count regardless
    of direction — agents can use it without branching on action."""
    sell = TradeResult(success=True, shares_sold=18.16)
    assert sell.shares_filled == 18.16

    buy = TradeResult(success=True, shares_bought=12.34)
    assert buy.shares_filled == 12.34


def test_fully_filled_property_handles_sells():
    """fully_filled was previously buy-only — verify sells now evaluate correctly."""
    partial_sell = TradeResult(
        success=True, shares_sold=5.0, shares_requested=10.0
    )
    assert partial_sell.fully_filled is False

    full_sell = TradeResult(
        success=True, shares_sold=10.0, shares_requested=10.0
    )
    assert full_sell.fully_filled is True


def test_sim_buy_response_still_populates_shares_bought():
    """Backward compat: SIM buys must keep working unchanged."""
    client = _make_client(venue="sim")

    def fake_request(method, path, **kwargs):
        return {
            "success": True,
            "trade_id": "buy_trade",
            "market_id": "m1",
            "side": "yes",
            "shares_bought": 20.0,
            "shares_requested": 20.0,
            "cost": 10.0,
            "new_price": 0.55,
            "position": {"sim_balance": 9980.0},
            "fill_status": "filled",
        }

    client._request = fake_request
    result = client.trade(market_id="m1", side="yes", action="buy", amount=10.0)
    assert result.success
    assert result.shares_bought == 20.0
    assert result.shares_sold == 0
