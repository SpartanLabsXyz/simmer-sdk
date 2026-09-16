"""SIM-5434: historical forecast archive builder.

Recorded Open-Meteo Previous Runs payload only. No network.
"""
import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock


_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_SKILL_DIR, "scripts")
_RECORDED = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "open_meteo_previous_day1_klga.json",
)
sys.path.insert(0, _SKILL_DIR)
sys.path.insert(0, _SCRIPTS)

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
sys.modules.setdefault("simmer_sdk", MagicMock())
sys.modules.setdefault("simmer_sdk.skill", _skill_mod)

import build_replay_forecast_archive as builder  # noqa: E402
import weather_trader as wt  # noqa: E402


def _recorded():
    with open(_RECORDED, encoding="utf-8") as fh:
        return json.load(fh)


def _fold(payload, start="2026-04-30", end="2026-04-30", station="KLGA"):
    return builder.daily_from_previous_runs(
        payload, station=station, start=start, end=end
    )


class TestDailyFromPreviousRuns(unittest.TestCase):
    def test_recorded_leads_fold_independently(self):
        """P1 (b): recorded payload with three leads folds each independently."""
        days = _fold(_recorded())
        day = days["2026-04-30"]
        self.assertEqual(day["high"], 56)
        self.assertEqual(day["low"], 48)
        self.assertEqual(day["leads"]["1"], {"high": 56, "low": 48})
        self.assertEqual(day["leads"]["2"], {"high": 66, "low": 58})
        self.assertEqual(day["leads"]["3"], {"high": 48, "low": 40})

    def test_null_hour_aborts_build(self):
        """P2: a null hour is not a daily high/low — fail closed."""
        recorded = _recorded()
        recorded["hourly"]["temperature_2m_previous_day1"][0] = None
        with self.assertRaises(builder.ArchiveBuildError) as ctx:
            _fold(recorded)
        self.assertIn("KLGA/2026-04-30/lead 1", str(ctx.exception))
        self.assertIn("null hourly", str(ctx.exception))

    def test_missing_18_00_aborts_build(self):
        """P2: 00:00–23:00 each once. Dropping 18:00 is not a valid day."""
        recorded = _recorded()
        times = recorded["hourly"]["time"]
        idx = next(i for i, t in enumerate(times) if "T18:00" in str(t))
        recorded["hourly"]["time"].pop(idx)
        for key in (
            "temperature_2m_previous_day1",
            "temperature_2m_previous_day2",
            "temperature_2m_previous_day3",
        ):
            recorded["hourly"][key].pop(idx)
        with self.assertRaises(builder.ArchiveBuildError) as ctx:
            _fold(recorded)
        self.assertIn("KLGA/2026-04-30/lead 1", str(ctx.exception))
        self.assertIn("18:00", str(ctx.exception))


class TestBuildArchiveRecorded(unittest.TestCase):
    def test_shape_meta_and_no_network(self):
        recorded = _recorded()
        calls = []

        def fetch(url):
            calls.append(url)
            self.assertIn("previous-runs-api.open-meteo.com/v1/forecast", url)
            self.assertIn("temperature_2m_previous_day1", url)
            self.assertIn("temperature_2m_previous_day2", url)
            self.assertIn("temperature_2m_previous_day3", url)
            return recorded

        spec = builder.StationSpec(
            "KLGA", 40.7769, -73.874, "America/New_York", "fahrenheit"
        )
        archive = builder.build_archive(
            "2026-04-30",
            "2026-04-30",
            [spec],
            fetch=fetch,
            fetched_at="2026-09-16T06:00:00Z",
        )
        self.assertEqual(
            archive["_meta"],
            {
                "source": "open-meteo-previous-runs",
                "fetched_at": "2026-09-16T06:00:00Z",
                "lead": "previous_day1",
                "utc_offset_seconds": {"KLGA": -14400},
            },
        )
        day = archive["KLGA"]["2026-04-30"]
        self.assertEqual(day["high"], 56)
        self.assertEqual(day["low"], 48)
        self.assertEqual(day["leads"]["1"], {"high": 56, "low": 48})
        self.assertEqual(len(calls), 1)

    def test_us_fahrenheit_intl_celsius_urls(self):
        us = builder.StationSpec("KLGA", 40.7769, -73.874, "auto", "fahrenheit")
        intl = builder.StationSpec(
            "LLBG", 32.0114, 34.8867, "Asia/Jerusalem", "celsius"
        )
        us_url = builder.previous_runs_url(us, "2026-04-30", "2026-04-30")
        intl_url = builder.previous_runs_url(intl, "2026-04-30", "2026-04-30")
        self.assertIn("temperature_unit=fahrenheit", us_url)
        self.assertIn("timezone=auto", us_url)
        self.assertIn("temperature_2m_previous_day1", us_url)
        self.assertIn("temperature_2m_previous_day2", us_url)
        self.assertIn("temperature_2m_previous_day3", us_url)
        self.assertIn("temperature_unit=celsius", intl_url)
        self.assertIn("timezone=Asia%2FJerusalem", intl_url)

    def test_fetch_is_injected_urlopen_never_called(self):
        def boom(_url):
            raise AssertionError("tests must not hit the network")

        with self.assertRaises(AssertionError):
            builder.build_archive(
                "2026-04-30",
                "2026-04-30",
                [builder.StationSpec("KLGA", 1, 2, "auto", "fahrenheit")],
                fetch=boom,
                fetched_at="t",
            )


class TestSkillStationCoverage(unittest.TestCase):
    def test_covers_noaa_and_international_tables(self):
        rows = builder.stations_from_skill_tables(
            wt.STATION_ID_TO_NOAA, wt.INTERNATIONAL_STATION_COORDS
        )
        ids = {row.station_id for row in rows}
        self.assertEqual(
            ids, set(wt.STATION_ID_TO_NOAA) | set(wt.INTERNATIONAL_STATION_COORDS)
        )
        loc_stations = {info["station"] for info in wt.LOCATIONS.values()}
        self.assertTrue(loc_stations <= set(wt.STATION_ID_TO_NOAA))
        for row in rows:
            if row.station_id in wt.STATION_ID_TO_NOAA:
                self.assertEqual(row.unit, "fahrenheit")
            else:
                self.assertEqual(row.unit, "celsius")


class TestRefuseSampleOverwrite(unittest.TestCase):
    def test_cli_refuses_sample_json(self):
        with self.assertRaises(builder.ArchiveBuildError) as ctx:
            builder.main(
                [
                    "--start",
                    "2026-04-30",
                    "--end",
                    "2026-04-30",
                    "--out",
                    str(os.path.join(_SKILL_DIR, "fixtures", "replay_forecasts.sample.json")),
                ]
            )
        self.assertIn("shape reference", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
