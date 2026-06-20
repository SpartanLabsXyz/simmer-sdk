"""
Unit tests for SIM-2420: multi-source bucket-confidence scoring.

Covers the 4 tier branches of evaluate_source_agreement +
apply_source_tier_to_sizing. Pure unit (no network, no SDK).
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
    "exit_threshold": 0.45,
    "max_position_usd": 5.00,
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
    # SIM-2420 knobs
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


# Atlanta May 26 2026 high-temperature market — 4 adjacent °F buckets.
ATL_BUCKETS = [
    {"outcome_name": "80-81°F", "id": "m-80"},
    {"outcome_name": "82-83°F", "id": "m-82"},
    {"outcome_name": "84-85°F", "id": "m-84"},
    {"outcome_name": "86-87°F", "id": "m-86"},
]


class TestEvaluateSourceAgreement(unittest.TestCase):

    def test_match_same_bucket(self):
        # NOAA 83, Open-Meteo 82 → both land in 82-83°F bucket.
        tier, spread, sec = wt.evaluate_source_agreement(
            83, 82, "82-83°F", ATL_BUCKETS, "°F",
        )
        self.assertEqual(tier, "match")
        self.assertEqual(spread, 1)
        self.assertEqual(sec, "82-83°F")

    def test_adjacent_buckets(self):
        # NOAA 83 → 82-83 bucket. Open-Meteo 84 → 84-85 bucket. Spread 1°F.
        tier, spread, sec = wt.evaluate_source_agreement(
            83, 84, "82-83°F", ATL_BUCKETS, "°F",
        )
        self.assertEqual(tier, "adjacent")
        self.assertEqual(spread, 1)
        self.assertEqual(sec, "84-85°F")

    def test_wide_by_spread(self):
        # NOAA 83, Open-Meteo 86 → spread 3°F > MAX_SOURCE_SPREAD_F (2.0).
        tier, spread, _ = wt.evaluate_source_agreement(
            83, 86, "82-83°F", ATL_BUCKETS, "°F",
        )
        self.assertEqual(tier, "wide")
        self.assertEqual(spread, 3)

    def test_wide_by_nonadjacent_bucket(self):
        # Spread = 2°F (at threshold), but buckets are 82-83 vs 86-87 (2 apart).
        # We can't actually hit this with the 2°F cap since 83→85 is 2 and lands in 84-85.
        # Construct a market with sparser buckets to test bucket non-adjacency:
        sparse = [
            {"outcome_name": "70-72°F"},
            {"outcome_name": "73-75°F"},
            {"outcome_name": "76-78°F"},
        ]
        # NOAA 71, Open-Meteo 78 — but spread 7°F > MAX, so tier=wide via spread first.
        # Instead test adjacency boundary precisely: bump MAX so spread doesn't trip.
        orig = wt.MAX_SOURCE_SPREAD_F
        wt.MAX_SOURCE_SPREAD_F = 10.0
        try:
            tier, _, _ = wt.evaluate_source_agreement(
                71, 78, "70-72°F", sparse, "°F",
            )
            self.assertEqual(tier, "wide")  # 70-72 → 76-78 is 2 buckets apart
        finally:
            wt.MAX_SOURCE_SPREAD_F = orig

    def test_missing_secondary(self):
        tier, spread, sec = wt.evaluate_source_agreement(
            83, None, "82-83°F", ATL_BUCKETS, "°F",
        )
        self.assertEqual(tier, "missing_secondary")
        self.assertIsNone(spread)
        self.assertIsNone(sec)

    def test_celsius_spread_threshold(self):
        # °C market: MAX_SOURCE_SPREAD_F=2.0 → effective max ≈ 1.11°C.
        # Primary 22°C, secondary 24°C → spread 2°C > 1.11°C → wide.
        c_buckets = [
            {"outcome_name": "20-21°C"},
            {"outcome_name": "22-23°C"},
            {"outcome_name": "24-25°C"},
        ]
        tier, spread, _ = wt.evaluate_source_agreement(
            22, 24, "22-23°C", c_buckets, "°C",
        )
        self.assertEqual(tier, "wide")
        self.assertEqual(spread, 2)


class TestApplySourceTier(unittest.TestCase):

    def setUp(self):
        # Snapshot + reset all 4 knobs each test
        self._snap = (
            wt.REQUIRE_SOURCE_AGREEMENT, wt.CANARY_ON_ADJACENT,
            wt.MAX_CANARY_USD, wt.MAX_SOURCE_SPREAD_F,
        )
        wt.REQUIRE_SOURCE_AGREEMENT = False
        wt.CANARY_ON_ADJACENT = True
        wt.MAX_CANARY_USD = 2.0
        wt.MAX_SOURCE_SPREAD_F = 2.0

    def tearDown(self):
        (wt.REQUIRE_SOURCE_AGREEMENT, wt.CANARY_ON_ADJACENT,
         wt.MAX_CANARY_USD, wt.MAX_SOURCE_SPREAD_F) = self._snap

    def test_match_passes_size_unchanged(self):
        size, reason = wt.apply_source_tier_to_sizing("match", 5.0)
        self.assertEqual(size, 5.0)
        self.assertIn("agree", reason.lower())

    def test_adjacent_caps_to_canary(self):
        size, reason = wt.apply_source_tier_to_sizing("adjacent", 5.0)
        self.assertEqual(size, 2.0)
        self.assertIn("canary", reason.lower())

    def test_adjacent_under_canary_not_capped_up(self):
        # Canary cap is a ceiling, not a floor.
        size, _ = wt.apply_source_tier_to_sizing("adjacent", 1.0)
        self.assertEqual(size, 1.0)

    def test_adjacent_with_require_agreement_skips(self):
        wt.REQUIRE_SOURCE_AGREEMENT = True
        size, reason = wt.apply_source_tier_to_sizing("adjacent", 5.0)
        self.assertIsNone(size)
        self.assertIn("require", reason.lower())

    def test_adjacent_with_canary_off_passes_full_size(self):
        wt.CANARY_ON_ADJACENT = False
        size, reason = wt.apply_source_tier_to_sizing("adjacent", 5.0)
        self.assertEqual(size, 5.0)
        self.assertIn("disabled", reason.lower())

    def test_wide_skips(self):
        size, reason = wt.apply_source_tier_to_sizing("wide", 5.0)
        self.assertIsNone(size)
        self.assertIn("spread", reason.lower())

    def test_missing_secondary_caps_to_canary(self):
        # SIM-3412: missing secondary → canary cap, not full size.
        size, reason = wt.apply_source_tier_to_sizing("missing_secondary", 5.0)
        self.assertEqual(size, 2.0)
        self.assertIn("canary", reason.lower())

    def test_missing_secondary_under_canary_not_capped_up(self):
        # Canary cap is a ceiling, not a floor.
        size, _ = wt.apply_source_tier_to_sizing("missing_secondary", 1.0)
        self.assertEqual(size, 1.0)

    def test_missing_secondary_with_require_agreement_skips(self):
        wt.REQUIRE_SOURCE_AGREEMENT = True
        size, reason = wt.apply_source_tier_to_sizing("missing_secondary", 5.0)
        self.assertIsNone(size)
        self.assertIn("require", reason.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
