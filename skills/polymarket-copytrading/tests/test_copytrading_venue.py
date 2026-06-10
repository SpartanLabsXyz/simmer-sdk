import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

_mock_cfg = {
    "wallets": "",
    "top_n": "",
    "max_usd": 50.0,
    "max_trades_per_run": 10,
    "venue": "",
    "order_type": "GTC",
    "cadence_mode": "polling",
}

_skill_stub = types.ModuleType("simmer_sdk.skill")
_skill_stub.load_config = lambda schema, file, slug=None: _mock_cfg.copy()
_skill_stub.update_config = lambda updates, file, slug=None: None
_skill_stub.get_config_path = lambda file: "/tmp/config.json"

with patch.dict(sys.modules, {
    "simmer_sdk": MagicMock(),
    "simmer_sdk.skill": _skill_stub,
}):
    import copytrading_trader as ct  # noqa: E402


def _trade_result(success=True):
    result = MagicMock()
    result.success = success
    result.error = None
    result.trade_id = "trade_test"
    result.retryable = True
    return result


class TestCopytradingVenue(unittest.TestCase):
    def setUp(self):
        ct._config["venue"] = ""

    def test_explicit_venue_reaches_plan_and_trade_when_client_default_differs(self):
        client = MagicMock()
        client.venue = "polymarket"
        client._request.return_value = {
            "success": True,
            "trades": [{
                "market_id": "mkt_1",
                "side": "yes",
                "action": "buy",
                "shares": 12.0,
                "estimated_cost": 5.0,
                "market_title": "Test market",
            }],
        }
        client.trade.return_value = _trade_result()

        with patch.dict(os.environ, {"TRADING_VENUE": "polymarket"}), \
             patch.object(ct, "get_client", return_value=client):
            result = ct.execute_copytrading(
                ["0xwhale"],
                max_usd=5.0,
                dry_run=False,
                venue="sim",
            )

        self.assertEqual(result["trades_executed"], 1)
        plan_kwargs = client._request.call_args.kwargs
        self.assertEqual(plan_kwargs["json"]["venue"], "sim")
        trade_kwargs = client.trade.call_args.kwargs
        self.assertEqual(trade_kwargs["venue"], "sim")

    def test_run_copytrading_uses_effective_venue_for_preflight_and_execution(self):
        client = MagicMock()
        client.venue = "polymarket"

        with patch.dict(os.environ, {"TRADING_VENUE": "polymarket"}), \
             patch.object(ct, "get_client", return_value=client), \
             patch.object(ct, "execute_copytrading", return_value={
                 "success": True,
                 "trades": [],
                 "trades_needed": 0,
                 "trades_executed": 0,
                 "summary": "ok",
             }) as execute:
            ct.run_copytrading(
                wallets=["0xwhale"],
                max_usd=5.0,
                dry_run=False,
                venue="sim",
            )

        client.ensure_can_trade.assert_not_called()
        self.assertEqual(execute.call_args.kwargs["venue"], "sim")


if __name__ == "__main__":
    unittest.main()
