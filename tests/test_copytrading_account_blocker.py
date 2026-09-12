"""Tests for SIM-5274: stop burning trade attempts once the server reports
an account-wide structural blocker (missing approvals, wrong collateral,
pUSD migration pending) via `trade_result.retryable=False`.

Before this fix, polling-mode copytrading re-attempted every mirrored whale
signal in a run even after the first one revealed the wallet is
structurally blocked — one actor generated 20-330 failed attempts/day against
the same permanent condition (SIM-5274 investigation).
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


SKILL_PATH = (
    Path(__file__).resolve().parent.parent
    / "skills"
    / "polymarket-copytrading"
    / "copytrading_trader.py"
)


def _load_trader_module():
    spec = importlib.util.spec_from_file_location("copytrading_trader_under_test_blocker", SKILL_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ACCOUNT_BLOCKER_ERROR = (
    "Funding-state error suppressed by circuit breaker: your wallet has a "
    "structural funding issue (missing approvals, wrong collateral type, or "
    "pUSD migration pending). Activate trading at https://simmer.markets/dashboard"
)


def _fake_trade_result(success, error=None, retryable=True):
    return SimpleNamespace(success=success, error=error, retryable=retryable, trade_id=None)


@pytest.mark.parametrize(
    "error,expected",
    [
        (ACCOUNT_BLOCKER_ERROR, True),
        ("Polymarket V2 trading approvals required. Activate V2 Trading: https://simmer.markets/dashboard", True),
        ("insufficient balance for this order", False),
        (None, False),
        ("", False),
    ],
)
def test_is_account_blocker_error(error, expected):
    mod = _load_trader_module()
    assert mod._is_account_blocker_error(error) is expected


def test_execute_copytrading_stops_after_account_blocker():
    mod = _load_trader_module()

    trades_plan = [
        {"market_id": "m1", "action": "buy", "side": "yes", "shares": 0, "estimated_cost": 10},
        {"market_id": "m2", "action": "buy", "side": "yes", "shares": 0, "estimated_cost": 10},
        {"market_id": "m3", "action": "buy", "side": "yes", "shares": 0, "estimated_cost": 10},
    ]

    fake_client = MagicMock()
    fake_client._request.return_value = {"trades": trades_plan}
    fake_client.trade.side_effect = [
        _fake_trade_result(False, error=ACCOUNT_BLOCKER_ERROR, retryable=False),
        _fake_trade_result(True),
        _fake_trade_result(True),
    ]

    with patch.object(mod, "get_client", return_value=fake_client):
        result = mod.execute_copytrading(
            wallets=["0xabc"], dry_run=False, max_usd=50.0, venue="polymarket",
        )

    # Only the first (blocked) signal should have reached client.trade() —
    # the loop must stop there instead of attempting m2 and m3.
    assert fake_client.trade.call_count == 1
    assert result["trades_executed"] == 0
    assert trades_plan[0]["success"] is False
    assert "success" not in trades_plan[1]
    assert "success" not in trades_plan[2]


def test_execute_copytrading_continues_past_market_specific_failure():
    mod = _load_trader_module()

    trades_plan = [
        {"market_id": "m1", "action": "sell", "side": "yes", "shares": 5, "estimated_cost": 0},
        {"market_id": "m2", "action": "buy", "side": "yes", "shares": 0, "estimated_cost": 10},
    ]

    fake_client = MagicMock()
    fake_client._request.return_value = {"trades": trades_plan}
    fake_client.trade.side_effect = [
        _fake_trade_result(False, error="position already cleared on-chain", retryable=False),
        _fake_trade_result(True),
    ]

    with patch.object(mod, "get_client", return_value=fake_client):
        result = mod.execute_copytrading(
            wallets=["0xabc"], dry_run=False, max_usd=50.0, venue="polymarket",
        )

    # A market-specific non-retryable failure (position cleared) must NOT
    # abort the whole run — the next signal is unrelated and should proceed.
    assert fake_client.trade.call_count == 2
    assert result["trades_executed"] == 1
