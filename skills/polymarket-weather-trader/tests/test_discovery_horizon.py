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
    # Pin year/rollover to MORNING. parse_weather_event uses datetime.now()
    # and adds +1 year after 7 days — after ~2026-09-16 an unpinned parse
    # would date these titles 2027-09-* and the morning select would keep 0.
    parsed = [wt.parse_weather_event(name, now=MORNING) for name in FIXTURE_EVENT_NAMES]
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

    def test_fixture_year_stays_2026_after_seven_day_rollover(self):
        """Unpinned parse after ~2026-09-16 rolls Sept 9 → 2027; pin keeps 2026."""
        # Sept 11 is still inside the 7-day window on the 16th; use the 19th
        # so every fixture title would roll without the MORNING pin.
        rolled = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
        unpinned = [wt.parse_weather_event(name, now=rolled) for name in FIXTURE_EVENT_NAMES]
        self.assertTrue(all(info["date"].startswith("2027-") for info in unpinned))
        pinned = _parsed_fixture()
        self.assertTrue(all(info["date"].startswith("2026-") for info in pinned))
        selected = wt.select_events_in_horizon(pinned, now=MORNING, min_hours=24)
        self.assertGreaterEqual(
            len([info for info in selected if info["date"] > "2026-09-09"]), 1
        )

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


class TestStarvePathIssuesDatedQueries(unittest.TestCase):
    """Production fetch/import must issue the +1 day q= — helpers alone are not enough."""

    def test_fetch_issues_september_10_query(self):
        calls = []

        def _request(method, path, params=None):
            calls.append(params or {})
            return {"markets": []}

        mock_client = MagicMock()
        mock_client._request.side_effect = _request
        with patch.object(wt, "get_client", return_value=mock_client):
            wt.fetch_weather_markets(now=MORNING, min_hours=24)

        qs = [c.get("q") for c in calls]
        self.assertIn("september 10", qs)
        self.assertIn("september 11", qs)
        self.assertTrue(any(c.get("tags") == "weather" and "q" not in c for c in calls))
        mock_client._request.assert_called()

    def test_import_issues_dated_search(self):
        qs = []

        def list_importable_markets(q=None, **kwargs):
            qs.append(q)
            return []

        mock_client = MagicMock()
        mock_client.list_importable_markets.side_effect = list_importable_markets
        with patch.object(wt, "get_client", return_value=mock_client):
            wt.discover_and_import_weather_markets(
                log=lambda *a, **k: None, now=MORNING, min_hours=24
            )

        self.assertTrue(any(q and "september 10" in q for q in qs))
        self.assertTrue(any(q and "september 11" in q for q in qs))
        mock_client.list_importable_markets.assert_called()


class TestQueryTokenMatchesParser(unittest.TestCase):
    def test_day_has_no_leading_zero(self):
        self.assertEqual(wt.event_date_query_token("2026-09-09"), "september 9")
        self.assertEqual(wt.event_date_query_token("2026-09-10"), "september 10")


if __name__ == "__main__":
    unittest.main()
