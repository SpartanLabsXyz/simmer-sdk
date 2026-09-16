"""Pinned proofs for SIM-5442: BTC up-down replay plane.

Under SIMMER_REPLAY=1 the skill must:
  - list markets from the Simmer tape (q=up or down; no tags/status)
  - never hit live Gamma or CLOB
  - honor SIMMER_REPLAY_NOW for horizon
  - use tape yes_price (not a live midpoint)
  - skip_preflight so WALLET_UNVERIFIED cannot block SimState fills
  - not burn daily spend on a failed trade

Pure-unit: no network, no live Polymarket, no SIMMER_API_KEY.
"""

import os
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

_mock_cfg = {
    "entry_threshold": 0.05,
    "min_momentum_pct": 0.3,
    "max_position": 10.0,
    "lookback_minutes": 30,
    "daily_budget": 50.0,
    "min_hours_to_resolution": 4.0,
    "exit_before_resolution_hours": 1.0,
    "volume_spike_exit_multiplier": 3.0,
    "target_hit_capture_pct": 0.85,
    "volume_baseline_windows": 6,
}

skill_module = types.ModuleType("simmer_sdk.skill")
skill_module.load_config = lambda schema, file, slug=None: _mock_cfg.copy()
skill_module.update_config = lambda updates, file, slug=None: None
skill_module.get_config_path = lambda file: "/tmp/config.json"
sys.modules["simmer_sdk"] = MagicMock()
sys.modules["simmer_sdk.skill"] = skill_module

import strategy as strat  # noqa: E402


REPLAY_NOW = "2026-04-14T12:00:00+00:00"
REPLAY_DT = datetime(2026, 4, 14, 12, tzinfo=timezone.utc)


def _tape_market(**overrides):
    """Replay /api/sdk/markets row: yes_price + resolves_at, no CLOB tokens."""
    row = {
        "id": "btc-ud-2026-04-15",
        "question": "Bitcoin Up or Down on April 15?",
        "slug": "bitcoin-up-or-down-on-april-15",
        "yes_price": 0.40,
        "current_probability": 0.40,
        "resolves_at": "2026-04-15T00:00:00+00:00",
        "polymarket_condition_id": "0xcond",
        "polymarket_token_id": "replay-btc-ud-yes",
    }
    row.update(overrides)
    return row


class _ReplayEnvMixin:
    def tearDown(self):
        os.environ.pop("SIMMER_REPLAY", None)
        os.environ.pop("SIMMER_REPLAY_NOW", None)
        strat._client = None


class TestReplayFlag(_ReplayEnvMixin, unittest.TestCase):
    def test_replay_flag_must_be_exactly_one(self):
        os.environ["SIMMER_REPLAY"] = "true"
        self.assertFalse(strat._is_replay())
        params = strat._btc_markets_params()
        self.assertEqual(params["q"], "up or down")

    def test_is_replay_true_only_for_one(self):
        os.environ["SIMMER_REPLAY"] = "1"
        self.assertTrue(strat._is_replay())


class TestReplayDiscoveryParams(_ReplayEnvMixin, unittest.TestCase):
    def test_replay_params_are_q_only(self):
        params = strat._btc_markets_params()
        self.assertEqual(params["q"], "up or down")
        self.assertNotIn("tags", params)
        self.assertNotIn("status", params)
        self.assertEqual(params["limit"], 200)


class TestFetchReplayMarkets(_ReplayEnvMixin, unittest.TestCase):
    def test_replay_request_uses_q_omits_filters_that_422(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        client = MagicMock()
        client._request.return_value = {"markets": [_tape_market()]}
        live_vendor = MagicMock(side_effect=AssertionError("live vendor is look-ahead"))
        with patch.object(strat, "get_client", return_value=client), \
             patch.object(strat, "_api_request", live_vendor):
            markets = strat.fetch_btc_updown_markets()
        self.assertEqual(len(markets), 1)
        sent = client._request.call_args.kwargs["params"]
        self.assertEqual(sent["q"], "up or down")
        self.assertNotIn("tags", sent)
        self.assertNotIn("status", sent)
        live_vendor.assert_not_called()
        path = client._request.call_args.args[1]
        self.assertEqual(path, "/api/sdk/markets")

    def test_replay_422_raises_not_empty_list(self):
        os.environ["SIMMER_REPLAY"] = "1"
        client = MagicMock()
        client._request.side_effect = Exception("422 replay does not filter on 'tags'")
        with patch.object(strat, "get_client", return_value=client), \
             self.assertRaises(strat.MarketFetchError):
            strat.fetch_btc_updown_markets()

    def test_empty_200_is_honest_empty_tape_not_failure(self):
        os.environ["SIMMER_REPLAY"] = "1"
        client = MagicMock()
        client._request.return_value = {"markets": []}
        with patch.object(strat, "get_client", return_value=client):
            self.assertEqual(strat.fetch_btc_updown_markets(), [])

    def test_live_still_uses_gamma_not_simmer_q(self):
        os.environ.pop("SIMMER_REPLAY", None)
        gamma_row = {
            "question": "Bitcoin Up or Down on September 16?",
            "slug": "bitcoin-up-or-down-on-september-16",
            "endDate": "2099-09-16T00:00:00+00:00",
            "clobTokenIds": ["yes", "no"],
            "conditionId": "live-cond",
        }
        with patch.object(strat, "_api_request", return_value=[gamma_row]) as api:
            markets = strat.fetch_btc_updown_markets()
        self.assertEqual(len(markets), 1)
        url = api.call_args.args[0]
        self.assertIn("gamma-api.polymarket.com", url)

    def test_filters_eth_and_fast_windows(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        rows = [
            _tape_market(),
            _tape_market(
                id="eth",
                question="Ethereum Up or Down on April 15?",
                slug="ethereum-up-or-down-on-april-15",
            ),
            _tape_market(
                id="fast",
                question="Bitcoin Up or Down - 5m",
                slug="btc-up-or-down-5m",
            ),
        ]
        client = MagicMock()
        client._request.return_value = {"markets": rows}
        with patch.object(strat, "get_client", return_value=client):
            markets = strat.fetch_btc_updown_markets()
        self.assertEqual([m["id"] for m in markets], ["btc-ud-2026-04-15"])


class TestReplayClockAndPrice(_ReplayEnvMixin, unittest.TestCase):
    def test_clock_uses_replay_now(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        self.assertEqual(strat._clock(), REPLAY_DT)

    def test_horizon_keeps_tape_row_wall_clock_would_drop(self):
        """Wall-clock Sept 2026 would mark an April 15 daily as already resolved."""
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        markets = strat._normalize_btc_markets([_tape_market()])
        self.assertEqual(len(markets), 1)
        self.assertGreater(markets[0]["_hours_to_resolution"], 4.0)
        self.assertLess(markets[0]["_hours_to_resolution"], 24.0)

        os.environ.pop("SIMMER_REPLAY", None)
        os.environ.pop("SIMMER_REPLAY_NOW", None)
        self.assertEqual(strat._normalize_btc_markets([_tape_market()]), [])

    def test_replay_yes_price_from_tape_not_half(self):
        os.environ["SIMMER_REPLAY"] = "1"
        self.assertEqual(strat._market_yes_price({"yes_price": 0.40}), 0.40)
        self.assertEqual(strat._market_yes_price({"current_probability": 0.11}), 0.11)
        self.assertIsNone(strat._market_yes_price({}))

    def test_fetch_live_midpoint_is_dark_under_replay(self):
        os.environ["SIMMER_REPLAY"] = "1"
        live_vendor = MagicMock(side_effect=AssertionError("CLOB is look-ahead"))
        with patch.object(strat, "_api_request", live_vendor):
            self.assertIsNone(strat.fetch_live_midpoint("token"))
        live_vendor.assert_not_called()

    def test_api_request_blocks_gamma_and_clob_under_replay(self):
        os.environ["SIMMER_REPLAY"] = "1"
        with self.assertRaises(strat.MarketFetchError):
            strat._api_request("https://gamma-api.polymarket.com/markets")
        with self.assertRaises(strat.MarketFetchError):
            strat._api_request("https://clob.polymarket.com/midpoint?token_id=x")


class TestReplayEntryAndSpend(_ReplayEnvMixin, unittest.TestCase):
    def setUp(self):
        strat.DAILY_BUDGET = 50.0
        strat.MAX_POSITION_USD = 10.0
        strat.MIN_MOMENTUM_PCT = 0.3
        strat.MIN_HOURS_TO_RESOLUTION = 4.0
        strat.ENTRY_THRESHOLD = 0.05

    def test_replay_entry_uses_tape_price_skips_preflight(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        client = MagicMock()
        result = MagicMock()
        result.success = True
        result.error = None
        client.trade.return_value = result
        market = _tape_market()
        market["_hours_to_resolution"] = 12.0
        market["_end_dt"] = datetime(2026, 4, 15, tzinfo=timezone.utc)
        live_vendor = MagicMock(side_effect=AssertionError("live vendor is look-ahead"))
        with patch.object(strat, "_load_daily_spend", return_value={"spent": 0.0, "trades": 0}), \
             patch.object(strat, "_save_daily_spend") as save, \
             patch.object(strat, "fetch_btc_momentum", return_value=(0.8, "up", 100000.0)), \
             patch.object(strat, "fetch_btc_updown_markets", return_value=[market]), \
             patch.object(strat, "fetch_live_midpoint", live_vendor), \
             patch.object(strat, "_api_request", live_vendor), \
             patch.object(strat, "log_trade"):
            entered = strat.run_entry_scan(client, live=True, quiet=True)
        self.assertEqual(entered, 1)
        live_vendor.assert_not_called()
        kwargs = client.trade.call_args.kwargs
        self.assertTrue(kwargs["skip_preflight"])
        self.assertEqual(kwargs["market_id"], "btc-ud-2026-04-15")
        self.assertEqual(kwargs["side"], "yes")
        save.assert_called_once()

    def test_failed_trade_does_not_burn_daily_spend(self):
        os.environ.pop("SIMMER_REPLAY", None)
        client = MagicMock()
        client.trade.return_value = {"success": False, "error": "no fill"}
        market = {
            "conditionId": "mkt_entry",
            "question": "Bitcoin up or down?",
            "clobTokenIds": ["yes_token", "no_token"],
            "_hours_to_resolution": 8.0,
            "_end_dt": datetime(2099, 1, 1, tzinfo=timezone.utc),
        }
        with patch.object(strat, "_load_daily_spend", return_value={"spent": 0.0, "trades": 0}), \
             patch.object(strat, "_save_daily_spend") as save, \
             patch.object(strat, "fetch_btc_momentum", return_value=(0.8, "up", 100000.0)), \
             patch.object(strat, "fetch_btc_updown_markets", return_value=[market]), \
             patch.object(strat, "fetch_live_midpoint", return_value=0.40), \
             patch.object(strat, "log_trade") as journal:
            entered = strat.run_entry_scan(client, live=True, quiet=True)
        self.assertEqual(entered, 0)
        save.assert_not_called()
        journal.assert_not_called()

    def test_replay_volume_spike_does_not_hit_clob(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        live_vendor = MagicMock(side_effect=AssertionError("CLOB volume is look-ahead"))
        end_dt = datetime(2026, 4, 15, tzinfo=timezone.utc)
        with patch.object(strat, "fetch_live_midpoint", live_vendor), \
             patch.object(strat, "check_volume_spike_exit", live_vendor):
            fired, reason, details = strat.evaluate_exit_triggers(
                {"entry_price": 0.40, "side": "YES"},
                end_dt,
                "token",
                current_price=0.41,
            )
        self.assertFalse(fired)
        self.assertIsNone(reason)
        live_vendor.assert_not_called()
        self.assertEqual(details["current_price"], 0.41)

    def test_replay_exit_looks_up_tape_not_gamma(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        client = MagicMock()
        client.get_positions.return_value = [
            {
                "marketId": "btc-ud-2026-04-15",
                "side": "YES",
                "quantity": 5.0,
                "source": strat.TRADE_SOURCE,
                "entry_price": 0.40,
            }
        ]
        client._request.return_value = {"market": _tape_market()}
        live_vendor = MagicMock(side_effect=AssertionError("Gamma is look-ahead"))
        with patch.object(strat, "_api_request", live_vendor):
            closed = strat.run_exit_monitor(client, live=False, quiet=True)
        self.assertEqual(closed, 0)
        live_vendor.assert_not_called()
        path = client._request.call_args.args[1]
        self.assertEqual(path, "/api/sdk/context/btc-ud-2026-04-15")


if __name__ == "__main__":
    unittest.main(verbosity=2)
