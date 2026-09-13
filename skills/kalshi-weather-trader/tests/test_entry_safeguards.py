"""
Pinned proofs for Kalshi weather entry safeguards.

Pure-unit: no network, no SIMMER_API_KEY.
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
}

_skill_mod = types.ModuleType("simmer_sdk.skill")
_skill_mod.load_config = lambda schema, file, slug=None: _mock_cfg.copy()
_skill_mod.update_config = lambda updates, file, slug=None: None
_skill_mod.get_config_path = lambda file: "/tmp/config.json"
sys.modules["simmer_sdk"] = MagicMock()
sys.modules["simmer_sdk.skill"] = _skill_mod

import weather_trader as wt  # noqa: E402


def _context(time_to_resolution=""):
    return {
        "market": {"time_to_resolution": time_to_resolution},
        "warnings": [],
        "discipline": {},
        "slippage": {},
        "edge": {},
    }


class TestMinEntryPrice(unittest.TestCase):
    def tearDown(self):
        wt.MIN_ENTRY_PRICE = 0.0
        wt.ENTRY_THRESHOLD = 0.15

    def test_schema_knob_is_env_backed_default_off(self):
        spec = wt.CONFIG_SCHEMA["min_entry_price"]
        self.assertEqual(spec["env"], "SIMMER_WEATHER_MIN_ENTRY_PRICE")
        self.assertEqual(spec["default"], 0.0)
        self.assertIs(spec["type"], float)

    def test_default_zero_allows_lottery_mid(self):
        wt.MIN_ENTRY_PRICE = 0.0
        wt.ENTRY_THRESHOLD = 0.50
        ok, reason = wt.check_entry_price(0.10)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_floor_rejects_low_mid(self):
        wt.MIN_ENTRY_PRICE = 0.15
        wt.ENTRY_THRESHOLD = 0.50
        ok, reason = wt.check_entry_price(0.10)
        self.assertFalse(ok)
        self.assertIn("below min entry", reason)
        self.assertIn("0.10", reason)
        self.assertIn("0.15", reason)

    def test_floor_allows_mid_inside_band(self):
        wt.MIN_ENTRY_PRICE = 0.15
        wt.ENTRY_THRESHOLD = 0.50
        ok, reason = wt.check_entry_price(0.20)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_upper_bound_still_rejects_above_entry(self):
        wt.MIN_ENTRY_PRICE = 0.15
        wt.ENTRY_THRESHOLD = 0.50
        ok, reason = wt.check_entry_price(0.55)
        self.assertFalse(ok)
        self.assertIn("above threshold", reason)

    def test_floor_is_invisible_to_context_safeguards(self):
        wt.MIN_ENTRY_PRICE = 0.15
        ok, reasons = wt.check_context_safeguards(_context("3h"))
        self.assertTrue(ok)
        self.assertFalse(any("min entry" in r for r in reasons))


class TestMinHoursToResolve(unittest.TestCase):
    def tearDown(self):
        wt.TIME_TO_RESOLUTION_MIN_HOURS = 2

    def test_schema_knob_is_env_backed_default_two(self):
        spec = wt.CONFIG_SCHEMA["min_hours_to_resolve"]
        self.assertEqual(spec["env"], "SIMMER_WEATHER_MIN_HOURS_TO_RESOLVE")
        self.assertEqual(spec["default"], 2)
        self.assertIs(spec["type"], float)

    def test_default_two_hours_rejects_one_hour(self):
        wt.TIME_TO_RESOLUTION_MIN_HOURS = 2
        ok, reasons = wt.check_context_safeguards(_context("1h"))
        self.assertFalse(ok)
        self.assertTrue(any("too soon" in r for r in reasons))

    def test_raised_floor_skips_resolve_day(self):
        wt.TIME_TO_RESOLUTION_MIN_HOURS = 24
        ok, reasons = wt.check_context_safeguards(_context("10h"))
        self.assertFalse(ok)
        self.assertTrue(any("too soon" in r for r in reasons))

    def test_raised_floor_allows_next_day(self):
        wt.TIME_TO_RESOLUTION_MIN_HOURS = 24
        ok, reasons = wt.check_context_safeguards(_context("1d 6h"))
        self.assertTrue(ok)
        self.assertFalse(any("too soon" in r for r in reasons))

    def test_exits_keep_two_hour_floor_when_entry_knob_is_raised(self):
        wt.TIME_TO_RESOLUTION_MIN_HOURS = 24
        ok, reasons = wt.check_context_safeguards(_context("3h"), min_hours=wt.EXIT_MIN_HOURS_TO_RESOLVE)
        self.assertTrue(ok)
        self.assertFalse(any("too soon" in r for r in reasons))

    def test_exit_floor_is_the_original_two_hours(self):
        self.assertEqual(wt.EXIT_MIN_HOURS_TO_RESOLVE, 2)
        wt.TIME_TO_RESOLUTION_MIN_HOURS = 24
        ok, _ = wt.check_context_safeguards(_context("1h"), min_hours=wt.EXIT_MIN_HOURS_TO_RESOLVE)
        self.assertFalse(ok)


class TestEntryPathOrdering(unittest.TestCase):
    def tearDown(self):
        wt.MIN_ENTRY_PRICE = 0.0
        wt.ENTRY_THRESHOLD = 0.15
        wt.ACTIVE_LOCATIONS = ["NYC"]

    def test_below_floor_candidate_makes_zero_context_or_history_calls(self):
        wt.MIN_ENTRY_PRICE = 0.15
        wt.ENTRY_THRESHOLD = 0.50
        wt.ACTIVE_LOCATIONS = ["NYC"]
        market = {
            "id": "market-1",
            "event_id": "event-1",
            "event_name": "Highest temperature in NYC on September 30",
            "outcome_name": "70 to 80",
            "external_price_yes": 0.10,
        }

        with patch.object(wt, "get_client") as get_client, \
                patch.object(wt, "discover_and_import_weather_markets", return_value=0), \
                patch.object(wt, "fetch_weather_markets", return_value=[market]), \
                patch.object(wt, "get_noaa_forecast", return_value={"2026-09-30": {"high": 75, "low": 60}}), \
                patch.object(wt, "get_market_context") as get_market_context, \
                patch.object(wt, "get_price_history") as get_price_history, \
                patch.object(wt, "check_exit_opportunities", return_value=(0, 0)):
            get_client.return_value.auto_redeem.return_value = []

            wt.run_weather_strategy(dry_run=True, quiet=True)

        get_market_context.assert_not_called()
        get_price_history.assert_not_called()


if __name__ == "__main__":
    unittest.main()
