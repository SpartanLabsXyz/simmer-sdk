"""Tests for SimmerClient.ensure_can_trade balance pre-flight helper (SIM-1063).

Contract:
- Non-polymarket venues short-circuit to ok=True (paper/kalshi have their own checks).
- Underfunded polymarket wallet returns ok=False with reason="insufficient_balance".
- Funded wallet returns ok=True and max_safe_size = balance * (1 - safety_buffer).
- Portfolio RPC failure returns ok=False with reason="balance_unavailable".
"""

from unittest.mock import patch

import pytest

from simmer_sdk.client import SimmerClient


def _make_client(venue: str = "polymarket") -> SimmerClient:
    # API key is required at construction; helper never makes a network call
    # because we mock get_portfolio below.
    return SimmerClient(api_key="test-key", venue=venue)


def test_skipped_non_polymarket_venue():
    client = _make_client(venue="sim")
    result = client.ensure_can_trade(min_usd=5.0)
    assert result["ok"] is True
    assert result["reason"] == "skipped_non_polymarket"
    assert result["collateral"] == ""


def test_insufficient_balance_returns_skip():
    client = _make_client()
    fake_portfolio = {"polymarket": {"balance": 0.50}, "balance_usdc": 0.50}
    with patch.object(client, "get_portfolio", return_value=fake_portfolio):
        result = client.ensure_can_trade(min_usd=1.0)
    assert result["ok"] is False
    assert result["reason"] == "insufficient_balance"
    assert result["balance"] == pytest.approx(0.50)
    assert result["max_safe_size"] == 0.0


def test_sufficient_balance_returns_max_safe_size():
    client = _make_client()
    fake_portfolio = {"polymarket": {"balance": 100.0}, "balance_usdc": 100.0}
    with patch.object(client, "get_portfolio", return_value=fake_portfolio):
        result = client.ensure_can_trade(min_usd=1.0, safety_buffer=0.02)
    assert result["ok"] is True
    assert result["reason"] == "ok"
    assert result["balance"] == pytest.approx(100.0)
    assert result["max_safe_size"] == pytest.approx(98.0)


def test_custom_safety_buffer():
    client = _make_client()
    fake_portfolio = {"polymarket": {"balance": 50.0}, "balance_usdc": 50.0}
    with patch.object(client, "get_portfolio", return_value=fake_portfolio):
        result = client.ensure_can_trade(min_usd=1.0, safety_buffer=0.10)
    assert result["ok"] is True
    assert result["max_safe_size"] == pytest.approx(45.0)


def test_portfolio_rpc_failure_returns_balance_unavailable():
    client = _make_client()
    with patch.object(client, "get_portfolio", side_effect=RuntimeError("RPC down")):
        result = client.ensure_can_trade(min_usd=1.0)
    assert result["ok"] is False
    assert result["reason"] == "balance_unavailable"
    assert result["balance"] == 0.0


def test_null_balance_returns_balance_unavailable():
    """Server returns null balance when RPC fetch fails — treat as unavailable, not empty."""
    client = _make_client()
    fake_portfolio = {"polymarket": {"balance": None}, "balance_usdc": None}
    with patch.object(client, "get_portfolio", return_value=fake_portfolio):
        result = client.ensure_can_trade(min_usd=1.0)
    assert result["ok"] is False
    assert result["reason"] == "balance_unavailable"


def test_venue_override_argument():
    """`venue` kwarg overrides the client's default venue."""
    client = _make_client(venue="polymarket")
    result = client.ensure_can_trade(min_usd=1.0, venue="sim")
    assert result["ok"] is True
    assert result["reason"] == "skipped_non_polymarket"
