"""Tests for pre-submission decimal quantization in SimmerClient.trade().

The client rounds amount (USDC maker, max 2 decimals) and shares (taker, max 5
decimals) to the venue's input precision instead of rejecting full-precision
floats, so skills can pass raw planner/Kelly outputs without re-implementing the
round() workaround (SIM-3272). Tick-aware rounding of the on-chain order amounts
stays in signing.py — this layer only quantizes the human-facing inputs.
"""

import pytest

from simmer_sdk.client import SimmerClient


def _make_client():
    """Return a live-mode client with network calls stubbed out.

    The stubbed ``_request`` records the last payload so tests can assert what
    amount/shares actually reached the wire after quantization.
    """
    client = SimmerClient.__new__(SimmerClient)
    client.live = True
    client.venue = "polymarket"
    client._private_key = None
    client._ows_wallet = None
    client._solana_private_key = None
    client._held_markets_cache = None
    client._approvals_warned = False
    client.ORDER_TYPES = {"FAK", "FOK", "GTC", "GTD"}
    client.VENUES = {"sim", "polymarket", "kalshi", "simmer"}

    client.sent_payloads = []

    def _fake_request(method, path, **kwargs):
        if kwargs.get("json") is not None:
            client.sent_payloads.append(kwargs["json"])
        return {
            "success": True, "trade_id": "t1", "market_id": "m1",
            "side": "yes", "shares_bought": 10, "shares_requested": 10,
            "order_status": "MATCHED", "cost": 10.00, "new_price": 0.5,
            "position": {},
        }

    client._request = _fake_request
    return client


class TestAmountDecimalQuantization:
    """amount (maker USDC) is rounded to max 2 decimal places."""

    def test_exact_two_decimals_accepted(self):
        client = _make_client()
        result = client.trade("m1", "yes", amount=10.12)
        assert result.success
        assert client.sent_payloads[-1]["amount"] == 10.12

    def test_integer_amount_accepted(self):
        client = _make_client()
        result = client.trade("m1", "yes", amount=10.0)
        assert result.success
        assert client.sent_payloads[-1]["amount"] == 10.0

    def test_three_decimals_rounded(self):
        client = _make_client()
        result = client.trade("m1", "yes", amount=10.123)
        assert result.success
        assert client.sent_payloads[-1]["amount"] == 10.12

    def test_many_decimals_rounded(self):
        """The exact value from the SIM-3265 live run."""
        client = _make_client()
        result = client.trade("m1", "yes", amount=16.489550245148255)
        assert result.success
        assert client.sent_payloads[-1]["amount"] == 16.49

    def test_amount_rounding_to_zero_rejected(self):
        """A sub-cent amount that quantizes to 0 must not place a $0 order."""
        client = _make_client()
        with pytest.raises(ValueError, match="too small to place an order"):
            client.trade("m1", "yes", amount=0.004)


class TestSharesDecimalQuantization:
    """shares (taker) are rounded to max 5 decimal places."""

    def test_exact_five_decimals_accepted(self):
        client = _make_client()
        result = client.trade("m1", "yes", shares=1.23456, action="sell")
        assert result.success
        assert client.sent_payloads[-1]["shares"] == 1.23456

    def test_six_decimals_rounded(self):
        client = _make_client()
        result = client.trade("m1", "yes", shares=1.234567, action="sell")
        assert result.success
        assert client.sent_payloads[-1]["shares"] == 1.23457

    def test_integer_shares_accepted(self):
        client = _make_client()
        result = client.trade("m1", "yes", shares=5.0, action="sell")
        assert result.success
        assert client.sent_payloads[-1]["shares"] == 5.0

    def test_shares_rounding_to_zero_rejected(self):
        client = _make_client()
        with pytest.raises(ValueError, match="too small to place an order"):
            client.trade("m1", "yes", shares=0.000004, action="sell")


class TestSharesBuyGuard:
    """shares must not be passed on buy orders — fail loud instead of silently ignoring."""

    def test_shares_on_buy_raises(self):
        client = _make_client()
        with pytest.raises(ValueError, match="shares is for sell orders only"):
            client.trade("m1", "yes", amount=10.0, shares=5.0, action="buy")

    def test_shares_on_default_buy_raises(self):
        """action defaults to 'buy', so passing shares without action should also raise."""
        client = _make_client()
        with pytest.raises(ValueError, match="shares is for sell orders only"):
            client.trade("m1", "yes", amount=10.0, shares=5.0)

    def test_shares_zero_on_buy_allowed(self):
        """shares=0 (default) on a buy is fine — no guard triggered."""
        client = _make_client()
        result = client.trade("m1", "yes", amount=10.0, shares=0)
        assert result.success

    def test_shares_on_sell_still_works(self):
        """The sell path is unaffected by the buy guard."""
        client = _make_client()
        result = client.trade("m1", "yes", shares=5.0, action="sell")
        assert result.success
