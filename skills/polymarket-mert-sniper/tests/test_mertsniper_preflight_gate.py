"""
Smoke tests for the preflight gate in polymarket-mert-sniper.

Verifies execute_trade():
  - returns {"error": "preflight_blocked: ..."} and does NOT call client.trade()
    when preflight returns ok_to_trade=False
  - calls client.trade() normally when preflight returns ok_to_trade=True
  - skips preflight entirely in paper mode (client.live=False)

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
    "market_filter": "", "max_bet_usd": 10.00, "expiry_window_mins": 8,
    "min_split": 0.60, "max_trades_per_run": 5, "sizing_pct": 0.05,
    "order_type": "GTC", "fee_buffer": 0.02, "min_edge": 0.0,
    "enable_news_veto": True,
}

_skill_stub = types.ModuleType("simmer_sdk.skill")
_skill_stub.load_config = lambda schema, file, slug=None: _mock_cfg.copy()
_skill_stub.update_config = lambda updates, file, slug=None: None
_skill_stub.get_config_path = lambda file: "/tmp/config.json"
_guards_stub = types.ModuleType("simmer_sdk.guards.news_recency_veto")
_guards_stub.load_macro_news_schedule = lambda path=None: {"events": []}
_guards_stub.news_window_match = lambda market_id, schedule, lookback_s=30, now=None: (False, None)

with patch.dict(sys.modules, {
    "simmer_sdk": MagicMock(),
    "simmer_sdk.skill": _skill_stub,
    "simmer_sdk.guards.news_recency_veto": _guards_stub,
}):
    import mert_sniper as ms  # noqa: E402


def _make_preflight(ok=False, blockers=None):
    pf = MagicMock()
    pf.ok_to_trade = ok
    pf.blockers = blockers or ["WALLET_UNVERIFIED"]
    return pf


def _make_trade_result(success=True, simulated=False):
    r = MagicMock()
    r.success = success
    r.trade_id = "t_test"
    r.shares_bought = 10.0
    r.error = None
    r.simulated = simulated
    return r


class TestPreflightGateMertSniper(unittest.TestCase):

    def test_blocked_returns_error_dict_and_no_trade(self):
        mock_client = MagicMock()
        mock_client.live = True
        mock_client.venue = "polymarket"
        mock_client.preflight.return_value = _make_preflight(ok=False, blockers=["WALLET_UNVERIFIED"])

        with patch.object(ms, "get_client", return_value=mock_client):
            result = ms.execute_trade("mkt_abc", "yes", 5.0)

        self.assertEqual(result, {"error": "preflight_blocked: WALLET_UNVERIFIED"})
        mock_client.trade.assert_not_called()

    def test_blocked_multiple_blockers_joined(self):
        mock_client = MagicMock()
        mock_client.live = True
        mock_client.venue = "polymarket"
        mock_client.preflight.return_value = _make_preflight(
            ok=False, blockers=["WALLET_UNVERIFIED", "VENUE_UNSUPPORTED"]
        )

        with patch.object(ms, "get_client", return_value=mock_client):
            result = ms.execute_trade("mkt_abc", "yes", 5.0)

        self.assertEqual(result["error"], "preflight_blocked: WALLET_UNVERIFIED, VENUE_UNSUPPORTED")
        mock_client.trade.assert_not_called()

    def test_preflight_ok_calls_trade(self):
        mock_client = MagicMock()
        mock_client.live = True
        mock_client.venue = "polymarket"
        mock_client.preflight.return_value = _make_preflight(ok=True)
        mock_client.trade.return_value = _make_trade_result(success=True)

        with patch.object(ms, "get_client", return_value=mock_client):
            result = ms.execute_trade("mkt_abc", "yes", 5.0)

        mock_client.trade.assert_called_once()
        self.assertTrue(result["success"])

    def test_preflight_called_with_cap_zero(self):
        mock_client = MagicMock()
        mock_client.live = True
        mock_client.venue = "polymarket"
        mock_client.preflight.return_value = _make_preflight(ok=True)
        mock_client.trade.return_value = _make_trade_result()

        with patch.object(ms, "get_client", return_value=mock_client):
            ms.execute_trade("mkt_abc", "yes", 8.0)

        mock_client.preflight.assert_called_once_with(
            planned_amount=8.0, exposure_cap_usd=0, venue="polymarket"
        )

    def test_paper_mode_skips_preflight(self):
        mock_client = MagicMock()
        mock_client.live = False
        mock_client.venue = "polymarket"
        mock_client.trade.return_value = _make_trade_result(simulated=True)

        with patch.object(ms, "get_client", return_value=mock_client):
            result = ms.execute_trade("mkt_abc", "yes", 5.0)

        mock_client.preflight.assert_not_called()
        self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()
