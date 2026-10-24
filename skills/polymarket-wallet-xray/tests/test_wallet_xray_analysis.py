"""Offline contracts for the wallet xray analysis helpers."""

import importlib
from pathlib import Path
import sys
from unittest.mock import patch

import pytest


@pytest.fixture
def xray(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    # The CLI configures stdout at import; pytest's capture stream may not support it.
    with patch.object(sys, "stdout"):
        module = importlib.import_module("wallet_xray")

    def no_network(*args, **kwargs):
        pytest.fail("Analysis helpers must not fetch wallet data or call the API")

    monkeypatch.setattr(module, "get_wallet_trades", no_network)
    monkeypatch.setattr(module, "api_request", no_network)
    return module


@pytest.mark.parametrize(
    ("helper", "expected"),
    [
        ("compute_profitability", {
            "time_profitable_pct": 0,
            "win_rate_pct": 0,
            "avg_profit_per_win": 0,
            "avg_loss_per_loss": 0,
            "realized_pnl_usd": 0,
            "total_trades": 0,
        }),
        ("compute_entry_quality", {
            "avg_price_spread_bps": 0,
            "quality_rating": "N/A",
            "assessment": "No trades to analyze",
        }),
        ("detect_bot_behavior", {
            "is_bot_detected": False,
            "trading_intensity": "very_low",
            "avg_seconds_between_trades": 0,
            "price_chasing": "unknown",
            "accumulation_signal": "unknown",
        }),
        ("detect_arbitrage_edge", {
            "hedge_check_combined_avg": 1.0,
            "has_arbitrage_edge": False,
            "assessment": "No trades to analyze",
        }),
        ("compute_risk_profile", {
            "max_drawdown_pct": 0,
            "volatility": "unknown",
            "max_position_concentration": 0,
        }),
    ],
)
def test_empty_trades(xray, helper, expected):
    assert getattr(xray, helper)([]) == expected


@pytest.mark.parametrize(
    ("sale_price", "pnl", "win_pct", "avg_win", "avg_loss"),
    [(0.75, 2.5, 100.0, 2.5, 0), (0.25, -2.5, 0, 0, -2.5)],
)
def test_profitability_round_trip(xray, sale_price, pnl, win_pct, avg_win, avg_loss):
    # Ten shares bought for 5, then sold for 7.5 or 2.5. Reverse input order
    # to check that realized results follow timestamps rather than list order.
    trades = [
        {"created_at": "2026-01-02T00:00:00Z", "market_id": "market-1",
         "side": "yes", "action": "sell_yes", "shares": 10,
         "price": sale_price, "cost_usdc": 10 * sale_price},
        {"created_at": "2026-01-01T00:00:00Z", "market_id": "market-1",
         "side": "yes", "action": "buy_yes", "shares": 10,
         "price": 0.5, "cost_usdc": 5},
    ]
    assert xray.compute_profitability(trades) == {
        "time_profitable_pct": win_pct,
        "win_rate_pct": win_pct,
        "avg_profit_per_win": avg_win,
        "avg_loss_per_loss": avg_loss,
        "realized_pnl_usd": pnl,
        "total_trades": 2,
    }


def test_recommendation_from_empty_trades(xray):
    metrics = {
        "profitability": xray.compute_profitability([]),
        "entry_quality": xray.compute_entry_quality([]),
        "behavior": xray.detect_bot_behavior([]),
        "edge_detection": xray.detect_arbitrage_edge([]),
        "risk_profile": xray.compute_risk_profile([]),
    }
    assert xray.generate_recommendation(metrics) == (
        "Risky trader. ❌ Low Time Profitable (<40%); "
        "⚠️ Insufficient data for entry quality "
        "Avoid copytrading or limit to <5% of capital."
    )


def test_recommendation_excellent_trader(xray):
    metrics = {
        "profitability": {"time_profitable_pct": 90, "total_trades": 101},
        "entry_quality": {"quality_rating": "A"},
        "behavior": {"is_bot_detected": False},
        "edge_detection": {"has_arbitrage_edge": True},
        "risk_profile": {"max_drawdown_pct": 10},
    }
    assert xray.generate_recommendation(metrics) == (
        "Excellent trader. ✅ Strong Time Profitable (>80%); "
        "✅ Excellent entry quality; ✅ Human trader (not bot) "
        "Safe to copytrade with 25-50% of capital."
    )
