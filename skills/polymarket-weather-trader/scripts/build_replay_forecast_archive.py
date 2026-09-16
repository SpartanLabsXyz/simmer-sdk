#!/usr/bin/env python3
"""Build a historical forecast archive for weather replay (SIM-5434).

Open-Meteo Previous Runs has no daily ``previous_day1_temperature_2m_max/min``.
Those names 400. This script hits the Previous Runs forecast endpoint per
station coord and reads hourly ``temperature_2m_previous_day{1,2,3}``. Each
local day folds into {high, low, leads}. Top-level high/low is lead 1 so the
loader shape stays ``{high, low}``.

Lead N is the forecast issued N days before each valid hour. The loader picks
the smallest N in 1–3 such that every hourly issuance for the event day
precedes the tick (station-local 23:59:59 → UTC, using
``_meta["utc_offset_seconds"]``).

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PREVIOUS_RUNS_FORECAST = "https://previous-runs-api.open-meteo.com/v1/forecast"
HOURLY_BY_LEAD = {
    1: "temperature_2m_previous_day1",
    2: "temperature_2m_previous_day2",
    3: "temperature_2m_previous_day3",
}
REQUIRED_HOUR_LABELS = tuple(f"{h:02d}:00" for h in range(24))
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
            "hourly": ",".join(HOURLY_BY_LEAD.values()),
            "start_date": start,
            "end_date": end,
            "timezone": spec.timezone,
            "temperature_unit": spec.unit,
        }
    )
    return f"{PREVIOUS_RUNS_FORECAST}?{query}"


def requested_dates(start: str, end: str) -> list[str]:
    cur = datetime.strptime(start, "%Y-%m-%d").date()
    last = datetime.strptime(end, "%Y-%m-%d").date()
    out = []
    while cur <= last:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _hour_label(stamp, *, station: str, lead: int) -> tuple[str, str]:
    text = str(stamp)
    if "T" not in text or len(text) < 16:
        raise ArchiveBuildError(
            f"{station}/lead {lead}: bad hourly timestamp {stamp!r}"
        )
    return text[:10], text.split("T", 1)[1][:5]


def utc_offset_from_payload(payload: dict, *, station: str) -> int:
    val = payload.get("utc_offset_seconds") if isinstance(payload, dict) else None
    if not isinstance(val, (int, float)):
        raise ArchiveBuildError(f"{station}: missing utc_offset_seconds")
    return int(val)


def _hours_by_day(times, temps, *, station: str, dates: list[str], lead: int) -> dict:
    """Fold one lead's hourly series. Incomplete days abort the build."""
    if not isinstance(times, list) or not isinstance(temps, list):
        raise ArchiveBuildError(
            f"{station}/lead {lead}: hourly arrays must be lists"
        )
    if len(times) != len(temps):
        raise ArchiveBuildError(
            f"{station}/lead {lead}: hourly array length mismatch "
            f"time={len(times)} temps={len(temps)}"
        )
    by_day: dict[str, dict[str, float]] = {d: {} for d in dates}
    for stamp, value in zip(times, temps):
        if not stamp:
            raise ArchiveBuildError(f"{station}/lead {lead}: missing hourly timestamp")
        day, hour = _hour_label(stamp, station=station, lead=lead)
        if day not in by_day:
            continue
        if value is None:
            raise ArchiveBuildError(
                f"{station}/{day}/lead {lead}: null hourly value"
            )
        if hour in by_day[day]:
            raise ArchiveBuildError(
                f"{station}/{day}/lead {lead}: duplicate hour {hour}"
            )
        by_day[day][hour] = float(value)
    out = {}
    for day in dates:
        labels = by_day[day]
        missing = [h for h in REQUIRED_HOUR_LABELS if h not in labels]
        extra = [h for h in labels if h not in REQUIRED_HOUR_LABELS]
        if missing or extra:
            detail = f"missing {missing[0]}" if missing else f"extra {extra[0]}"
            raise ArchiveBuildError(
                f"{station}/{day}/lead {lead}: expected hours 00:00–23:00 "
                f"each once ({detail})"
            )
        vals = [labels[h] for h in REQUIRED_HOUR_LABELS]
        out[day] = {"high": round(max(vals)), "low": round(min(vals))}
    return out


def daily_from_previous_runs(payload: dict, *, station: str, start: str, end: str) -> dict:
    """Fold leads 1–3 independently. Top-level high/low is lead 1."""
    dates = requested_dates(start, end)
    hourly = payload.get("hourly") if isinstance(payload, dict) else None
    if not isinstance(hourly, dict):
        raise ArchiveBuildError(f"{station}: missing hourly object")
    times = hourly.get("time")
    if not isinstance(times, list):
        raise ArchiveBuildError(f"{station}: missing hourly.time")
    by_lead = {}
    for lead, var in HOURLY_BY_LEAD.items():
        temps = hourly.get(var)
        if not isinstance(temps, list):
            raise ArchiveBuildError(f"{station}/lead {lead}: missing {var}")
        by_lead[lead] = _hours_by_day(
            times, temps, station=station, dates=dates, lead=lead
        )
    out = {}
    for day in dates:
        lead1 = by_lead[1][day]
        out[day] = {
            "high": lead1["high"],
            "low": lead1["low"],
            "leads": {
                "1": dict(by_lead[1][day]),
                "2": dict(by_lead[2][day]),
                "3": dict(by_lead[3][day]),
            },
        }
    return out


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
            "utc_offset_seconds": {},
        }
    }
    for spec in stations:
        payload = fetch(previous_runs_url(spec, start, end))
        archive[META_KEY]["utc_offset_seconds"][spec.station_id] = (
            utc_offset_from_payload(payload, station=spec.station_id)
        )
        archive[spec.station_id] = daily_from_previous_runs(
            payload,
            station=spec.station_id,
            start=start,
            end=end,
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
    print(f"wrote {out} stations={n} {args.start}–{args.end} leads=1-3")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ArchiveBuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
