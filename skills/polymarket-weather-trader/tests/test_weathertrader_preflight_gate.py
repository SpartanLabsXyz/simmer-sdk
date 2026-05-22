"""
Smoke tests for the preflight gate in polymarket-weather-trader.

Verifies execute_trade() and execute_sell():
  - returns {"error": "preflight_blocked: ..."} and does NOT call client.trade()
    when preflight returns ok_to_trade=False
  - calls client.trade() normally when preflight returns ok_to_trade=True
  - skips preflight entirely in paper mode (client.live=False)
  - execute_sell() calls preflight with planned_amount=0, exposure_cap_usd=0
    so exit trades are never blocked by the exposure cap

All tests are pure-unit: no network calls, no SIMMER_API_KEY required.
"""
import sys
import os
import types
import unittest
from unittest.mock import MagicMock, patch

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

_mock_cfg = {
    "entry_threshold": 0.15, "exit_threshold": 0.45, "max_position_usd": 2.00,
    "sizing_pct": 0.05, "max_trades_per_run": 5, "locations": "NYC",
    "binary_only": False, "slippage_max": 0.15, "min_liquidity": 0.0,
    "order_type": "GTC", "vol_targeting": False, "target_vol": 0.20,
    "vol_max_leverage": 2.0, "vol_min_allocation": 0.2, "vol_span": 10,
}

_skill_stub = types.ModuleType("simmer_sdk.skill")
_skill_stub.load_config = lambda schema, file, slug=None: _mock_cfg.copy()
_skill_stub.update_config = lambda updates, file, slug=None: None
_skill_stub.get_config_path = lambda file: "/tmp/config.json"

sys.modules.setdefault("simmer_sdk", MagicMock())
sys.modules["simmer_sdk.skill"] = _skill_stub

import weather_trader as wt  # noqa: E402


def _make_preflight(ok=False, blockers=None):
    pf = MagicMock()
    pf.ok_to_trade = ok
    pf.blockers = blockers or ["WALLET_UNVERIFIED"]
    return pf


def _make_trade_result(success=True, simulated=False, order_status="filled"):
    r = MagicMock()
    r.success = success
    r.trade_id = "t_test"
    r.shares_bought = 10.0
    r.error = None
    r.simulated = simulated
    r.order_status = order_status
    return r


class TestPreflightGateWeatherBuy(unittest.TestCase):

    def test_blocked_returns_error_dict_and_no_trade(self):
        mock_client = MagicMock()
        mock_client.live = True
        mock_client.venue = "polymarket"
        mock_client.preflight.return_value = _make_preflight(ok=False, blockers=["WALLET_UNVERIFIED"])

        with patch.object(wt, "get_client", return_value=mock_client):
            result = wt.execute_trade("mkt_abc", "yes", 2.0)

        self.assertEqual(result, {"error": "preflight_blocked: WALLET_UNVERIFIED"})
        mock_client.trade.assert_not_called()

    def test_preflight_ok_calls_trade(self):
        mock_client = MagicMock()
        mock_client.live = True
        mock_client.venue = "polymarket"
        mock_client.preflight.return_value = _make_preflight(ok=True)
        mock_client.trade.return_value = _make_trade_result(success=True)

        with patch.object(wt, "get_client", return_value=mock_client):
            result = wt.execute_trade("mkt_abc", "yes", 2.0)

        mock_client.trade.assert_called_once()
        self.assertTrue(result["success"])

    def test_buy_preflight_called_with_cap_zero(self):
        mock_client = MagicMock()
        mock_client.live = True
        mock_client.venue = "polymarket"
        mock_client.preflight.return_value = _make_preflight(ok=True)
        mock_client.trade.return_value = _make_trade_result()

        with patch.object(wt, "get_client", return_value=mock_client):
            wt.execute_trade("mkt_abc", "yes", 3.0)

        mock_client.preflight.assert_called_once_with(
            planned_amount=3.0, exposure_cap_usd=0, venue="polymarket"
        )

    def test_paper_mode_skips_preflight(self):
        mock_client = MagicMock()
        mock_client.live = False
        mock_client.venue = "polymarket"
        mock_client.trade.return_value = _make_trade_result(simulated=True)

        with patch.object(wt, "get_client", return_value=mock_client):
            result = wt.execute_trade("mkt_abc", "yes", 2.0)

        mock_client.preflight.assert_not_called()
        self.assertTrue(result["success"])


class TestPreflightGateWeatherSell(unittest.TestCase):

    def test_sell_blocked_returns_error_and_no_trade(self):
        mock_client = MagicMock()
        mock_client.live = True
        mock_client.venue = "polymarket"
        mock_client.preflight.return_value = _make_preflight(ok=False, blockers=["WALLET_UNVERIFIED"])

        with patch.object(wt, "get_client", return_value=mock_client):
            result = wt.execute_sell("mkt_abc", 10.0)

        self.assertEqual(result, {"error": "preflight_blocked: WALLET_UNVERIFIED"})
        mock_client.trade.assert_not_called()

    def test_sell_preflight_uses_zero_amount_and_zero_cap(self):
        """Sells must never be blocked by the exposure cap — always use planned_amount=0, exposure_cap_usd=0."""
        mock_client = MagicMock()
        mock_client.live = True
        mock_client.venue = "polymarket"
        mock_client.preflight.return_value = _make_preflight(ok=True)
        mock_client.trade.return_value = _make_trade_result()

        with patch.object(wt, "get_client", return_value=mock_client):
            wt.execute_sell("mkt_abc", 10.0)

        mock_client.preflight.assert_called_once_with(
            planned_amount=0, exposure_cap_usd=0, venue="polymarket"
        )

    def test_sell_ok_calls_trade(self):
        mock_client = MagicMock()
        mock_client.live = True
        mock_client.venue = "polymarket"
        mock_client.preflight.return_value = _make_preflight(ok=True)
        mock_client.trade.return_value = _make_trade_result(success=True)

        with patch.object(wt, "get_client", return_value=mock_client):
            result = wt.execute_sell("mkt_abc", 10.0)

        mock_client.trade.assert_called_once()
        self.assertTrue(result["success"])

    def test_sell_paper_mode_skips_preflight(self):
        mock_client = MagicMock()
        mock_client.live = False
        mock_client.venue = "polymarket"
        mock_client.trade.return_value = _make_trade_result(simulated=True)

        with patch.object(wt, "get_client", return_value=mock_client):
            result = wt.execute_sell("mkt_abc", 10.0)

        mock_client.preflight.assert_not_called()
        self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()
