"""
Pinned proofs for #354: discovery horizon tracks MIN_HOURS_TO_RESOLVE.

Same-day newest-first fetch/import left morning heartbeats with only
resolve-day buckets. Horizon is max(MIN_HOURS, 48h) — no new env knob.

Pure-unit: no network, no SIMMER_API_KEY.
"""
import os
import sys
import types
import unittest
from datetime import datetime, timezone
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


# Dogfood 09:00 Asia/Shanghai heartbeat that starved after #350.
MORNING = datetime(2026, 9, 9, 1, 19, 14, tzinfo=timezone.utc)

# Same-day + future-day titles in the parse_weather_event shape.
FIXTURE_EVENT_NAMES = [
    "Highest temperature in New York City on September 9",
    "Highest temperature in New York City on September 10",
    "Highest temperature in Amsterdam on September 9",
    "Highest temperature in London on September 11",
]


def _parsed_fixture():
    parsed = [wt.parse_weather_event(name) for name in FIXTURE_EVENT_NAMES]
    assert all(parsed), "fixture titles must parse"
    return parsed


class TestDiscoveryHorizonHours(unittest.TestCase):
    def test_floor_is_48h_not_a_new_env(self):
        self.assertEqual(wt.DISCOVERY_HORIZON_FLOOR_HOURS, 48)
        self.assertNotIn("discovery_horizon", wt.CONFIG_SCHEMA)
        self.assertEqual(
            wt.CONFIG_SCHEMA["min_hours_to_resolve"]["env"],
            "SIMMER_WEATHER_MIN_HOURS_TO_RESOLVE",
        )

    def test_default_two_hours_still_uses_48h_floor(self):
        self.assertEqual(wt.discovery_horizon_hours(2), 48)

    def test_raised_24h_still_uses_48h_floor(self):
        self.assertEqual(wt.discovery_horizon_hours(24), 48)

    def test_raised_72h_tracks_the_hours_knob(self):
        self.assertEqual(wt.discovery_horizon_hours(72), 72)


class TestMorningMinHours24FindsFutureDay(unittest.TestCase):
    """Acceptance: morning wall-clock + MIN_HOURS=24 finds ≥1 future-day candidate."""

    def test_horizon_dates_include_plus_one_and_plus_two(self):
        dates = wt.discovery_event_dates(now=MORNING, min_hours=24)
        self.assertEqual(dates, ["2026-09-09", "2026-09-10", "2026-09-11"])

    def test_select_keeps_future_day_from_fixture(self):
        selected = wt.select_events_in_horizon(
            _parsed_fixture(), now=MORNING, min_hours=24
        )
        future = [info for info in selected if info["date"] > "2026-09-09"]
        self.assertGreaterEqual(len(future), 1)
        self.assertTrue(any(info["date"] == "2026-09-10" for info in future))
        self.assertTrue(any(info["date"] == "2026-09-11" for info in future))

    def test_same_day_events_stay_visible(self):
        """Hours safeguard still rejects them; discovery does not drop today."""
        selected = wt.select_events_in_horizon(
            _parsed_fixture(), now=MORNING, min_hours=24
        )
        self.assertTrue(any(info["date"] == "2026-09-09" for info in selected))

    def test_fetch_queries_add_dated_pages_past_today(self):
        queries = wt.discovery_fetch_queries(now=MORNING, min_hours=24)
        self.assertIsNone(queries[0])
        self.assertIn("september 10", queries)
        self.assertIn("september 11", queries)

    def test_import_terms_add_dated_location_searches(self):
        terms = wt.discovery_search_terms("NYC", now=MORNING, min_hours=24)
        self.assertIn("temperature new york", terms)
        self.assertTrue(any("september 10" in term for term in terms))
        self.assertTrue(any("september 11" in term for term in terms))


class TestQueryTokenMatchesParser(unittest.TestCase):
    def test_day_has_no_leading_zero(self):
        self.assertEqual(wt.event_date_query_token("2026-09-09"), "september 9")
        self.assertEqual(wt.event_date_query_token("2026-09-10"), "september 10")


if __name__ == "__main__":
    unittest.main()
