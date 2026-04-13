"""Tests for pre-submission decimal rounding in SimmerClient.trade()."""

import logging
import unittest
from unittest.mock import MagicMock, patch

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


class TestAmountDecimalRounding:
    """amount (maker USDC) must be rounded to 2 d.p. before submission."""

    def test_exact_two_decimals_unchanged(self):
        client = _make_client()
        captured = {}

        def fake_request(method, path, **kwargs):
            captured.update(kwargs.get("json", {}))
            return {
                "success": True, "trade_id": "t1", "market_id": "m1",
                "side": "yes", "shares_bought": 10, "shares_requested": 10,
                "order_status": "MATCHED", "cost": 10.00, "new_price": 0.5,
                "position": {},
            }

        client._request = fake_request
        client.trade("m1", "yes", amount=10.00)
        assert captured["amount"] == 10.00

    def test_three_decimals_rounded(self, caplog):
        client = _make_client()
        captured = {}

        def fake_request(method, path, **kwargs):
            captured.update(kwargs.get("json", {}))
            return {
                "success": True, "trade_id": "t1", "market_id": "m1",
                "side": "yes", "shares_bought": 10, "shares_requested": 10,
                "order_status": "MATCHED", "cost": 10.0, "new_price": 0.5,
                "position": {},
            }

        client._request = fake_request
        with caplog.at_level(logging.WARNING, logger="simmer_sdk.client"):
            client.trade("m1", "yes", amount=10.123)

        assert captured["amount"] == pytest.approx(10.12)
        assert any("rounded" in r.message for r in caplog.records)

    def test_many_decimals_rounded(self, caplog):
        client = _make_client()
        captured = {}

        def fake_request(method, path, **kwargs):
            captured.update(kwargs.get("json", {}))
            return {
                "success": True, "trade_id": "t1", "market_id": "m1",
                "side": "yes", "shares_bought": 5, "shares_requested": 5,
                "order_status": "MATCHED", "cost": 5.0, "new_price": 0.5,
                "position": {},
            }

        client._request = fake_request
        with caplog.at_level(logging.WARNING, logger="simmer_sdk.client"):
            client.trade("m1", "yes", amount=5.333333333)

        assert captured["amount"] == pytest.approx(5.33)

    def test_no_warning_when_exact(self, caplog):
        client = _make_client()

        def fake_request(method, path, **kwargs):
            return {
                "success": True, "trade_id": "t1", "market_id": "m1",
                "side": "yes", "shares_bought": 10, "shares_requested": 10,
                "order_status": "MATCHED", "cost": 10.0, "new_price": 0.5,
                "position": {},
            }

        client._request = fake_request
        with caplog.at_level(logging.WARNING, logger="simmer_sdk.client"):
            client.trade("m1", "yes", amount=10.50)

        assert not any("rounded" in r.message for r in caplog.records)


class TestSharesDecimalRounding:
    """shares (taker) must be rounded to 5 d.p. before submission."""

    def test_exact_five_decimals_unchanged(self):
        client = _make_client()
        captured = {}

        def fake_request(method, path, **kwargs):
            captured.update(kwargs.get("json", {}))
            return {
                "success": True, "trade_id": "t1", "market_id": "m1",
                "side": "yes", "shares_bought": 0, "shares_requested": 0,
                "order_status": "MATCHED", "cost": 0, "new_price": 0.5,
                "position": {},
            }

        client._request = fake_request
        client.trade("m1", "yes", shares=1.23456, action="sell")
        assert captured["shares"] == pytest.approx(1.23456)

    def test_six_decimals_rounded(self, caplog):
        client = _make_client()
        captured = {}

        def fake_request(method, path, **kwargs):
            captured.update(kwargs.get("json", {}))
            return {
                "success": True, "trade_id": "t1", "market_id": "m1",
                "side": "yes", "shares_bought": 0, "shares_requested": 0,
                "order_status": "MATCHED", "cost": 0, "new_price": 0.5,
                "position": {},
            }

        client._request = fake_request
        with caplog.at_level(logging.WARNING, logger="simmer_sdk.client"):
            client.trade("m1", "yes", shares=1.234567, action="sell")

        assert captured["shares"] == pytest.approx(1.23457)
        assert any("rounded" in r.message for r in caplog.records)

    def test_no_warning_when_exact(self, caplog):
        client = _make_client()

        def fake_request(method, path, **kwargs):
            return {
                "success": True, "trade_id": "t1", "market_id": "m1",
                "side": "yes", "shares_bought": 0, "shares_requested": 0,
                "order_status": "MATCHED", "cost": 0, "new_price": 0.5,
                "position": {},
            }

        client._request = fake_request
        with caplog.at_level(logging.WARNING, logger="simmer_sdk.client"):
            client.trade("m1", "yes", shares=5.0, action="sell")

        assert not any("rounded" in r.message for r in caplog.records)
