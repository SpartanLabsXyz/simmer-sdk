import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

_mock_cfg = {
    "price_cap": 0.10,
    "max_bet_usd": 5.0,
    "max_trades_per_run": 3,
    "daily_budget": 15.0,
    "min_liquidity": 500.0,
    "min_volume_24h": 100.0,
    "candidate_pages": 20,
}

_skill_stub = types.ModuleType("simmer_sdk.skill")
_skill_stub.load_config = lambda schema, file, slug=None: _mock_cfg.copy()
_skill_stub.update_config = lambda updates, file, slug=None: None
_skill_stub.get_config_path = lambda file: "/tmp/config.json"

_simmer_sdk_stub = types.ModuleType("simmer_sdk")

with patch.dict(sys.modules, {
    "simmer_sdk": _simmer_sdk_stub,
    "simmer_sdk.skill": _skill_stub,
}):
    import nothing_ever_happens as neh  # noqa: E402


def _trade_result(success=True, simulated=True):
    result = MagicMock()
    result.success = success
    result.trade_id = "trade_test"
    result.shares_bought = 10.0
    result.error = None
    result.simulated = simulated
    return result


class TestNothingEverHappensVenue(unittest.TestCase):
    def setUp(self):
        neh._client = None
        neh._client_venue = None

    def test_trading_venue_sim_reaches_client_constructor(self):
        client_cls = MagicMock()
        _simmer_sdk_stub.SimmerClient = client_cls

        with patch.dict(os.environ, {
            "SIMMER_API_KEY": "test-key",
            "TRADING_VENUE": "sim",
        }), patch.dict(sys.modules, {"simmer_sdk": _simmer_sdk_stub}):
            neh.get_client(live=True)

        client_cls.assert_called_once_with(api_key="test-key", venue="sim", live=True)

    def test_execute_trade_passes_resolved_venue_to_trade(self):
        client = MagicMock()
        client.trade.return_value = _trade_result(simulated=True)

        with patch.dict(os.environ, {"TRADING_VENUE": "sim"}), \
             patch.object(neh, "get_client", return_value=client):
            result = neh.execute_trade("mkt_1", 5.0)

        self.assertTrue(result["success"])
        self.assertEqual(client.trade.call_args.kwargs["venue"], "sim")

    def test_live_sim_main_skips_polymarket_preflight_and_executes_sim_venue(self):
        client = MagicMock()
        client.auto_redeem.return_value = []

        with patch.dict(os.environ, {
            "SIMMER_API_KEY": "test-key",
            "TRADING_VENUE": "sim",
        }), \
             patch.object(sys, "argv", ["nothing_ever_happens.py", "--live", "--quiet"]), \
             patch.object(neh, "get_client", return_value=client), \
             patch.object(neh, "fetch_candidate_markets", return_value=[]), \
             patch.object(neh, "run_trades", return_value=(0, 0, 0, [], 0.0, [])) as run_trades:
            neh.main()

        client.ensure_can_trade.assert_not_called()
        self.assertEqual(run_trades.call_args.kwargs["dry_run"], False)
        self.assertEqual(run_trades.call_args.kwargs["venue"], "sim")


if __name__ == "__main__":
    unittest.main()
