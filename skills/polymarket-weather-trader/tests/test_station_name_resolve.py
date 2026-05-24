"""
Unit tests for SIM-2428: station-name → ICAO fallback.

When Polymarket cites a station by name only (no Wunderground URL / ICAO),
the resolver should map the normalized name to a known ICAO so the market
isn't silently skipped.
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


class TestNormalizeStationName(unittest.TestCase):

    def test_strip_diacritics(self):
        # Esenboğa → esenboga (g̃ collapsed)
        self.assertEqual(wt._normalize_station_name("Esenboğa"), "esenboga")
        self.assertEqual(wt._normalize_station_name("Adolfo Suárez"), "adolfo suarez")

    def test_strip_intl_airport_suffix(self):
        self.assertEqual(wt._normalize_station_name("Heathrow Airport"), "heathrow")
        self.assertEqual(wt._normalize_station_name("Esenboğa Intl Airport"), "esenboga")
        self.assertEqual(
            wt._normalize_station_name("JFK International Airport"), "jfk"
        )

    def test_idempotent_repeated_suffix(self):
        # If someone writes "International Intl Airport Airport" (silly but defensible),
        # repeated stripping should still terminate cleanly.
        self.assertEqual(
            wt._normalize_station_name("Foo Intl International Airport"), "foo"
        )

    def test_empty_inputs(self):
        self.assertEqual(wt._normalize_station_name(""), "")
        self.assertEqual(wt._normalize_station_name(None), "")

    def test_case_insensitive(self):
        self.assertEqual(
            wt._normalize_station_name("HEATHROW AIRPORT"), "heathrow"
        )


class TestResolveStationIdFromName(unittest.TestCase):

    def test_resolve_ankara_with_diacritic(self):
        # The exact case from SIM-2428 repro
        self.assertEqual(
            wt.resolve_station_id_from_name("Esenboğa Intl Airport"), "LTAC"
        )

    def test_resolve_ankara_ascii_variant(self):
        self.assertEqual(
            wt.resolve_station_id_from_name("Esenboga Intl Airport"), "LTAC"
        )
        self.assertEqual(
            wt.resolve_station_id_from_name("Esenboga International Airport"),
            "LTAC",
        )

    def test_resolve_us_station_by_full_name(self):
        # Index includes US stations too — Polymarket might cite "Hartsfield-..."
        # without an ICAO for a US market.
        self.assertEqual(
            wt.resolve_station_id_from_name(
                "Hartsfield-Jackson Atlanta International Airport"
            ),
            "KATL",
        )
        self.assertEqual(
            wt.resolve_station_id_from_name("JFK International Airport"), "KJFK"
        )

    def test_resolve_intl_multi_airport_city(self):
        # Multi-airport intl cities (Milan, Tokyo) should distinguish per name
        self.assertEqual(
            wt.resolve_station_id_from_name("Milan Malpensa Airport"), "LIMC"
        )
        self.assertEqual(
            wt.resolve_station_id_from_name("Milan Linate Airport"), "LIML"
        )
        self.assertEqual(
            wt.resolve_station_id_from_name("Tokyo Haneda Airport"), "RJTT"
        )
        self.assertEqual(
            wt.resolve_station_id_from_name("Narita International Airport"),
            "RJAA",
        )

    def test_resolve_london_city_official_station(self):
        # Polymarket's published London weather station is EGLC, not Heathrow.
        self.assertEqual(
            wt.resolve_station_id_from_name("London City Airport"), "EGLC"
        )

    def test_london_city_has_openmeteo_coords(self):
        station = wt.INTERNATIONAL_STATION_COORDS.get("EGLC")
        self.assertIsNotNone(station)
        self.assertEqual(station["city"], "London")
        self.assertEqual(station["tz"], "Europe/London")

    def test_unknown_returns_none(self):
        self.assertIsNone(
            wt.resolve_station_id_from_name("Somewhere Nonexistent Airport")
        )

    def test_empty_returns_none(self):
        self.assertIsNone(wt.resolve_station_id_from_name(""))
        self.assertIsNone(wt.resolve_station_id_from_name(None))


class TestStationNameIndex(unittest.TestCase):

    def test_index_covers_all_stations(self):
        # Every entry in both coord tables should produce a name-index entry.
        expected_count = len(wt.STATION_ID_TO_NOAA) + len(
            wt.INTERNATIONAL_STATION_COORDS
        )
        self.assertEqual(len(wt._STATION_NAME_INDEX), expected_count)

    def test_index_values_are_valid_icaos(self):
        all_icaos = (
            set(wt.STATION_ID_TO_NOAA) | set(wt.INTERNATIONAL_STATION_COORDS)
        )
        for icao in wt._STATION_NAME_INDEX.values():
            self.assertIn(icao, all_icaos)


if __name__ == "__main__":
    unittest.main(verbosity=2)
