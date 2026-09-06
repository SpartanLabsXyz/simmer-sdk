"""
Regression tests for the 2026-08-22 Polymarket resolution_criteria reword.

Polymarket changed every weather-temperature market's criteria on the same
day: the Wunderground URL was dropped (resolution source moved to NOAA) and
the station phrase gained an agency clause. Both parser patterns died at
once and the skill silently stopped entering — no error, no failed trade,
no retry, so nothing downstream noticed for two weeks.

These tests pin the new wording, the old wording, the reason split, and the
coverage guard that makes the next reword visible on the first run.

Criteria strings below are copied verbatim from the live corpus on 2026-09-06.
"""

import os
import sys
import types
import unittest
from unittest.mock import MagicMock


_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

_mock_cfg = {
    "entry_threshold": 0.15, "exit_threshold": 0.45, "max_position_usd": 2.0,
    "sizing_pct": 0.05, "max_trades_per_run": 5, "locations": "NYC",
    "binary_only": False, "slippage_max": 0.15, "min_liquidity": 0.0,
    "order_type": "GTC", "vol_targeting": False, "target_vol": 0.20,
    "vol_max_leverage": 2.0, "vol_min_allocation": 0.2, "vol_span": 10,
    "require_source_agreement": False, "canary_on_adjacent": True,
    "max_canary_usd": 2.0, "max_source_spread_f": 2.0,
}

_skill_mod = types.ModuleType("simmer_sdk.skill")
_skill_mod.load_config = lambda schema, file, slug=None: _mock_cfg.copy()
_skill_mod.update_config = lambda updates, file, slug=None: None
_skill_mod.get_config_path = lambda file: "/tmp/config.json"
sys.modules["simmer_sdk"] = MagicMock()
sys.modules["simmer_sdk.skill"] = _skill_mod

import weather_trader as wt  # noqa: E402


# Verbatim from markets.resolution_criteria, 2026-09-06.
NEW_FORM_NYC = (
    "This market will resolve to the temperature range that contains the "
    "highest temperature recorded by NOAA at the LaGuardia Airport Station "
    "in degrees Fahrenheit on 6 Sep '26.\n\nThe resolution source for this "
    "market will be information from NOAA, specifically the highest reading "
    'under the "Temp" column.'
)

# The pre-2026-08-22 shape, still live on a residual ~84 markets.
OLD_FORM_TAIPEI = (
    "This market will resolve to the temperature range that contains the "
    "highest temperature recorded at the Taipei Songshan Airport Station in "
    "degrees Celsius on 2 May '26.\n\nThe resolution source for this market "
    "will be information from Wunderground, available here: "
    "https://www.wunderground.com/history/daily/tw/taipei/RCSS."
)


class TestNewFormWording(unittest.TestCase):
    """The reword must not skip the market."""

    def test_agency_clause_does_not_break_the_parse(self):
        parsed = wt.parse_resolution_station(NEW_FORM_NYC)
        self.assertIsNotNone(
            parsed, "the 'recorded by NOAA at the ...' wording must parse"
        )
        self.assertEqual(parsed["station_name"], "LaGuardia Airport")

    def test_nyc_routes_to_klga(self):
        parsed = wt.parse_resolution_station(NEW_FORM_NYC)
        self.assertEqual(
            wt.resolve_station_id_from_name(parsed["station_name"]), "KLGA"
        )

    def test_a_future_agency_reword_still_parses(self):
        # The clause is generic on purpose: the point of the fix is that the
        # next rename does not cost us another two silent weeks.
        for agency in ("NOAA", "NWS", "the National Weather Service"):
            criteria = NEW_FORM_NYC.replace("by NOAA", f"by {agency}")
            with self.subTest(agency=agency):
                parsed = wt.parse_resolution_station(criteria)
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed["station_name"], "LaGuardia Airport")


class TestOldFormStillWorks(unittest.TestCase):
    """~84 markets still carry the pre-reword shape."""

    def test_wunderground_url_still_yields_the_icao(self):
        parsed = wt.parse_resolution_station(OLD_FORM_TAIPEI)
        self.assertEqual(parsed["station_id"], "RCSS")
        self.assertEqual(parsed["station_name"], "Taipei Songshan Airport")


class TestAdvertisedLocationsRoute(unittest.TestCase):
    """Every location the skill advertises must route under the new wording.

    clawhub.json promises six US cities; parse_weather_event() adds the
    international aliases. A location we advertise but cannot route is a
    silent no-op for the agent that configured it.
    """

    # station name Polymarket cites today -> ICAO we must land on
    CASES = {
        "LaGuardia Airport": "KLGA",
        "Chicago O'Hare Intl Airport": "KORD",
        "Seattle-Tacoma International Airport": "KSEA",
        "Hartsfield-Jackson International Airport": "KATL",
        "Dallas Love Field": "KDAL",
        "Miami Intl Airport": "KMIA",
        "Munich Airport": "EDDM",
        "London City Airport": "EGLC",
        "Tokyo Haneda Airport": "RJTT",
        "Incheon Intl Airport": "RKSI",
        "Esenboğa Intl Airport": "LTAC",
        "Chaudhary Charan Singh Intl Airport": "VILK",
        "Wellington Intl Airport": "NZWN",
        "Adolfo Suárez Madrid-Barajas Airport": "LEMD",
        "Malpensa Intl Airport": "LIMC",
        "Amsterdam Airport Schiphol": "EHAM",
    }

    def test_each_advertised_station_routes(self):
        for cited_name, expected_icao in self.CASES.items():
            criteria = NEW_FORM_NYC.replace("LaGuardia Airport", cited_name)
            with self.subTest(station=cited_name):
                parsed = wt.parse_resolution_station(criteria)
                self.assertIsNotNone(parsed, f"{cited_name} failed to parse")
                icao = parsed["station_id"] or wt.resolve_station_id_from_name(
                    parsed["station_name"]
                )
                self.assertEqual(icao, expected_icao)
                self.assertTrue(
                    icao in wt.STATION_ID_TO_NOAA
                    or icao in wt.INTERNATIONAL_STATION_COORDS,
                    f"{icao} has no coordinates, so the market would be skipped",
                )

    def test_aliases_only_point_at_stations_we_have_coords_for(self):
        # Guard against the KDFW/KDAL failure mode: an alias that routes a
        # market to an airport we cannot forecast is worse than no alias.
        for name, icao in wt._STATION_NAME_ALIASES.items():
            with self.subTest(alias=name):
                self.assertTrue(
                    icao in wt.STATION_ID_TO_NOAA
                    or icao in wt.INTERNATIONAL_STATION_COORDS,
                    f"alias {name!r} -> {icao} has no coordinates",
                )


class TestSkipReasonSplit(unittest.TestCase):
    """Missing criteria and unreadable criteria are different failures."""

    def test_empty_criteria_reports_missing(self):
        for empty in ("", None, 123):
            with self.subTest(value=empty):
                result = wt.parse_resolution_station_result(empty)
                self.assertIsNone(result["station"])
                self.assertEqual(result["reason"], wt.SKIP_MISSING_CRITERIA)

    def test_present_but_unreadable_reports_unparseable(self):
        result = wt.parse_resolution_station_result(
            "This market resolves on measurable precipitation per the NWS "
            "Daily Climate Report (CLI) for the city's station."
        )
        self.assertIsNone(result["station"])
        self.assertEqual(result["reason"], wt.SKIP_UNPARSEABLE_CRITERIA)

    def test_a_good_parse_carries_no_reason(self):
        self.assertIsNone(wt.parse_resolution_station_result(NEW_FORM_NYC)["reason"])


class TestParseCoverageGuard(unittest.TestCase):
    """The guard must actually discriminate — silent then, loud now."""

    def _capture(self, ok, unreadable):
        lines = []
        wt._report_parse_coverage(ok, unreadable, lambda m, **kw: lines.append(m))
        return "\n".join(lines)

    def test_the_warning_survives_quiet_mode(self):
        # A guard that --quiet swallows is not a guard.
        seen = {}
        wt._report_parse_coverage(0, 40, lambda m, **kw: seen.update(kw))
        self.assertTrue(seen.get("force"), "coverage warning must pass force=True")

    def test_fires_when_the_parser_falls_behind(self):
        # The Aug-22 shape: criteria everywhere, readable nowhere.
        out = self._capture(ok=0, unreadable=40)
        self.assertIn("0/40", out)
        self.assertIn("failing to look", out)

    def test_silent_on_a_healthy_run(self):
        self.assertEqual(self._capture(ok=40, unreadable=0), "")

    def test_silent_on_ordinary_per_market_noise(self):
        self.assertEqual(self._capture(ok=38, unreadable=2), "")

    def test_silent_when_no_events_carried_criteria(self):
        # Nothing to conclude from an empty scan; don't cry wolf.
        self.assertEqual(self._capture(ok=0, unreadable=0), "")

    def test_fires_at_the_threshold_boundary(self):
        self.assertEqual(self._capture(ok=5, unreadable=5), "")      # 50% == floor
        self.assertIn("4/10", self._capture(ok=4, unreadable=6))     # 40% < floor


if __name__ == "__main__":
    unittest.main()
