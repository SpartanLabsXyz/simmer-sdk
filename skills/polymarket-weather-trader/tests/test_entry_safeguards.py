"""
Pinned proofs for #349: min-entry floor + env-ified hours-to-resolve.

Both gates reuse existing reject paths:
  - check_entry_price() is the ENTRY_THRESHOLD upper-bound check plus the
    missing MIN_ENTRY_PRICE floor (entry path only).
  - TIME_TO_RESOLUTION_MIN_HOURS still lives in check_context_safeguards
    beside flip-flop / slippage / resolved — now an env-backed knob.

Pure-unit: no network, no SIMMER_API_KEY.
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock


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


def _context(time_to_resolution=""):
    return {
        "market": {"time_to_resolution": time_to_resolution},
        "warnings": [],
        "discipline": {},
        "slippage": {},
        "edge": {},
    }


class TestMinEntryPrice(unittest.TestCase):
    """Dogfood bought Amsterdam ~$0.10 with ENTRY=0.50 and no floor."""

    def tearDown(self):
        wt.MIN_ENTRY_PRICE = 0.0
        wt.ENTRY_THRESHOLD = 0.15

    def test_schema_knob_is_env_backed_default_off(self):
        spec = wt.CONFIG_SCHEMA["min_entry_price"]
        self.assertEqual(spec["env"], "SIMMER_WEATHER_MIN_ENTRY_PRICE")
        self.assertEqual(spec["default"], 0.0)
        self.assertIs(spec["type"], float)

    def test_default_zero_allows_lottery_mid(self):
        """Back-compat: default 0 does not reject a 10¢ ticket under a raised entry."""
        wt.MIN_ENTRY_PRICE = 0.0
        wt.ENTRY_THRESHOLD = 0.50
        ok, reason = wt.check_entry_price(0.10)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_floor_rejects_dogfood_lottery_ticket(self):
        """Pinned: mid 0.10 / ENTRY 0.50 / MIN 0.15 → reject (Amsterdam resolve-day)."""
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

    def test_boundary_min_is_inclusive(self):
        wt.MIN_ENTRY_PRICE = 0.15
        wt.ENTRY_THRESHOLD = 0.50
        ok, _ = wt.check_entry_price(0.15)
        self.assertTrue(ok)

    def test_boundary_entry_is_exclusive(self):
        wt.MIN_ENTRY_PRICE = 0.15
        wt.ENTRY_THRESHOLD = 0.50
        ok, _ = wt.check_entry_price(0.50)
        self.assertFalse(ok)

    def test_floor_is_invisible_to_context_safeguards(self):
        """Exits call check_context_safeguards only — the floor must not reject there."""
        wt.MIN_ENTRY_PRICE = 0.15
        ok, reasons = wt.check_context_safeguards(_context("3h"))
        self.assertTrue(ok)
        self.assertFalse(any("min entry" in r for r in reasons))


class TestMinHoursToResolve(unittest.TestCase):
    """TIME_TO_RESOLUTION_MIN_HOURS stays in check_context_safeguards."""

    def tearDown(self):
        wt.TIME_TO_RESOLUTION_MIN_HOURS = 2

    def test_schema_knob_is_env_backed_default_two(self):
        spec = wt.CONFIG_SCHEMA["min_hours_to_resolve"]
        self.assertEqual(spec["env"], "SIMMER_WEATHER_MIN_HOURS_TO_RESOLVE")
        self.assertEqual(spec["default"], 2)
        self.assertIs(spec["type"], int)

    def test_default_two_hours_rejects_one_hour(self):
        wt.TIME_TO_RESOLUTION_MIN_HOURS = 2
        ok, reasons = wt.check_context_safeguards(_context("1h"))
        self.assertFalse(ok)
        self.assertTrue(any("too soon" in r for r in reasons))

    def test_default_two_hours_allows_three_hours(self):
        wt.TIME_TO_RESOLUTION_MIN_HOURS = 2
        ok, reasons = wt.check_context_safeguards(_context("3h"))
        self.assertTrue(ok)
        self.assertFalse(any("too soon" in r for r in reasons))

    def test_raised_floor_skips_resolve_day(self):
        """Pinned: MIN_HOURS=24 rejects a same-day 10h print (dogfood resolve-day)."""
        wt.TIME_TO_RESOLUTION_MIN_HOURS = 24
        ok, reasons = wt.check_context_safeguards(_context("10h"))
        self.assertFalse(ok)
        self.assertTrue(any("too soon" in r for r in reasons))

    def test_raised_floor_allows_next_day(self):
        wt.TIME_TO_RESOLUTION_MIN_HOURS = 24
        ok, reasons = wt.check_context_safeguards(_context("1d 3h"))
        self.assertTrue(ok)
        self.assertFalse(any("too soon" in r for r in reasons))

    def test_module_constant_reads_schema_default(self):
        self.assertEqual(wt.TIME_TO_RESOLUTION_MIN_HOURS, 2)
        self.assertEqual(wt.MIN_ENTRY_PRICE, 0.0)


if __name__ == "__main__":
    unittest.main()
