"""SIM-5853: warnings, client_order_id and exchange_order_id_pending survive parsing.

`_build_result` maps fields explicitly. When the relay returns an unconfirmed
fill (no fill data yet), the server attaches `warnings` explaining that plus
`client_order_id` / `exchange_order_id_pending` so the caller can track the
order — but the field allow-list dropped all three, so an agent saw
`success=True, shares_bought=0, cost=0` with no explanation.
"""

from __future__ import annotations

from simmer_sdk.client import SimmerClient, TradeResult


def _make_client(venue: str = "sim") -> SimmerClient:
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


def _unconfirmed_response(**extra):
    resp = {
        "success": True,
        "trade_id": "t1",
        "market_id": "m1",
        "side": "yes",
        "shares_bought": 0,
        "shares_requested": 20.0,
        "cost": 0,
        "new_price": 0.55,
        "position": {"sim_balance": 9990.0},
        "fill_status": "unconfirmed",
        "client_order_id": "co-123",
        "exchange_order_id_pending": True,
        "warnings": ["Fill data not yet available — order may still be pending on-chain."],
    }
    resp.update(extra)
    return resp


def test_trade_result_defaults_new_fields_absent():
    result = TradeResult(success=True)
    assert result.client_order_id is None
    assert result.exchange_order_id_pending is False
    assert result.warnings is None


def test_unconfirmed_fill_fields_survive_parsing():
    client = _make_client()
    client._request = lambda method, path, **kw: _unconfirmed_response()
    result = client.trade(market_id="m1", side="yes", amount=10.0)
    assert result.success
    assert result.fill_status == "unconfirmed"
    assert result.client_order_id == "co-123"
    assert result.exchange_order_id_pending is True
    assert result.warnings == [
        "Fill data not yet available — order may still be pending on-chain."
    ]


def test_absent_new_fields_stay_default():
    client = _make_client()
    client._request = lambda method, path, **kw: {
        "success": True,
        "trade_id": "t1",
        "market_id": "m1",
        "side": "yes",
        "shares_bought": 20.0,
        "shares_requested": 20.0,
        "cost": 10.0,
        "new_price": 0.55,
        "position": {"sim_balance": 9980.0},
        "fill_status": "filled",
    }
    result = client.trade(market_id="m1", side="yes", amount=10.0)
    assert result.client_order_id is None
    assert result.exchange_order_id_pending is False
    assert result.warnings is None
