"""Pinned proofs for #368: replay-compatible weather discovery + fail-closed fetch.

Replay rejects tags/status (sdk 0.25.3/0.25.4, 422). The skill uses the
existing replay q= path under SIMMER_REPLAY=1. A failed listing must raise
so the harness can mark failed_ticks — returning [] exits 0 and paints
bundle.clean=true on a 0-eval tick.

Pure-unit: no network, no live Polymarket, no SIMMER_API_KEY.
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

_mock_cfg = {
    "entry_threshold": 0.15,
    "min_entry_price": 0.0,
    "min_hours_to_resolve": 2,
    "exit_threshold": 0.45,
    "max_position_usd": 2.00,
    "sizing_pct": 0.05,
    "max_trades_per_run": 5,
    "locations": "NYC",
    "binary_only": False,
    "slippage_max": 0.15,
    "min_liquidity": 0.0,
    "order_type": "GTC",
    "vol_targeting": False,
    "target_vol": 0.20,
    "vol_max_leverage": 2.0,
    "vol_min_allocation": 0.2,
    "vol_span": 10,
    "require_source_agreement": False,
    "canary_on_adjacent": True,
    "max_canary_usd": 2.0,
    "max_source_spread_f": 2.0,
}

_skill_mod = types.ModuleType("simmer_sdk.skill")
_skill_mod.load_config = lambda schema, file, slug=None: _mock_cfg.copy()
_skill_mod.update_config = lambda updates, file, slug=None: None
_skill_mod.get_config_path = lambda file: "/tmp/config.json"
sys.modules["simmer_sdk"] = MagicMock()
sys.modules["simmer_sdk.skill"] = _skill_mod

import weather_trader as wt  # noqa: E402


class TestWeatherMarketsParams(unittest.TestCase):
    """Live keeps tags=weather; replay uses q=temperature only."""

    def tearDown(self):
        os.environ.pop("SIMMER_REPLAY", None)

    def test_live_sends_tags_and_status_not_q(self):
        os.environ.pop("SIMMER_REPLAY", None)
        params = wt._weather_markets_params()
        self.assertEqual(params["tags"], "weather")
        self.assertEqual(params["status"], "active")
        self.assertNotIn("q", params)
        self.assertEqual(params["include"], "resolution_criteria")

    def test_replay_uses_q_temperature_omits_tags_status(self):
        os.environ["SIMMER_REPLAY"] = "1"
        params = wt._weather_markets_params()
        self.assertEqual(params["q"], "temperature")
        self.assertNotIn("tags", params)
        self.assertNotIn("status", params)
        self.assertEqual(params["include"], "resolution_criteria")

    def test_replay_flag_must_be_exactly_one(self):
        os.environ["SIMMER_REPLAY"] = "true"
        self.assertFalse(wt._is_replay())
        params = wt._weather_markets_params()
        self.assertEqual(params.get("tags"), "weather")


class TestFetchWeatherMarketsFailClosed(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("SIMMER_REPLAY", None)
        wt._client = None

    def test_live_request_uses_live_params(self):
        client = MagicMock()
        client._request.return_value = {"markets": [{"id": "m1"}]}
        wt.get_client = MagicMock(return_value=client)
        os.environ.pop("SIMMER_REPLAY", None)

        markets = wt.fetch_weather_markets()

        # Same market on every horizon page dedupes to one row.
        self.assertEqual(markets, [{"id": "m1"}])
        calls = client._request.call_args_list
        # Base page first (no q), then one dated page per horizon day.
        self.assertGreaterEqual(len(calls), 2)
        for call in calls:
            _method, path = call.args[:2]
            self.assertEqual(_method, "GET")
            self.assertEqual(path, "/api/sdk/markets")
            sent = call.kwargs["params"]
            self.assertEqual(sent["tags"], "weather")
            self.assertEqual(sent["status"], "active")
        self.assertNotIn("q", calls[0].kwargs["params"])
        for call in calls[1:]:
            self.assertIn("q", call.kwargs["params"])

    def test_replay_request_omits_filters_that_422(self):
        client = MagicMock()
        client._request.return_value = {"markets": []}
        wt.get_client = MagicMock(return_value=client)
        os.environ["SIMMER_REPLAY"] = "1"

        markets = wt.fetch_weather_markets()

        self.assertEqual(markets, [])
        sent = client._request.call_args.kwargs["params"]
        self.assertEqual(sent["q"], "temperature")
        self.assertNotIn("tags", sent)
        self.assertNotIn("status", sent)

    def test_replay_422_raises_not_empty_list(self):
        """The dogfood 422 must not become [] / exit 0."""
        client = MagicMock()
        client._request.side_effect = Exception(
            "422 replay does not filter on 'tags'"
        )
        wt.get_client = MagicMock(return_value=client)
        os.environ["SIMMER_REPLAY"] = "1"

        with self.assertRaises(wt.MarketFetchError) as ctx:
            wt.fetch_weather_markets()
        self.assertIn("Failed to fetch markets", str(ctx.exception))

    def test_any_listing_failure_raises_market_fetch_error(self):
        client = MagicMock()
        client._request.side_effect = RuntimeError("connection refused")
        wt.get_client = MagicMock(return_value=client)

        with self.assertRaises(wt.MarketFetchError):
            wt.fetch_weather_markets()

    def test_empty_200_is_honest_empty_tape_not_failure(self):
        client = MagicMock()
        client._request.return_value = {"markets": []}
        wt.get_client = MagicMock(return_value=client)

        self.assertEqual(wt.fetch_weather_markets(), [])


class TestRunFailClosedOnFetch(unittest.TestCase):
    """run_weather_strategy must not swallow MarketFetchError (exit 0 / clean)."""

    def tearDown(self):
        wt._client = None

    def test_run_propagates_fetch_failure(self):
        client = MagicMock()
        client.auto_redeem.return_value = []
        fetch = MagicMock(
            side_effect=wt.MarketFetchError("Failed to fetch markets from Simmer API")
        )
        # patch.object restores the module attributes after the test; a bare
        # assignment leaks the raising mock into whichever test runs next.
        with patch.object(wt, "get_client", MagicMock(return_value=client)), \
             patch.object(wt, "discover_and_import_weather_markets", MagicMock(return_value=0)), \
             patch.object(wt, "fetch_weather_markets", fetch), \
             self.assertRaises(wt.MarketFetchError):
            wt.run_weather_strategy(dry_run=True, quiet=True)


if __name__ == "__main__":
    unittest.main()
