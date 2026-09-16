#!/usr/bin/env python3
"""Build a replay forecast archive from Open-Meteo Previous Runs.

Output shape:
    {station_id: {YYYY-MM-DD: {"high": int | None, "low": int | None}}, "_meta": {...}}
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


SKILL_DIR = Path(__file__).resolve().parents[1]
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

import weather_trader as wt  # noqa: E402


OPEN_METEO_PREVIOUS_RUNS_BASE = "https://previous-runs-api.open-meteo.com/v1/forecast"
LEADS = (1, 2, 3)
CHUNK_DAYS = 10
MAX_ATTEMPTS = 3
REQUESTED_DAILY_VARS = tuple(
    var
    for lead in LEADS
    for var in (
        f"temperature_2m_max_previous_day{lead}",
        f"temperature_2m_min_previous_day{lead}",
    )
)
ALT_DAILY_VARS = {
    f"temperature_2m_max_previous_day{lead}": f"previous_day{lead}_temperature_2m_max"
    for lead in LEADS
} | {
    f"temperature_2m_min_previous_day{lead}": f"previous_day{lead}_temperature_2m_min"
    for lead in LEADS
}
REQUESTED_HOURLY_VARS = tuple(f"temperature_2m_previous_day{lead}" for lead in LEADS)
ALT_HOURLY_VARS = {
    f"temperature_2m_previous_day{lead}": f"previous_day{lead}_temperature_2m"
    for lead in LEADS
}


def _parse_day(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _station_specs(stations: str = "all") -> dict:
    specs = {}
    if stations not in {"us", "intl", "all"}:
        raise ValueError("stations must be one of: us, intl, all")

    if stations in {"us", "all"}:
        # Only configured Polymarket US resolution stations. STATION_ID_TO_NOAA
        # also carries alternates for parsing resilience, which should not
        # trigger extra archive-builder calls.
        for loc in wt.LOCATIONS.values():
            station = loc.get("station")
            if station and station in wt.STATION_ID_TO_NOAA:
                coords = wt.STATION_ID_TO_NOAA[station]
                specs[station] = {
                    "lat": coords["lat"],
                    "lon": coords["lon"],
                    "name": coords.get("name"),
                    "unit": "fahrenheit",
                    "table": "STATION_ID_TO_NOAA",
                }
    if stations in {"intl", "all"}:
        for station, coords in wt.INTERNATIONAL_STATION_COORDS.items():
            specs[station] = {
                "lat": coords["lat"],
                "lon": coords["lon"],
                "name": coords.get("name"),
                "unit": "celsius",
                "table": "INTERNATIONAL_STATION_COORDS",
            }
    return dict(sorted(specs.items()))


def _date_chunks(start: date, end: date, chunk_days: int = CHUNK_DAYS):
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end)
        yield current, chunk_end
        current = chunk_end + timedelta(days=1)


def _date_range(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _daily_values(daily: dict, preferred: str, alternate: str) -> list:
    if preferred in daily:
        return daily[preferred]
    return daily.get(alternate, [])


def _hourly_values(hourly: dict, preferred: str, alternate: str) -> list:
    if preferred in hourly:
        return hourly[preferred]
    return hourly.get(alternate, [])


def _open_json_with_retry(url: str, opener=urlopen, sleep=time.sleep) -> dict:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with opener(url, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except HTTPError as exc:
            if 400 <= exc.code < 500 or attempt == MAX_ATTEMPTS:
                raise
        except URLError:
            if attempt == MAX_ATTEMPTS:
                raise
        sleep(0.5 * (2 ** (attempt - 1)))
    raise RuntimeError("unreachable retry state")


def _round_temp(value):
    return round(value) if value is not None else None


def _fetch_station_chunk(
    station_id: str,
    spec: dict,
    start: date,
    end: date,
    opener=urlopen,
    sleep=time.sleep,
) -> tuple[dict, str, int | None]:
    params = {
        "latitude": spec["lat"],
        "longitude": spec["lon"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "hourly": ",".join(REQUESTED_HOURLY_VARS),
        "temperature_unit": spec["unit"],
        "timezone": "auto",
    }
    url = f"{OPEN_METEO_PREVIOUS_RUNS_BASE}?{urlencode(params)}"
    payload = _open_json_with_retry(url, opener=opener, sleep=sleep)

    daily = payload.get("daily", {})
    days = daily.get("time", [])
    archive = {}
    if days:
        lead_values = {}
        for lead in LEADS:
            high_key = f"temperature_2m_max_previous_day{lead}"
            low_key = f"temperature_2m_min_previous_day{lead}"
            lead_values[lead] = (
                _daily_values(daily, high_key, ALT_DAILY_VARS[high_key]),
                _daily_values(daily, low_key, ALT_DAILY_VARS[low_key]),
            )

        for index, day in enumerate(days):
            leads = {}
            for lead, (highs, lows) in lead_values.items():
                high = highs[index] if index < len(highs) else None
                low = lows[index] if index < len(lows) else None
                leads[str(lead)] = {"high": _round_temp(high), "low": _round_temp(low)}
            archive[day] = {
                "high": leads["1"]["high"],
                "low": leads["1"]["low"],
                "leads": leads,
            }
        return archive, url, payload.get("utc_offset_seconds")

    hourly = payload.get("hourly", {})
    hours = hourly.get("time", [])
    by_day = {day.isoformat(): {str(lead): [] for lead in LEADS} for day in _date_range(start, end)}
    for lead in LEADS:
        temp_key = f"temperature_2m_previous_day{lead}"
        values = _hourly_values(hourly, temp_key, ALT_HOURLY_VARS[temp_key])
        for index, timestamp in enumerate(hours):
            day = timestamp[:10]
            if day in by_day and index < len(values) and values[index] is not None:
                by_day[day][str(lead)].append(values[index])

    for day, lead_temps in by_day.items():
        leads = {}
        for lead, values in lead_temps.items():
            leads[lead] = {
                "high": _round_temp(max(values)) if values else None,
                "low": _round_temp(min(values)) if values else None,
            }
        archive[day] = {
            "high": leads["1"]["high"],
            "low": leads["1"]["low"],
            "leads": leads,
        }
    return archive, url, payload.get("utc_offset_seconds")


def _fetch_station(
    station_id: str,
    spec: dict,
    start: date,
    end: date,
    opener=urlopen,
    sleep=time.sleep,
) -> tuple[dict, list[str], int | None]:
    archive = {}
    urls = []
    utc_offset_seconds = None
    for chunk_start, chunk_end in _date_chunks(start, end):
        chunk_archive, url, chunk_offset = _fetch_station_chunk(
            station_id,
            spec,
            chunk_start,
            chunk_end,
            opener=opener,
            sleep=sleep,
        )
        if utc_offset_seconds is None:
            utc_offset_seconds = chunk_offset
        elif chunk_offset != utc_offset_seconds:
            raise ValueError(
                f"{station_id} utc_offset_seconds changed across chunks: "
                f"{utc_offset_seconds} != {chunk_offset}"
            )
        archive.update(chunk_archive)
        urls.append(url)
    return archive, urls, utc_offset_seconds


def build_archive(start: date, end: date, stations: str = "all", opener=urlopen, sleep=time.sleep) -> dict:
    if end < start:
        raise ValueError("--end must be on or after --start")

    specs = _station_specs(stations)
    out = {
        "_meta": {
            "source": "Open-Meteo Previous Runs API",
            "endpoint": OPEN_METEO_PREVIOUS_RUNS_BASE,
            "daily": list(REQUESTED_DAILY_VARS),
            "hourly": list(REQUESTED_HOURLY_VARS),
            "lead_time": "previous_day1",
            "leads": list(LEADS),
            "chunk_days": CHUNK_DAYS,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "stations": len(specs),
            "station_filter": stations,
            "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "station_units": {station: spec["unit"] for station, spec in specs.items()},
            "station_tables": {station: spec["table"] for station, spec in specs.items()},
        }
    }
    urls = {}
    utc_offsets = {}
    for station_id, spec in specs.items():
        out[station_id], urls[station_id], utc_offsets[station_id] = _fetch_station(
            station_id,
            spec,
            start,
            end,
            opener=opener,
            sleep=sleep,
        )
    out["_meta"]["request_urls"] = urls
    out["_meta"]["utc_offset_seconds"] = utc_offsets
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, type=_parse_day)
    parser.add_argument("--end", required=True, type=_parse_day)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--stations", choices=("us", "intl", "all"), default="all")
    args = parser.parse_args(argv)

    archive = build_archive(args.start, args.end, stations=args.stations)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(archive, indent=2, sort_keys=True) + "\n")
    station_count = len([k for k in archive if k != "_meta"])
    leads = archive["_meta"].get("leads", [])
    lead_span = f"{min(leads)}-{max(leads)}" if leads else "none"
    print(f"wrote {args.out} stations={station_count} leads={lead_span} span={args.start}..{args.end}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
