"""go_live passthrough: the server's sim-milestone nudge must survive parsing.

The server attaches a structured `go_live` block to successful sim-trade
responses when a sim-only agent crosses an activity milestone. `_build_result`
maps fields explicitly, so without a mapping the block is silently dropped —
these tests pin the mapping and the include_hints surfacing in next_steps.
"""

from __future__ import annotations

from simmer_sdk.client import SimmerClient, TradeResult

GO_LIVE_BLOCK = {
    "reason": "This agent has placed 10 simulated trades but real trading is not set up for this account.",
    "sim_trades": 10,
    "steps": [
        {
            "actor": "agent",
            "action": "enable_real_trading",
            "method": "PATCH /api/sdk/settings",
            "body": {"sdk_real_trading_enabled": True},
        },
        {
            "actor": "owner",
            "action": "fund_wallet",
            "url": "https://simmer.markets/dashboard",
        },
    ],
}


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


def _sim_response(**extra):
    resp = {
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
    resp.update(extra)
    return resp


def test_trade_result_defaults_go_live_none():
    assert TradeResult(success=True).go_live is None


def test_go_live_block_survives_parsing():
    client = _make_client()
    client._request = lambda method, path, **kw: _sim_response(go_live=GO_LIVE_BLOCK)
    result = client.trade(market_id="m1", side="yes", amount=10.0)
    assert result.success
    assert result.go_live == GO_LIVE_BLOCK


def test_absent_go_live_stays_none():
    client = _make_client()
    client._request = lambda method, path, **kw: _sim_response()
    result = client.trade(market_id="m1", side="yes", amount=10.0)
    assert result.go_live is None


def test_include_hints_surfaces_go_live_in_next_steps():
    client = _make_client()
    client._request = lambda method, path, **kw: _sim_response(go_live=GO_LIVE_BLOCK)
    result = client.trade(market_id="m1", side="yes", amount=10.0, include_hints=True)
    joined = " ".join(result.next_steps)
    assert "10 simulated trades" in joined
    assert "enable_real_trading" in joined
    assert "fund_wallet" in joined
