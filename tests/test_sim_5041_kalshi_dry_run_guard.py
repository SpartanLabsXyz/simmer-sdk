"""SIM-5041 regression: dry_run=True on venue="kalshi" must not place a real order.

Before this fix, client.trade(venue="kalshi", dry_run=True) ignored dry_run
entirely — _execute_kalshi_byow_trade had no dry_run parameter, so the call
fetched a quote, signed a Solana transaction with SOLANA_PRIVATE_KEY, and
submitted it to /api/sdk/trade/kalshi/submit. A call documented as placing
nothing placed a real order with real money.

Fix: dry_run is threaded into _execute_kalshi_byow_trade and short-circuits
before any quote/sign/submit call, returning a failure TradeResult that
names the limitation (Kalshi BYOW has no preview path — option (a)/(c) are
out of scope here).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from simmer_sdk.client import SimmerClient, TradeResult


def _make_client(*, solana_key_available: bool = True) -> SimmerClient:
    """Live-mode client with network calls and Solana signing stubbed out."""
    client = SimmerClient.__new__(SimmerClient)
    client.live = True
    client.venue = "kalshi"
    client._readonly = False
    client._private_key = None
    client._ows_wallet = None
    client._solana_wallet_address = None
    client._solana_wallet_registered = True
    client._solana_key_available = solana_key_available
    client._held_markets_cache = {}
    client._held_markets_ts = 0
    client._approvals_warned = False
    client.ORDER_TYPES = {"FAK", "FOK", "GTC", "GTD"}
    client.VENUES = {"sim", "polymarket", "kalshi", "simmer"}
    return client


def test_kalshi_dry_run_never_calls_transport():
    """dry_run=True must not reach the quote or submit endpoints."""
    client = _make_client()
    with patch.object(client, "_request", side_effect=AssertionError("transport called")) as mock_request:
        result = client.trade(
            "MKT-1", "yes", 10.0, action="buy", venue="kalshi",
            allow_rebuy=True, dry_run=True,
        )
    mock_request.assert_not_called()
    assert isinstance(result, TradeResult)
    assert result.success is False
    assert "dry_run" in result.error.lower()
    assert "kalshi" in result.error.lower()


def test_kalshi_dry_run_never_calls_solana_signer():
    """dry_run=True must not invoke the Solana signing entry point."""
    client = _make_client()
    with patch("simmer_sdk.solana_signing.sign_solana_transaction") as mock_sign:
        with patch.object(client, "_request", side_effect=AssertionError("transport called")):
            client.trade(
                "MKT-1", "yes", 10.0, action="buy", venue="kalshi",
                allow_rebuy=True, dry_run=True,
            )
    mock_sign.assert_not_called()


def test_kalshi_dry_run_leaves_held_markets_cache_untouched():
    """dry_run=True must not mutate the held-markets cache."""
    client = _make_client()
    seed = {"MKT-EXISTING": ["sdk:some-strategy"]}
    client._held_markets_cache = dict(seed)
    with patch.object(client, "_request", side_effect=AssertionError("transport called")):
        client.trade(
            "MKT-1", "yes", 10.0, action="buy", venue="kalshi",
            allow_rebuy=True, dry_run=True,
        )
    assert client._held_markets_cache == seed


def test_kalshi_dry_run_returns_explicit_failure_result():
    """The returned TradeResult names both 'no order placed' and 'dry-run unsupported'."""
    client = _make_client()
    with patch.object(client, "_request", side_effect=AssertionError("transport called")):
        result = client.trade(
            "MKT-1", "yes", 10.0, action="buy", venue="kalshi",
            allow_rebuy=True, dry_run=True,
        )
    assert result.success is False
    assert result.venue == "kalshi"
    assert "no order" in result.error.lower() or "not placed" in result.error.lower()
    assert "dry" in result.error.lower()


def test_kalshi_dry_run_false_still_places_real_order():
    """Regression guard: the short-circuit must not swallow real (dry_run=False) trades."""
    client = _make_client()

    def _request_side_effect(method, path, json=None, **kwargs):
        if path == "/api/sdk/trade/kalshi/quote":
            return {"success": True, "transaction": "unsigned-tx-b64", "quote_id": "q-1"}
        if path == "/api/sdk/trade/kalshi/submit":
            return {
                "success": True,
                "trade_id": "t-1",
                "market_id": "MKT-1",
                "side": "yes",
                "shares_bought": 20.0,
                "shares_requested": 20.0,
                "cost": 10.0,
                "new_price": 0.5,
            }
        raise AssertionError(f"unexpected request: {method} {path}")

    with patch("simmer_sdk.solana_signing.sign_solana_transaction", return_value="signed-tx-b64") as mock_sign:
        with patch.object(client, "_request", side_effect=_request_side_effect) as mock_request:
            result = client.trade(
                "MKT-1", "yes", 10.0, action="buy", venue="kalshi",
                allow_rebuy=True, dry_run=False,
            )

    mock_sign.assert_called_once_with("unsigned-tx-b64")
    called_paths = [c.args[1] for c in mock_request.call_args_list]
    assert "/api/sdk/trade/kalshi/quote" in called_paths
    assert "/api/sdk/trade/kalshi/submit" in called_paths
    assert result.success is True
    assert result.trade_id == "t-1"


def test_polymarket_dry_run_unchanged():
    """venue='polymarket' dry-run still goes through the generic /api/sdk/trade path."""
    client = _make_client()
    client.venue = "polymarket"

    def _request_side_effect(method, path, json=None, **kwargs):
        assert path == "/api/sdk/trade"
        assert json.get("dry_run") is True
        return {"success": True, "market_id": "MKT-1", "side": "yes", "shares_bought": 20.0}

    with patch.object(client, "_request", side_effect=_request_side_effect) as mock_request:
        result = client.trade(
            "MKT-1", "yes", 10.0, action="buy", venue="polymarket",
            allow_rebuy=True, dry_run=True,
        )
    mock_request.assert_called_once()
    assert result.success is True


def test_sim_dry_run_unchanged():
    """venue='sim' dry-run still goes through the generic /api/sdk/trade path."""
    client = _make_client()
    client.venue = "sim"

    def _request_side_effect(method, path, json=None, **kwargs):
        assert path == "/api/sdk/trade"
        assert json.get("dry_run") is True
        return {"success": True, "market_id": "MKT-1", "side": "yes", "shares_bought": 20.0}

    with patch.object(client, "_request", side_effect=_request_side_effect) as mock_request:
        result = client.trade(
            "MKT-1", "yes", 10.0, action="buy", venue="sim",
            allow_rebuy=True, dry_run=True,
        )
    mock_request.assert_called_once()
    assert result.success is True


def test_kalshi_dry_run_result_is_not_retryable():
    """A dry_run refusal can never succeed on retry. Six bundled skills branch on
    TradeResult.retryable to stop a retry loop and the dataclass default is True,
    so a client-side refusal that leaves it True tells them to try again."""
    client = _make_client()
    with patch.object(client, "_request", side_effect=AssertionError("transport called")):
        result = client.trade(
            "MKT-1", "yes", 10.0, action="buy", venue="kalshi",
            allow_rebuy=True, dry_run=True,
        )
    assert result.success is False
    assert result.error_code == "dry_run_unsupported"
    assert result.retryable is False
