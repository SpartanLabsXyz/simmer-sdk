"""SIM-5434: historical forecast archive builder.

Recorded Open-Meteo Previous Runs payload only. No network.
"""
import json
import os
import sys
import types
import unittest
from io import BytesIO
from unittest.mock import MagicMock
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse


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


class JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def _payload_for_dates(start, end, *, offset=-18000, timezone="America/New_York"):
    dates = builder.requested_dates(start, end)
    times = []
    lead_values = {1: [], 2: [], 3: []}
    for day_index, day in enumerate(dates):
        for hour in range(24):
            times.append(f"{day}T{hour:02d}:00")
            lead_values[1].append(50 + day_index + hour / 100)
            lead_values[2].append(60 + day_index + hour / 100)
            lead_values[3].append(40 + day_index + hour / 100)
    return {
        "utc_offset_seconds": offset,
        "timezone": timezone,
        "hourly": {
            "time": times,
            "temperature_2m_previous_day1": lead_values[1],
            "temperature_2m_previous_day2": lead_values[2],
            "temperature_2m_previous_day3": lead_values[3],
        },
    }


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

    def test_build_archive_chunks_and_merges_matching_offsets(self):
        seen_ranges = []

        def fetch(url):
            query = parse_qs(urlparse(url).query)
            start = query["start_date"][0]
            end = query["end_date"][0]
            seen_ranges.append((start, end))
            return _payload_for_dates(start, end, offset=-18000)

        spec = builder.StationSpec(
            "KLGA", 40.7769, -73.874, "America/New_York", "fahrenheit"
        )
        archive = builder.build_archive(
            "2026-02-05",
            "2026-02-25",
            [spec],
            fetch=fetch,
            fetched_at="2026-09-16T06:00:00Z",
        )

        self.assertEqual(
            seen_ranges,
            [
                ("2026-02-05", "2026-02-14"),
                ("2026-02-15", "2026-02-24"),
                ("2026-02-25", "2026-02-25"),
            ],
        )
        self.assertEqual(len(archive["KLGA"]), 21)
        self.assertEqual(archive["KLGA"]["2026-02-25"]["high"], 50)
        self.assertEqual(archive["_meta"]["utc_offset_seconds"], {"KLGA": -18000})

    def test_build_archive_aborts_when_chunk_offsets_differ(self):
        offsets = iter([-18000, -14400])

        def fetch(url):
            query = parse_qs(urlparse(url).query)
            return _payload_for_dates(
                query["start_date"][0],
                query["end_date"][0],
                offset=next(offsets),
            )

        spec = builder.StationSpec(
            "KLGA", 40.7769, -73.874, "America/New_York", "fahrenheit"
        )
        with self.assertRaises(builder.ArchiveBuildError) as ctx:
            builder.build_archive(
                "2026-02-05",
                "2026-02-15",
                [spec],
                fetch=fetch,
                fetched_at="2026-09-16T06:00:00Z",
            )
        self.assertIn("utc_offset_seconds changed", str(ctx.exception))

    def test_build_archive_retries_502_then_succeeds(self):
        calls = 0

        def fake_urlopen(req, timeout):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise HTTPError(req.full_url, 502, "Bad Gateway", hdrs=None, fp=BytesIO())
            return JsonResponse(_payload_for_dates("2026-02-05", "2026-02-05"))

        spec = builder.StationSpec(
            "KLGA", 40.7769, -73.874, "America/New_York", "fahrenheit"
        )
        archive = builder.build_archive(
            "2026-02-05",
            "2026-02-05",
            [spec],
            fetch=lambda url: builder.fetch_previous_runs(
                url, opener=fake_urlopen, sleep=lambda _seconds: None
            ),
            fetched_at="2026-09-16T06:00:00Z",
        )

        self.assertEqual(calls, 2)
        self.assertEqual(archive["KLGA"]["2026-02-05"]["high"], 50)

    def test_build_archive_400_aborts_without_retry(self):
        calls = 0

        def fake_urlopen(req, timeout):
            nonlocal calls
            calls += 1
            raise HTTPError(req.full_url, 400, "Bad Request", hdrs=None, fp=BytesIO())

        spec = builder.StationSpec(
            "KLGA", 40.7769, -73.874, "America/New_York", "fahrenheit"
        )
        with self.assertRaises(builder.ArchiveBuildError) as ctx:
            builder.build_archive(
                "2026-02-05",
                "2026-02-05",
                [spec],
                fetch=lambda url: builder.fetch_previous_runs(
                    url, opener=fake_urlopen, sleep=lambda _seconds: None
                ),
                fetched_at="2026-09-16T06:00:00Z",
            )
        self.assertIn("HTTP 400", str(ctx.exception))
        self.assertEqual(calls, 1)

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


class TestDstWindowGuard(unittest.TestCase):
    """Codex pass-3 P2: labels use the request-time offset, so a window that
    crosses a DST transition folds the wrong local hours. Refuse it."""

    def test_window_crossing_us_dst_aborts(self):
        recorded = _recorded()  # timezone America/New_York
        with self.assertRaises(builder.ArchiveBuildError) as ctx:
            builder.reject_dst_crossing(
                recorded, station="KLGA", start="2026-03-07", end="2026-03-08"
            )
        self.assertIn("DST", str(ctx.exception))

    def test_window_inside_one_offset_passes(self):
        recorded = _recorded()
        builder.reject_dst_crossing(
            recorded, station="KLGA", start="2026-04-28", end="2026-05-05"
        )

    def test_build_archive_runs_guard(self):
        recorded = _recorded()
        spec = builder.StationSpec(
            "KLGA", 40.7769, -73.874, "America/New_York", "fahrenheit"
        )
        with self.assertRaises(builder.ArchiveBuildError):
            builder.build_archive(
                "2026-11-01", "2026-11-01", [spec],
                fetch=lambda _url: recorded, fetched_at="t",
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

    def test_station_filter_limits_us_to_configured_resolution_stations(self):
        us_rows = builder.stations_from_skill_tables(
            wt.STATION_ID_TO_NOAA,
            wt.INTERNATIONAL_STATION_COORDS,
            stations="us",
            locations=wt.LOCATIONS,
        )
        intl_rows = builder.stations_from_skill_tables(
            wt.STATION_ID_TO_NOAA,
            wt.INTERNATIONAL_STATION_COORDS,
            stations="intl",
            locations=wt.LOCATIONS,
        )
        all_rows = builder.stations_from_skill_tables(
            wt.STATION_ID_TO_NOAA,
            wt.INTERNATIONAL_STATION_COORDS,
            stations="all",
            locations=wt.LOCATIONS,
        )
        us_ids = {row.station_id for row in us_rows}
        intl_ids = {row.station_id for row in intl_rows}

        self.assertEqual(len(us_ids), 8)
        self.assertEqual(
            us_ids, {"KATL", "KAUS", "KBKF", "KHOU", "KLGA", "KMIA", "KORD", "KSEA"}
        )
        self.assertFalse(us_ids & intl_ids)
        self.assertEqual({row.station_id for row in all_rows}, us_ids | intl_ids)


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
