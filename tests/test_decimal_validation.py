"""Tests for pre-submission decimal validation in SimmerClient.trade()."""

import pytest

from simmer_sdk.client import SimmerClient


def _make_client():
    """Return a live-mode client with network calls stubbed out."""
    client = SimmerClient.__new__(SimmerClient)
    client.live = True
    client.venue = "polymarket"
    client._private_key = None
    client._solana_private_key = None
    client._held_markets_cache = None
    client._approvals_warned = False
    client.ORDER_TYPES = {"FAK", "FOK", "GTC", "GTD"}
    client.VENUES = {"sim", "polymarket", "kalshi", "simmer"}
    return client


def _fake_request(method, path, **kwargs):
    return {
        "success": True, "trade_id": "t1", "market_id": "m1",
        "side": "yes", "shares_bought": 10, "shares_requested": 10,
        "order_status": "MATCHED", "cost": 10.00, "new_price": 0.5,
        "position": {},
    }


class TestAmountDecimalValidation:
    """amount (maker USDC) must have max 2 decimal places."""

    def test_exact_two_decimals_accepted(self):
        client = _make_client()
        client._request = _fake_request
        result = client.trade("m1", "yes", amount=10.12)
        assert result.success

    def test_integer_amount_accepted(self):
        client = _make_client()
        client._request = _fake_request
        result = client.trade("m1", "yes", amount=10.0)
        assert result.success

    def test_three_decimals_rejected(self):
        client = _make_client()
        client._request = _fake_request
        with pytest.raises(ValueError, match="too many decimal places.*max 2"):
            client.trade("m1", "yes", amount=10.123)

    def test_many_decimals_rejected(self):
        client = _make_client()
        client._request = _fake_request
        with pytest.raises(ValueError, match="too many decimal places.*max 2"):
            client.trade("m1", "yes", amount=5.333333333)

    def test_error_suggests_rounded_value(self):
        client = _make_client()
        client._request = _fake_request
        with pytest.raises(ValueError, match="Use 10.12 instead"):
            client.trade("m1", "yes", amount=10.123)


class TestSharesDecimalValidation:
    """shares (taker) must have max 5 decimal places."""

    def test_exact_five_decimals_accepted(self):
        client = _make_client()
        client._request = _fake_request
        result = client.trade("m1", "yes", shares=1.23456, action="sell")
        assert result.success

    def test_six_decimals_rejected(self):
        client = _make_client()
        client._request = _fake_request
        with pytest.raises(ValueError, match="too many decimal places.*max 5"):
            client.trade("m1", "yes", shares=1.234567, action="sell")

    def test_integer_shares_accepted(self):
        client = _make_client()
        client._request = _fake_request
        result = client.trade("m1", "yes", shares=5.0, action="sell")
        assert result.success
