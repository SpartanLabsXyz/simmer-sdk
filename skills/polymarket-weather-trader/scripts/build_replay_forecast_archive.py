#!/usr/bin/env python3
"""Build a historical forecast archive for weather replay (SIM-5434).

Open-Meteo Previous Runs has no daily ``previous_day1_temperature_2m_max/min``.
Those names 400. This script hits the Previous Runs forecast endpoint per
station coord, reads hourly ``temperature_2m_previous_day1``, and folds each
local day into {high, low}. Tick D therefore sees the D-1 forecast — no
look-ahead, no live NOAA.

Shape matches the SIM-5429 loader:

    {_meta, station_id: {YYYY-MM-DD: {high, low}}}

US stations (LOCATIONS / STATION_ID_TO_NOAA) are °F. International stations
(INTERNATIONAL_STATION_COORDS) are °C. Same units as ``_station_forecast``.

Usage (from repo root):

    python skills/polymarket-weather-trader/scripts/build_replay_forecast_archive.py \\
      --start YYYY-MM-DD --end YYYY-MM-DD --out fixtures/replay_forecasts.json
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PREVIOUS_RUNS_FORECAST = "https://previous-runs-api.open-meteo.com/v1/forecast"
HOURLY_VAR = "temperature_2m_previous_day1"
LEAD = "previous_day1"
SOURCE = "open-meteo-previous-runs"
META_KEY = "_meta"
SAMPLE_NAME = "replay_forecasts.sample.json"


class ArchiveBuildError(RuntimeError):
    """Previous Runs fetch or CLI input failed. Fail closed."""


@dataclass(frozen=True)
class StationSpec:
    station_id: str
    lat: float
    lon: float
    timezone: str
    unit: str  # "fahrenheit" | "celsius" — Open-Meteo temperature_unit


def stations_from_skill_tables(us_table: dict, intl_table: dict) -> list[StationSpec]:
    """One row per NOAA + international station. Reuses the live tables."""
    rows = []
    for icao, info in us_table.items():
        rows.append(
            StationSpec(icao, info["lat"], info["lon"], "auto", "fahrenheit")
        )
    for icao, info in intl_table.items():
        rows.append(
            StationSpec(icao, info["lat"], info["lon"], info["tz"], "celsius")
        )
    return rows


def previous_runs_url(spec: StationSpec, start: str, end: str) -> str:
    query = urlencode(
        {
            "latitude": spec.lat,
            "longitude": spec.lon,
            "hourly": HOURLY_VAR,
            "start_date": start,
            "end_date": end,
            "timezone": spec.timezone,
            "temperature_unit": spec.unit,
        }
    )
    return f"{PREVIOUS_RUNS_FORECAST}?{query}"


def daily_high_low_from_hourly(payload: dict) -> dict:
    """Fold hourly previous_day1 temps into loader {date: {high, low}}."""
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    temps = hourly.get(HOURLY_VAR) or []
    by_day: dict[str, list[float]] = {}
    for stamp, value in zip(times, temps):
        if value is None or not stamp:
            continue
        by_day.setdefault(str(stamp)[:10], []).append(float(value))
    return {
        day: {"high": round(max(vals)), "low": round(min(vals))}
        for day, vals in by_day.items()
    }


def fetch_previous_runs(url: str, *, opener=urlopen) -> dict:
    req = Request(url, headers={"User-Agent": "SimmerWeatherSkill/replay-archive"})
    try:
        with opener(req, timeout=30) as resp:
            raw = resp.read().decode()
    except HTTPError as exc:
        raise ArchiveBuildError(f"Open-Meteo HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise ArchiveBuildError(f"Open-Meteo request failed: {exc.reason}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArchiveBuildError("Open-Meteo returned non-JSON") from exc
    if data.get("error"):
        raise ArchiveBuildError(data.get("reason") or "Open-Meteo error")
    return data


def build_archive(
    start: str,
    end: str,
    stations: list[StationSpec],
    *,
    fetch,
    fetched_at: str,
) -> dict:
    archive = {
        META_KEY: {
            "source": SOURCE,
            "fetched_at": fetched_at,
            "lead": LEAD,
        }
    }
    for spec in stations:
        archive[spec.station_id] = daily_high_low_from_hourly(
            fetch(previous_runs_url(spec, start, end))
        )
    return archive


def parse_iso_date(label: str, value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ArchiveBuildError(f"--{label} must be YYYY-MM-DD") from exc


def _import_weather_trader():
    skill_dir = str(Path(__file__).resolve().parents[1])
    if skill_dir not in sys.path:
        sys.path.insert(0, skill_dir)
    import weather_trader as wt  # noqa: E402

    return wt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="inclusive YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="inclusive YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="JSON output path")
    args = parser.parse_args(argv)

    start = parse_iso_date("start", args.start)
    end = parse_iso_date("end", args.end)
    if start > end:
        raise ArchiveBuildError("--start must be on or before --end")

    out = Path(args.out)
    if out.name == SAMPLE_NAME:
        raise ArchiveBuildError(
            f"refusing to overwrite {SAMPLE_NAME} (shape reference only)"
        )

    wt = _import_weather_trader()
    missing = [
        info["station"]
        for info in wt.LOCATIONS.values()
        if info.get("station") not in wt.STATION_ID_TO_NOAA
    ]
    if missing:
        raise ArchiveBuildError(
            f"LOCATIONS stations missing from STATION_ID_TO_NOAA: {missing}"
        )
    stations = stations_from_skill_tables(
        wt.STATION_ID_TO_NOAA, wt.INTERNATIONAL_STATION_COORDS
    )
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    archive = build_archive(
        args.start,
        args.end,
        stations,
        fetch=fetch_previous_runs,
        fetched_at=fetched_at,
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(archive, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    n = len(archive) - 1  # drop _meta
    print(f"wrote {out} stations={n} {args.start}–{args.end}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ArchiveBuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
