import importlib.util
import json
import sys
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

import pytest


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_DIR / "scripts" / "build_replay_forecast_archive.py"


class JsonResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def _load_weather_trader(name):
    spec = importlib.util.spec_from_file_location(name, SKILL_DIR / "weather_trader.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_builder(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_replay_forecast_loader_ignores_meta(tmp_path):
    wt = _load_weather_trader("_weather_replay_loader")
    archive = tmp_path / "replay_forecasts.json"
    archive.write_text(json.dumps({
        "_meta": {"stations": 1},
        "KLGA": {"2026-04-28": {"high": 64, "low": 52}},
    }))

    loaded = wt.load_replay_forecast_archive(archive)

    assert "_meta" not in loaded
    assert loaded == {"KLGA": {"2026-04-28": {"high": 64, "low": 52}}}


def test_parse_weather_event_uses_replay_now_for_year(monkeypatch):
    wt = _load_weather_trader("_weather_replay_parse_now")
    monkeypatch.setenv("SIMMER_REPLAY", "1")
    monkeypatch.setenv("SIMMER_REPLAY_NOW", "2026-04-28T00:00:00+00:00")

    parsed = wt.parse_weather_event(
        "Will the highest temperature in New York City be between 56-57°F on April 30?"
    )

    assert parsed["date"] == "2026-04-30"


def test_builder_uses_recorded_previous_runs_response(monkeypatch):
    builder = _load_builder("_weather_replay_builder")
    recorded = (SKILL_DIR / "tests" / "fixtures" / "openmeteo_previous_runs_klga.json").read_bytes()
    seen_urls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return recorded

    def fake_urlopen(url, timeout):
        seen_urls.append(url)
        return FakeResponse()

    monkeypatch.setattr(builder, "_station_specs", lambda stations="all": {
        "KLGA": {
            "lat": 40.7769,
            "lon": -73.8740,
            "name": "LaGuardia Airport",
            "unit": "fahrenheit",
            "table": "STATION_ID_TO_NOAA",
        }
    })

    archive = builder.build_archive(
        builder._parse_day("2026-04-28"),
        builder._parse_day("2026-04-30"),
        opener=fake_urlopen,
        sleep=lambda _seconds: None,
    )

    assert archive["KLGA"]["2026-04-28"]["high"] == 64
    assert archive["KLGA"]["2026-04-28"]["low"] == 52
    assert archive["KLGA"]["2026-04-29"]["leads"]["2"] == {"high": 69, "low": 54}
    assert archive["KLGA"]["2026-04-30"]["leads"]["1"] == {"high": None, "low": 58}
    assert archive["_meta"]["stations"] == 1
    assert archive["_meta"]["station_units"] == {"KLGA": "fahrenheit"}
    assert archive["_meta"]["utc_offset_seconds"] == {"KLGA": -14400}
    parsed = urlparse(seen_urls[0])
    query = parse_qs(parsed.query)
    assert parsed.netloc == "previous-runs-api.open-meteo.com"
    assert query["hourly"] == ["temperature_2m_previous_day1,temperature_2m_previous_day2,temperature_2m_previous_day3"]
    assert query["temperature_unit"] == ["fahrenheit"]


def test_builder_retries_5xx_then_succeeds(monkeypatch):
    builder = _load_builder("_weather_replay_builder_retry")
    calls = 0
    payload = {
        "utc_offset_seconds": 0,
        "daily": {
            "time": ["2026-02-05"],
            "previous_day1_temperature_2m_max": [52],
            "previous_day1_temperature_2m_min": [41],
        },
    }

    def fake_urlopen(url, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError(url, 502, "Bad Gateway", hdrs=None, fp=BytesIO())
        return JsonResponse(payload)

    monkeypatch.setattr(builder, "_station_specs", lambda stations="all": {
        "KLGA": {"lat": 40.7769, "lon": -73.8740, "unit": "fahrenheit", "table": "STATION_ID_TO_NOAA"}
    })

    archive = builder.build_archive(
        builder._parse_day("2026-02-05"),
        builder._parse_day("2026-02-05"),
        stations="us",
        opener=fake_urlopen,
        sleep=lambda _seconds: None,
    )

    assert calls == 2
    assert archive["KLGA"]["2026-02-05"]["high"] == 52


def test_builder_400_aborts_without_retry(monkeypatch):
    builder = _load_builder("_weather_replay_builder_400")
    calls = 0

    def fake_urlopen(url, timeout):
        nonlocal calls
        calls += 1
        raise HTTPError(url, 400, "Bad Request", hdrs=None, fp=BytesIO())

    monkeypatch.setattr(builder, "_station_specs", lambda stations="all": {
        "KLGA": {"lat": 40.7769, "lon": -73.8740, "unit": "fahrenheit", "table": "STATION_ID_TO_NOAA"}
    })

    with pytest.raises(HTTPError):
        builder.build_archive(
            builder._parse_day("2026-02-05"),
            builder._parse_day("2026-02-05"),
            stations="us",
            opener=fake_urlopen,
            sleep=lambda _seconds: None,
        )
    assert calls == 1


def test_builder_chunks_and_merges_matching_offsets(monkeypatch):
    builder = _load_builder("_weather_replay_builder_chunks")
    seen_ranges = []

    def fake_urlopen(url, timeout):
        query = parse_qs(urlparse(url).query)
        start = builder._parse_day(query["start_date"][0])
        end = builder._parse_day(query["end_date"][0])
        seen_ranges.append((start.isoformat(), end.isoformat()))
        days = []
        highs = []
        lows = []
        current = start
        while current <= end:
            days.append(current.isoformat())
            highs.append(70 + len(days))
            lows.append(50 + len(days))
            current = current.fromordinal(current.toordinal() + 1)
        return JsonResponse({
            "utc_offset_seconds": -18000,
            "daily": {
                "time": days,
                "previous_day1_temperature_2m_max": highs,
                "previous_day1_temperature_2m_min": lows,
            },
        })

    monkeypatch.setattr(builder, "_station_specs", lambda stations="all": {
        "KLGA": {"lat": 40.7769, "lon": -73.8740, "unit": "fahrenheit", "table": "STATION_ID_TO_NOAA"}
    })

    archive = builder.build_archive(
        builder._parse_day("2026-02-05"),
        builder._parse_day("2026-02-25"),
        stations="us",
        opener=fake_urlopen,
        sleep=lambda _seconds: None,
    )

    assert seen_ranges == [
        ("2026-02-05", "2026-02-14"),
        ("2026-02-15", "2026-02-24"),
        ("2026-02-25", "2026-02-25"),
    ]
    assert len(archive["KLGA"]) == 21
    assert archive["KLGA"]["2026-02-25"]["high"] == 71


def test_builder_aborts_when_chunk_offsets_differ(monkeypatch):
    builder = _load_builder("_weather_replay_builder_offset_guard")
    offsets = iter([-18000, -14400])

    def fake_urlopen(url, timeout):
        query = parse_qs(urlparse(url).query)
        return JsonResponse({
            "utc_offset_seconds": next(offsets),
            "daily": {
                "time": [query["start_date"][0]],
                "previous_day1_temperature_2m_max": [70],
                "previous_day1_temperature_2m_min": [50],
            },
        })

    monkeypatch.setattr(builder, "_station_specs", lambda stations="all": {
        "KLGA": {"lat": 40.7769, "lon": -73.8740, "unit": "fahrenheit", "table": "STATION_ID_TO_NOAA"}
    })

    with pytest.raises(ValueError, match="utc_offset_seconds changed"):
        builder.build_archive(
            builder._parse_day("2026-02-05"),
            builder._parse_day("2026-02-15"),
            stations="us",
            opener=fake_urlopen,
            sleep=lambda _seconds: None,
        )


def test_station_filter_limits_us_to_configured_resolution_stations():
    builder = _load_builder("_weather_replay_builder_station_filter")

    us_specs = builder._station_specs("us")
    intl_specs = builder._station_specs("intl")
    all_specs = builder._station_specs("all")

    assert len(us_specs) == 8
    assert set(us_specs) == {"KATL", "KAUS", "KBKF", "KHOU", "KLGA", "KMIA", "KORD", "KSEA"}
    assert not set(us_specs) & set(intl_specs)
    assert set(all_specs) == set(us_specs) | set(intl_specs)
