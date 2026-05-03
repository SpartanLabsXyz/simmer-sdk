#!/usr/bin/env python3
"""
Simmer Weather Trading Skill

Trades Polymarket weather markets using NOAA forecasts.
Inspired by gopfan2's $2M+ weather trading strategy.

Usage:
    python weather_trader.py              # Dry run (show opportunities, no trades)
    python weather_trader.py --live       # Execute real trades
    python weather_trader.py --positions  # Show current positions only
    python weather_trader.py --smart-sizing  # Use portfolio-based position sizing

Requires:
    SIMMER_API_KEY environment variable (get from simmer.markets/dashboard)
"""

import os
import sys
import re
import json
import argparse
from datetime import datetime, timezone, timedelta
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

# Force line-buffered stdout so output is visible in non-TTY environments (cron, Docker, OpenClaw)
sys.stdout.reconfigure(line_buffering=True)

# Optional: Trade Journal integration for tracking
try:
    from tradejournal import log_trade
    JOURNAL_AVAILABLE = True
except ImportError:
    try:
        # Try relative import within skills package
        from skills.tradejournal import log_trade
        JOURNAL_AVAILABLE = True
    except ImportError:
        JOURNAL_AVAILABLE = False
        def log_trade(*args, **kwargs):
            pass  # No-op if tradejournal not installed

# =============================================================================
# Configuration (config.json > env vars > defaults)
# =============================================================================

from simmer_sdk.skill import load_config, update_config, get_config_path

# Configuration schema
# Note: env var names match autotune registry. Legacy aliases (SIMMER_WEATHER_ENTRY,
# SIMMER_WEATHER_EXIT, SIMMER_WEATHER_MAX_POSITION, SIMMER_WEATHER_MAX_TRADES) are
# resolved as fallbacks below for backwards compatibility.
CONFIG_SCHEMA = {
    "entry_threshold":   {"env": "SIMMER_WEATHER_ENTRY_THRESHOLD",   "default": 0.15,  "type": float},
    "exit_threshold":    {"env": "SIMMER_WEATHER_EXIT_THRESHOLD",    "default": 0.45,  "type": float},
    "max_position_usd":  {"env": "SIMMER_WEATHER_MAX_POSITION_USD",  "default": 2.00,  "type": float},
    "sizing_pct":        {"env": "SIMMER_WEATHER_SIZING_PCT",        "default": 0.05,  "type": float},
    "max_trades_per_run":{"env": "SIMMER_WEATHER_MAX_TRADES_PER_RUN","default": 5,     "type": int},
    "locations":         {"env": "SIMMER_WEATHER_LOCATIONS",         "default": "NYC", "type": str},
    "binary_only":       {"env": "SIMMER_WEATHER_BINARY_ONLY",       "default": False, "type": bool},
    "slippage_max":      {"env": "SIMMER_WEATHER_SLIPPAGE_MAX",      "default": 0.15,  "type": float},
    "min_liquidity":     {"env": "SIMMER_WEATHER_MIN_LIQUIDITY",     "default": 0.0,   "type": float},
    "order_type":        {"env": "SIMMER_WEATHER_ORDER_TYPE",        "default": "GTC", "type": str,
                          "help": "Order type: GTC (default, limit order that waits for fill) or FAK (cancel if not filled immediately). GTC recommended for illiquid weather markets."},
    "vol_targeting":     {"env": "SIMMER_WEATHER_VOL_TARGETING",     "default": False, "type": bool,
                          "help": "Enable volatility targeting: scale position sizes by target_vol / realized_vol."},
    "target_vol":        {"env": "SIMMER_WEATHER_TARGET_VOL",        "default": 0.20,  "type": float,
                          "help": "Target annualized volatility (0.20 = 20%). Used when vol_targeting is enabled."},
    "vol_max_leverage":  {"env": "SIMMER_WEATHER_VOL_MAX_LEVERAGE",  "default": 2.0,   "type": float,
                          "help": "Max leverage multiplier from vol targeting (caps scale-up in calm markets)."},
    "vol_min_allocation":{"env": "SIMMER_WEATHER_VOL_MIN_ALLOC",     "default": 0.2,   "type": float,
                          "help": "Min allocation floor from vol targeting (stay in market during high vol)."},
    "vol_span":          {"env": "SIMMER_WEATHER_VOL_SPAN",          "default": 10,    "type": int,
                          "help": "EWMA span for volatility calculation (lower = more responsive)."},
}

# Backwards-compatible env var aliases (old name -> new name)
_LEGACY_ENV_ALIASES = {
    "SIMMER_WEATHER_ENTRY":        "SIMMER_WEATHER_ENTRY_THRESHOLD",
    "SIMMER_WEATHER_EXIT":         "SIMMER_WEATHER_EXIT_THRESHOLD",
    "SIMMER_WEATHER_MAX_POSITION": "SIMMER_WEATHER_MAX_POSITION_USD",
    "SIMMER_WEATHER_MAX_TRADES":   "SIMMER_WEATHER_MAX_TRADES_PER_RUN",
}
for _old, _new in _LEGACY_ENV_ALIASES.items():
    if _old in os.environ and _new not in os.environ:
        os.environ[_new] = os.environ[_old]

# Load configuration
_config = load_config(CONFIG_SCHEMA, __file__, slug="polymarket-weather-trader")

NOAA_API_BASE = "https://api.weather.gov"
ORDER_TYPE = (_config.get("order_type") or "GTC").upper()

# SimmerClient singleton
_client = None

def get_client(live=True):
    """Lazy-init SimmerClient singleton."""
    global _client
    if _client is None:
        try:
            from simmer_sdk import SimmerClient
        except ImportError:
            print("Error: simmer-sdk>=0.13.0 not installed. Run: pip install --upgrade simmer-sdk")
            sys.exit(1)
        # TRADING_VENUE is user-tunable — defaults to polymarket; set to "sim" for paper trading.
        venue = os.environ.get("TRADING_VENUE", "polymarket")
        _client = SimmerClient.from_env(venue=venue, live=live)
    return _client

# Source tag for tracking
TRADE_SOURCE = "sdk:weather"
SKILL_SLUG = "polymarket-weather-trader"

# Polymarket constraints
MIN_SHARES_PER_ORDER = 5.0  # Polymarket requires minimum 5 shares
MIN_TICK_SIZE = 0.01        # Minimum tradeable price

# Strategy parameters - from config
ENTRY_THRESHOLD = _config["entry_threshold"]
EXIT_THRESHOLD = _config["exit_threshold"]
MAX_POSITION_USD = _config["max_position_usd"]

# Smart sizing parameters
SMART_SIZING_PCT = _config["sizing_pct"]

# Rate limiting
MAX_TRADES_PER_RUN = _config["max_trades_per_run"]

# Market type filter
BINARY_ONLY = _config["binary_only"]

# Volatility targeting parameters
VOL_TARGETING = _config["vol_targeting"]
TARGET_VOL = _config["target_vol"]
VOL_MAX_LEVERAGE = _config["vol_max_leverage"]
VOL_MIN_ALLOCATION = _config["vol_min_allocation"]
VOL_SPAN = _config["vol_span"]

# Context safeguard thresholds
SLIPPAGE_MAX_PCT = _config["slippage_max"]  # Skip if slippage exceeds this (tunable)
MIN_LIQUIDITY_USD = _config["min_liquidity"]  # Skip markets with liquidity below this (0 = disabled)
TIME_TO_RESOLUTION_MIN_HOURS = 2  # Skip if resolving in < 2 hours

# Price trend detection
PRICE_DROP_THRESHOLD = 0.10  # 10% drop in last 24h = stronger signal

# City fallback coordinates. Used only as a last resort when a market does not
# expose resolution_criteria (older API responses pre 2026-05-03). Polymarket's
# actual oracle is parsed per-market from resolution_criteria — see
# parse_resolution_station() and STATION_ID_TO_NOAA below. Note: prior to the
# resolution-source rework, this table hardcoded KDFW for Dallas. Polymarket
# actually resolves Dallas weather on KDAL (Love Field), not KDFW. Keeping
# Dallas out of the fallback so we fail loud rather than silently re-introduce
# the bug.
LOCATIONS = {
    "NYC": {"lat": 40.7769, "lon": -73.8740, "name": "New York City (LaGuardia)", "station": "KLGA"},
    "Chicago": {"lat": 41.9742, "lon": -87.9073, "name": "Chicago (O'Hare)", "station": "KORD"},
    "Seattle": {"lat": 47.4502, "lon": -122.3088, "name": "Seattle (Sea-Tac)", "station": "KSEA"},
    "Atlanta": {"lat": 33.6407, "lon": -84.4277, "name": "Atlanta (Hartsfield)", "station": "KATL"},
    "Miami": {"lat": 25.7959, "lon": -80.2870, "name": "Miami (MIA)", "station": "KMIA"},
}

# Per-station coordinates for NOAA `/points/{lat},{lon}` lookup. Keyed by the
# ICAO code Polymarket cites in resolution_criteria. Add new entries here as
# Polymarket adds new resolution stations — the parser surfaces unknowns as
# skip-with-log so we notice quickly.
STATION_ID_TO_NOAA = {
    # NYC area
    "KLGA": {"lat": 40.7769, "lon": -73.8740, "name": "LaGuardia Airport"},
    "KJFK": {"lat": 40.6413, "lon": -73.7781, "name": "JFK International Airport"},
    "KEWR": {"lat": 40.6895, "lon": -74.1745, "name": "Newark Liberty International"},
    "KNYC": {"lat": 40.7831, "lon": -73.9712, "name": "NYC Central Park"},
    # Chicago area
    "KORD": {"lat": 41.9742, "lon": -87.9073, "name": "Chicago O'Hare Intl Airport"},
    "KMDW": {"lat": 41.7860, "lon": -87.7524, "name": "Chicago Midway"},
    # Seattle
    "KSEA": {"lat": 47.4502, "lon": -122.3088, "name": "Seattle-Tacoma International Airport"},
    # Atlanta
    "KATL": {"lat": 33.6407, "lon": -84.4277, "name": "Hartsfield-Jackson Atlanta International Airport"},
    # Dallas area — KDAL is Polymarket's actual oracle for Dallas; KDFW kept
    # so a future market that resolves on DFW also works
    "KDAL": {"lat": 32.8471, "lon": -96.8517, "name": "Dallas Love Field"},
    "KDFW": {"lat": 32.8998, "lon": -97.0403, "name": "Dallas/Fort Worth International Airport"},
    # Miami
    "KMIA": {"lat": 25.7959, "lon": -80.2870, "name": "Miami International Airport"},
    # Common additions (pre-populated to reduce churn as Polymarket expands)
    "KBOS": {"lat": 42.3656, "lon": -71.0096, "name": "Boston Logan International"},
    "KDCA": {"lat": 38.8512, "lon": -77.0402, "name": "Reagan National (DC)"},
    "KIAD": {"lat": 38.9531, "lon": -77.4565, "name": "Washington Dulles"},
    "KPHX": {"lat": 33.4373, "lon": -112.0078, "name": "Phoenix Sky Harbor"},
    "KLAS": {"lat": 36.0840, "lon": -115.1537, "name": "Las Vegas McCarran"},
    "KSFO": {"lat": 37.6213, "lon": -122.3790, "name": "San Francisco International"},
    "KLAX": {"lat": 33.9416, "lon": -118.4085, "name": "Los Angeles International"},
    "KDEN": {"lat": 39.8561, "lon": -104.6737, "name": "Denver International"},
    "KMSP": {"lat": 44.8848, "lon": -93.2223, "name": "Minneapolis-St. Paul International"},
    "KPHL": {"lat": 39.8744, "lon": -75.2424, "name": "Philadelphia International"},
}

# Active locations - from config
_locations_str = _config["locations"]
ACTIVE_LOCATIONS = [loc.strip().upper() for loc in _locations_str.split(",") if loc.strip()]

# =============================================================================
# NOAA Weather API
# =============================================================================

# International city coordinates for Open-Meteo fallback
# Keyed by the city name as it appears in market questions
INTERNATIONAL_LOCATIONS = {
    "Tel Aviv":   {"lat": 32.0853, "lon": 34.7818, "tz": "Asia/Jerusalem"},
    "Munich":     {"lat": 48.1351, "lon": 11.5820, "tz": "Europe/Berlin"},
    "London":     {"lat": 51.5074, "lon": -0.1278, "tz": "Europe/London"},
    "Tokyo":      {"lat": 35.6762, "lon": 139.6503, "tz": "Asia/Tokyo"},
    "Seoul":      {"lat": 37.5665, "lon": 126.9780, "tz": "Asia/Seoul"},
    "Ankara":     {"lat": 39.9334, "lon": 32.8597,  "tz": "Europe/Istanbul"},
    "Lucknow":    {"lat": 26.8467, "lon": 80.9462,  "tz": "Asia/Kolkata"},
    "Wellington": {"lat": -41.2866, "lon": 174.7756, "tz": "Pacific/Auckland"},
    "Madrid":     {"lat": 40.4168, "lon": -3.7038,  "tz": "Europe/Madrid"},
    "Milan":      {"lat": 45.4642, "lon": 9.1900,   "tz": "Europe/Rome"},
    "Amsterdam":  {"lat": 52.3676, "lon": 4.9041,   "tz": "Europe/Amsterdam"},
    "Taipei":     {"lat": 25.0330, "lon": 121.5654, "tz": "Asia/Taipei"},
}

# Per-station coordinates for international Open-Meteo lookup. Keyed by ICAO
# code, NOT city — because some cities have airports far from the city center
# (Milan/Malpensa is ~50km away, Tokyo/Narita is ~60km away, Seoul/Incheon
# ~50km, Madrid/Barajas ~13km). Routing by city would re-introduce the same
# class of bug the US side fixes by going per-station. Coords are the airport
# itself; tz is the local timezone.
INTERNATIONAL_STATION_COORDS = {
    "LLBG": {"lat": 32.0114, "lon": 34.8867, "tz": "Asia/Jerusalem",   "city": "Tel Aviv"},   # Ben Gurion
    "EDDM": {"lat": 48.3538, "lon": 11.7861, "tz": "Europe/Berlin",    "city": "Munich"},     # Munich Airport
    "EGLL": {"lat": 51.4700, "lon": -0.4543, "tz": "Europe/London",    "city": "London"},     # Heathrow
    "RJTT": {"lat": 35.5494, "lon": 139.7798, "tz": "Asia/Tokyo",      "city": "Tokyo"},      # Haneda
    "RJAA": {"lat": 35.7647, "lon": 140.3863, "tz": "Asia/Tokyo",      "city": "Tokyo"},      # Narita
    "RKSI": {"lat": 37.4602, "lon": 126.4407, "tz": "Asia/Seoul",      "city": "Seoul"},      # Incheon
    "RKSS": {"lat": 37.5586, "lon": 126.7906, "tz": "Asia/Seoul",      "city": "Seoul"},      # Gimpo
    "LTAC": {"lat": 40.1281, "lon": 32.9951, "tz": "Europe/Istanbul",  "city": "Ankara"},     # Esenboga
    "VILK": {"lat": 26.7606, "lon": 80.8893, "tz": "Asia/Kolkata",     "city": "Lucknow"},    # Chaudhary Charan Singh
    "NZWN": {"lat": -41.3272, "lon": 174.8053, "tz": "Pacific/Auckland", "city": "Wellington"},  # Wellington Intl
    "LEMD": {"lat": 40.4839, "lon": -3.5680, "tz": "Europe/Madrid",    "city": "Madrid"},     # Barajas
    "LIMC": {"lat": 45.6306, "lon": 8.7281, "tz": "Europe/Rome",       "city": "Milan"},      # Malpensa
    "LIML": {"lat": 45.4451, "lon": 9.2767, "tz": "Europe/Rome",       "city": "Milan"},      # Linate
    "EHAM": {"lat": 52.3105, "lon": 4.7683, "tz": "Europe/Amsterdam",  "city": "Amsterdam"},  # Schiphol
    "RCSS": {"lat": 25.0697, "lon": 121.5519, "tz": "Asia/Taipei",     "city": "Taipei"},     # Songshan
    "RCTP": {"lat": 25.0777, "lon": 121.2328, "tz": "Asia/Taipei",     "city": "Taipei"},     # Taoyuan
}

# =============================================================================
# Resolution-source parser
# =============================================================================
#
# Polymarket weather markets carry a `resolution_criteria` field that names
# the exact station the market resolves on, e.g.:
#
#   "This market will resolve to the temperature range that contains the
#    highest temperature recorded at the Chicago O'Hare Intl Airport Station
#    in degrees Fahrenheit on 2 May '26.
#    The resolution source for this market will be information from
#    Wunderground, specifically the highest temperature recorded for all
#    times on this day by the Forecast for the Chicago O'Hare Intl Airport
#    Station once information is finalized, available here:
#    https://www.wunderground.com/history/daily/us/il/chicago/KORD."
#
# We extract the ICAO code from the trailing wunderground URL — most reliable
# signal, present on every weather market we've sampled. Fall back to the
# station-name phrase when the URL is absent.
#
# Returns {"station_id": "KORD", "station_name": "Chicago O'Hare Intl Airport"}
# or None when the criteria text doesn't look like a recognized weather market.
_WUNDERGROUND_URL_RE = re.compile(
    # /history/daily/<country>/<region>/<city>/<ICAO> for US (3 path segments
    # after country) and /history/daily/<country>/<city>/<ICAO> for most
    # international (2 segments). The (?:.../)+ allows either shape.
    r"wunderground\.com/history/daily/[a-z]{2}/(?:[a-z0-9_\-]+/)+([A-Z]{4})\b",
    re.IGNORECASE,
)
_STATION_PHRASE_RE = re.compile(
    r"recorded at the (.+?) Station",
    re.IGNORECASE,
)


def parse_resolution_station(criteria: str) -> dict:
    """Extract the resolution station from a market's resolution_criteria.

    Returns a dict with `station_id` (4-letter ICAO, uppercase) and
    `station_name` (human-readable airport name), or None if the criteria
    doesn't reference a recognized weather station.
    """
    if not criteria or not isinstance(criteria, str):
        return None

    station_id = None
    url_match = _WUNDERGROUND_URL_RE.search(criteria)
    if url_match:
        station_id = url_match.group(1).upper()

    station_name = None
    phrase_match = _STATION_PHRASE_RE.search(criteria)
    if phrase_match:
        station_name = phrase_match.group(1).strip()

    if not station_id and not station_name:
        return None

    return {"station_id": station_id, "station_name": station_name}


OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"

def _fetch_openmeteo_at(lat: float, lon: float, tz: str, label: str) -> dict:
    """Internal: fetch Open-Meteo daily highs/lows at the given coords."""
    params = (
        f"?latitude={lat}&longitude={lon}"
        f"&daily=temperature_2m_max,temperature_2m_min"
        f"&temperature_unit=celsius"
        f"&timezone={tz.replace('/', '%2F')}"
        f"&forecast_days=10"
    )
    url = OPEN_METEO_BASE + params
    try:
        from urllib.request import urlopen
        import json as _json
        with urlopen(url, timeout=15) as resp:
            data = _json.loads(resp.read().decode())
    except Exception as e:
        print(f"  Open-Meteo error for {label}: {e}")
        return {}

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    highs = daily.get("temperature_2m_max", [])
    lows  = daily.get("temperature_2m_min", [])

    forecasts = {}
    for d, h, l in zip(dates, highs, lows):
        forecasts[d] = {
            "high_c": round(h) if h is not None else None,
            "low_c":  round(l) if l is not None else None,
        }
    return forecasts


def get_openmeteo_forecast(city: str) -> dict:
    """Legacy city-keyed Open-Meteo wrapper. Used as a fallback when the
    skill can't parse a per-station ICAO from resolution_criteria. Routes
    through INTERNATIONAL_LOCATIONS (city-center coords).
    """
    loc = INTERNATIONAL_LOCATIONS.get(city)
    if not loc:
        return {}
    return _fetch_openmeteo_at(loc["lat"], loc["lon"], loc["tz"], city)


def get_openmeteo_forecast_for_station(station_id: str) -> dict:
    """Get Open-Meteo forecast at the airport's exact coords (not the city
    center). Critical for cities with airports far from downtown — Milan/
    Malpensa is ~50km out, Tokyo/Narita ~60km, Seoul/Incheon ~50km.
    """
    coords = INTERNATIONAL_STATION_COORDS.get(station_id)
    if not coords:
        return {}
    return _fetch_openmeteo_at(coords["lat"], coords["lon"], coords["tz"], station_id)


def fetch_json(url, headers=None):
    """Fetch JSON from URL with error handling."""
    try:
        req = Request(url, headers=headers or {})
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode())
    except HTTPError as e:
        print(f"  HTTP Error {e.code}: {url}")
        return None
    except URLError as e:
        print(f"  URL Error: {e.reason}")
        return None
    except Exception as e:
        print(f"  Error fetching {url}: {e}")
        return None


def get_noaa_forecast_for_station(station_id: str) -> dict:
    """Get NOAA forecast for a specific ICAO station id.

    Looks up coordinates from STATION_ID_TO_NOAA, fetches the NOAA gridpoint
    forecast, and supplements today's high/low from the station's latest
    observation when the forecast period misses it. Returns dict keyed by
    `YYYY-MM-DD` -> {"high": int, "low": int} (Fahrenheit).
    """
    if station_id not in STATION_ID_TO_NOAA:
        print(f"  Unknown NOAA station: {station_id}")
        return {}

    loc = STATION_ID_TO_NOAA[station_id]
    headers = {
        "User-Agent": "SimmerWeatherSkill/1.0 (https://simmer.markets)",
        "Accept": "application/geo+json",
    }

    points_url = f"{NOAA_API_BASE}/points/{loc['lat']},{loc['lon']}"
    points_data = fetch_json(points_url, headers)

    if not points_data or "properties" not in points_data:
        print(f"  Failed to get NOAA grid for {station_id}")
        return {}

    forecast_url = points_data["properties"].get("forecast")
    if not forecast_url:
        print(f"  No forecast URL for {station_id}")
        return {}

    forecast_data = fetch_json(forecast_url, headers)
    if not forecast_data or "properties" not in forecast_data:
        print(f"  Failed to get NOAA forecast for {station_id}")
        return {}

    periods = forecast_data["properties"].get("periods", [])
    forecasts = {}

    for period in periods:
        start_time = period.get("startTime", "")
        if not start_time:
            continue

        date_str = start_time[:10]
        temp = period.get("temperature")
        is_daytime = period.get("isDaytime", True)

        if date_str not in forecasts:
            forecasts[date_str] = {"high": None, "low": None}

        if is_daytime:
            forecasts[date_str]["high"] = temp
        else:
            forecasts[date_str]["low"] = temp

    # Supplement with NOAA observations for today (D+0). /forecast often
    # starts from the next period, missing today's daytime high. NOAA's
    # `/stations/{id}/observations/latest` keys by the same ICAO code
    # Polymarket cites in resolution_criteria, so this just works.
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today_str not in forecasts or forecasts[today_str].get("high") is None:
        try:
            obs_url = f"{NOAA_API_BASE}/stations/{station_id}/observations/latest"
            obs_data = fetch_json(obs_url, headers)
            if obs_data and "properties" in obs_data:
                temp_c = obs_data["properties"].get("temperature", {}).get("value")
                if temp_c is not None:
                    temp_f = round(temp_c * 9 / 5 + 32)
                    if today_str not in forecasts:
                        forecasts[today_str] = {"high": None, "low": None}
                    if forecasts[today_str]["high"] is None:
                        forecasts[today_str]["high"] = temp_f
                    if forecasts[today_str]["low"] is None:
                        forecasts[today_str]["low"] = temp_f
        except Exception:
            pass  # Observation fetch is best-effort

    return forecasts


def get_noaa_forecast(location: str) -> dict:
    """Legacy city-keyed wrapper. Routes to the per-station fetcher using the
    LOCATIONS fallback table. Kept only for the rare case a market is missing
    `resolution_criteria` (older API responses or non-Polymarket sources).
    """
    if location not in LOCATIONS:
        print(f"  Unknown location: {location}")
        return {}
    fallback_station = LOCATIONS[location].get("station")
    if not fallback_station:
        return {}
    return get_noaa_forecast_for_station(fallback_station)


# =============================================================================
# Market Parsing
# =============================================================================

def parse_weather_event(event_name: str) -> dict:
    """Parse weather event name to extract location, date, metric."""
    if not event_name:
        return None

    event_lower = event_name.lower()

    if 'highest' in event_lower or 'high temp' in event_lower:
        metric = 'high'
    elif 'lowest' in event_lower or 'low temp' in event_lower:
        metric = 'low'
    else:
        metric = 'high'

    location = None
    location_aliases = {
        'nyc': 'NYC', 'new york': 'NYC', 'laguardia': 'NYC', 'la guardia': 'NYC',
        'chicago': 'Chicago', "o'hare": 'Chicago', 'ohare': 'Chicago',
        'seattle': 'Seattle', 'sea-tac': 'Seattle',
        'atlanta': 'Atlanta', 'hartsfield': 'Atlanta',
        'dallas': 'Dallas', 'dfw': 'Dallas',
        'miami': 'Miami',
        # International cities (Open-Meteo)
        'tel aviv': 'Tel Aviv',
        'munich': 'Munich',
        'london': 'London',
        'tokyo': 'Tokyo',
        'seoul': 'Seoul',
        'ankara': 'Ankara',
        'lucknow': 'Lucknow',
        'wellington': 'Wellington',
        'madrid': 'Madrid',
        'milan': 'Milan',
        'amsterdam': 'Amsterdam',
        'taipei': 'Taipei',
    }

    for alias, loc in location_aliases.items():
        if alias in event_lower:
            location = loc
            break

    if not location:
        return None

    # Detect temperature unit from event name
    temp_unit = 'C' if '°c' in event_lower or re.search(r'\d+°?c\b', event_lower, re.IGNORECASE) else 'F'

    month_day_match = re.search(r'on\s+([a-zA-Z]+)\s+(\d{1,2})', event_name, re.IGNORECASE)
    if not month_day_match:
        return None

    month_name = month_day_match.group(1).lower()
    day = int(month_day_match.group(2))

    month_map = {
        'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3,
        'april': 4, 'apr': 4, 'may': 5, 'june': 6, 'jun': 6, 'july': 7, 'jul': 7,
        'august': 8, 'aug': 8, 'september': 9, 'sep': 9, 'october': 10, 'oct': 10,
        'november': 11, 'nov': 11, 'december': 12, 'dec': 12,
    }

    month = month_map.get(month_name)
    if not month:
        return None

    now = datetime.now(timezone.utc)
    year = now.year
    try:
        target_date = datetime(year, month, day, tzinfo=timezone.utc)
        if target_date < now - timedelta(days=7):
            year += 1
        date_str = f"{year}-{month:02d}-{day:02d}"
    except ValueError:
        return None

    return {"location": location, "date": date_str, "metric": metric, "unit": temp_unit}


def parse_temperature_bucket(outcome_name: str) -> tuple:
    """Parse temperature bucket from outcome name. Works for both °F and °C markets,
    including single-degree exact buckets (e.g. '22°C') and ranges (e.g. '54-55°F')."""
    if not outcome_name:
        return None

    below_match = re.search(r'(\d+)\s*°?[fFcC]?\s*(or below|or less)', outcome_name, re.IGNORECASE)
    if below_match:
        return (-999, int(below_match.group(1)))

    above_match = re.search(r'(\d+)\s*°?[fFcC]?\s*(or higher|or above|or more)', outcome_name, re.IGNORECASE)
    if above_match:
        return (int(above_match.group(1)), 999)

    range_match = re.search(r'(\d+)\s*(?:°?\s*[fFcC])?\s*(?:-|–|to)\s*(\d+)', outcome_name)
    if range_match:
        low, high = int(range_match.group(1)), int(range_match.group(2))
        return (min(low, high), max(low, high))

    # Single exact-degree bucket: "be 22°C on" or "22°F"
    exact_match = re.search(r'\b(\d+)\s*°[fFcC]\b', outcome_name)
    if exact_match:
        t = int(exact_match.group(1))
        return (t, t)

    # Bare integer in short outcome names like "22°C"
    bare_match = re.match(r'^\s*(\d+)\s*°?[cCfF]?\s*$', outcome_name.strip())
    if bare_match:
        t = int(bare_match.group(1))
        return (t, t)

    return None


# =============================================================================
# Simmer API - Core
# =============================================================================

# =============================================================================
# Simmer API - Portfolio & Context
# =============================================================================

def get_portfolio() -> dict:
    """Get portfolio summary from SDK."""
    try:
        return get_client().get_portfolio()
    except Exception as e:
        print(f"  ⚠️  Portfolio fetch failed: {e}")
        return None


def get_market_context(market_id: str, my_probability: float = None) -> dict:
    """Get market context with safeguards and optional edge analysis."""
    try:
        if my_probability is not None:
            return get_client()._request("GET", f"/api/sdk/context/{market_id}",
                                         params={"my_probability": my_probability})
        return get_client().get_market_context(market_id)
    except Exception:
        return None


def get_price_history(market_id: str) -> list:
    """Get price history for trend detection."""
    try:
        return get_client().get_price_history(market_id)
    except Exception:
        return []


def check_context_safeguards(context: dict, use_edge: bool = True) -> tuple:
    """
    Check context for safeguards. Returns (should_trade, reasons).
    
    Args:
        context: Context response from SDK
        use_edge: If True, respect edge recommendation (TRADE/HOLD/SKIP)
    """
    if not context:
        return True, []  # No context = proceed (fail open)

    reasons = []
    market = context.get("market", {})
    warnings = context.get("warnings", [])
    discipline = context.get("discipline", {})
    slippage = context.get("slippage", {})
    edge = context.get("edge", {})

    # Check for deal-breakers in warnings
    for warning in warnings:
        if "MARKET RESOLVED" in str(warning).upper():
            return False, ["Market already resolved"]

    # Check flip-flop warning
    warning_level = discipline.get("warning_level", "none")
    if warning_level == "severe":
        return False, [f"Severe flip-flop warning: {discipline.get('flip_flop_warning', '')}"]
    elif warning_level == "mild":
        reasons.append("Mild flip-flop warning (proceed with caution)")

    # Check time to resolution
    time_str = market.get("time_to_resolution", "")
    if time_str:
        try:
            hours = 0
            if "d" in time_str:
                days = int(time_str.split("d")[0].strip())
                hours += days * 24
            if "h" in time_str:
                h_part = time_str.split("h")[0]
                if "d" in h_part:
                    h_part = h_part.split("d")[-1].strip()
                hours += int(h_part)

            if hours < TIME_TO_RESOLUTION_MIN_HOURS:
                return False, [f"Resolves in {hours}h - too soon"]
        except (ValueError, IndexError):
            pass

    # Check liquidity (pre-filter before slippage, avoids wasting a context call)
    if MIN_LIQUIDITY_USD > 0:
        liquidity = market.get("liquidity", 0) or 0
        if liquidity < MIN_LIQUIDITY_USD:
            return False, [f"Liquidity too low: ${liquidity:.0f} < ${MIN_LIQUIDITY_USD:.0f} min"]

    # Check slippage
    estimates = slippage.get("estimates", []) if slippage else []
    if estimates:
        slippage_pct = estimates[0].get("slippage_pct", 0)
        if slippage_pct > SLIPPAGE_MAX_PCT:
            return False, [f"Slippage too high: {slippage_pct:.1%} (max {SLIPPAGE_MAX_PCT:.0%})"]

    # Check edge recommendation (if available and use_edge=True)
    if use_edge and edge:
        recommendation = edge.get("recommendation")
        user_edge = edge.get("user_edge")
        threshold = edge.get("suggested_threshold", 0)
        
        if recommendation == "SKIP":
            return False, ["Edge analysis: SKIP (market resolved or invalid)"]
        elif recommendation == "HOLD":
            if user_edge is not None and threshold:
                reasons.append(f"Edge {user_edge:.1%} below threshold {threshold:.1%} - marginal opportunity")
            else:
                reasons.append("Edge analysis recommends HOLD")
        elif recommendation == "TRADE":
            reasons.append(f"Edge {user_edge:.1%} ≥ threshold {threshold:.1%} - good opportunity")

    return True, reasons


def detect_price_trend(history: list) -> dict:
    """
    Analyze price history for trends.
    Returns: {direction: "up"/"down"/"flat", change_24h: float, is_opportunity: bool}
    """
    if not history or len(history) < 2:
        return {"direction": "unknown", "change_24h": 0, "is_opportunity": False}

    # Get recent and older prices
    recent_price = history[-1].get("price_yes", 0.5)
    
    # Find price ~24h ago (assuming 15-min intervals, ~96 points)
    lookback = min(96, len(history) - 1)
    old_price = history[-lookback].get("price_yes", recent_price)

    if old_price == 0:
        return {"direction": "unknown", "change_24h": 0, "is_opportunity": False}

    change = (recent_price - old_price) / old_price

    if change < -PRICE_DROP_THRESHOLD:
        return {"direction": "down", "change_24h": change, "is_opportunity": True}
    elif change > PRICE_DROP_THRESHOLD:
        return {"direction": "up", "change_24h": change, "is_opportunity": False}
    else:
        return {"direction": "flat", "change_24h": change, "is_opportunity": False}


# =============================================================================
# Volatility Targeting
# =============================================================================

import math

def calculate_ewma_vol(history: list, span: int = 10) -> float | None:
    """
    Calculate annualized EWMA volatility from price history points.

    Uses log returns of YES prices with exponentially weighted moving average.
    Returns annualized volatility as a decimal (e.g. 0.25 = 25%), or None if
    insufficient data.

    Args:
        history: List of dicts with 'price_yes' key (from get_price_history)
        span: EWMA span — lower values weight recent data more heavily
    """
    prices = [p.get("price_yes") or 0 for p in history]
    # Filter out zero/near-zero prices that would break log returns
    prices = [p for p in prices if p > 0.001]
    if len(prices) < span + 5:
        return None

    # Log returns
    log_returns = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
    if not log_returns:
        return None

    # EWMA variance (exponentially weighted moving average of squared deviations)
    alpha = 2.0 / (span + 1)
    ewma_var = log_returns[0] ** 2  # seed with first squared return
    for r in log_returns[1:]:
        ewma_var = alpha * (r ** 2) + (1 - alpha) * ewma_var

    ewma_std = math.sqrt(ewma_var)

    # Annualize: price history is ~15-min intervals, ~96 per day, 365 days
    # sqrt(96 * 365) ≈ 187.2
    intervals_per_day = 96
    annualized = ewma_std * math.sqrt(intervals_per_day * 365)
    return annualized


def apply_vol_targeting(base_size: float, current_vol: float | None,
                        target_vol: float = TARGET_VOL,
                        max_leverage: float = VOL_MAX_LEVERAGE,
                        min_allocation: float = VOL_MIN_ALLOCATION) -> tuple:
    """
    Apply volatility targeting multiplier to base position size.

    Returns (adjusted_size, metadata_dict).
    Falls back to base_size if vol data is unavailable.
    """
    meta = {"vol_targeting": True, "base_size": base_size, "current_vol": current_vol,
            "target_vol": target_vol}

    if current_vol is None or current_vol <= 0:
        meta["adjusted_for"] = "no_vol_data"
        meta["leverage"] = 1.0
        return base_size, meta

    raw_leverage = target_vol / current_vol
    leverage = max(min_allocation, min(raw_leverage, max_leverage))

    if leverage == min_allocation:
        meta["adjusted_for"] = "min_allocation_floor"
    elif leverage == max_leverage:
        meta["adjusted_for"] = "max_leverage_cap"
    else:
        meta["adjusted_for"] = "volatility_target"

    meta["raw_leverage"] = round(raw_leverage, 3)
    meta["leverage"] = round(leverage, 3)

    return round(base_size * leverage, 2), meta


# =============================================================================
# Market Discovery - Auto-import from Polymarket
# =============================================================================
# NOTE: Unlike fastloop (which queries Gamma API directly with tag=crypto),
# weather uses Simmer's list_importable_markets (Dome-backed keyword search).
# Gamma API has no weather/temperature tag and no public text search endpoint
# (/search requires auth). Tested Feb 2026: 600+ events paginated, zero weather.
# This path is slower but is the only way to discover weather markets by keyword.
# Trading does NOT depend on discovery — v1.10.1+ trades from already-imported
# markets via GET /api/sdk/markets?tags=weather.
# =============================================================================

# Search terms per location (matching Polymarket event naming)
LOCATION_SEARCH_TERMS = {
    "NYC": ["temperature new york", "temperature nyc"],
    "Chicago": ["temperature chicago"],
    "Seattle": ["temperature seattle"],
    "Atlanta": ["temperature atlanta"],
    "Dallas": ["temperature dallas"],
    "Miami": ["temperature miami"],
}


def discover_and_import_weather_markets(log=print):
    """Discover weather markets on Polymarket and auto-import to Simmer.

    Searches the importable markets endpoint for weather events matching
    ACTIVE_LOCATIONS, then imports any that aren't already in Simmer.

    Returns count of newly imported markets.
    """
    client = get_client()
    imported_count = 0
    seen_urls = set()

    for location in ACTIVE_LOCATIONS:
        search_terms = LOCATION_SEARCH_TERMS.get(location, [f"temperature {location.lower()}"])

        for term in search_terms:
            try:
                results = client.list_importable_markets(
                    q=term, venue="polymarket", min_volume=1000, limit=20
                )
            except Exception as e:
                log(f"  Discovery search failed for '{term}': {e}")
                continue

            for m in results:
                url = m.get("url", "")
                question = (m.get("question") or "").lower()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                # Filter: must be a temperature market on Polymarket
                if "temperature" not in question:
                    continue
                if not url.startswith("https://polymarket.com/"):
                    continue

                # Try to import
                try:
                    result = client.import_market(url)
                    status = result.get("status", "") if result else ""
                    if status == "imported":
                        imported_count += 1
                        log(f"  Imported: {m.get('question', url)[:70]}")
                    elif status == "already_exists":
                        pass  # Expected for most
                except Exception as e:
                    err_str = str(e)
                    if "rate limit" in err_str.lower() or "429" in err_str:
                        log(f"  Import rate limit reached — stopping discovery")
                        return imported_count
                    log(f"  Import failed for {url[:50]}: {e}")

    return imported_count


# =============================================================================
# Simmer API - Trading
# =============================================================================

def fetch_weather_markets():
    """Fetch weather-tagged markets from Simmer API.

    Requests `resolution_criteria` so we can route each market to the
    specific station Polymarket actually reads (KDAL vs KDFW, KORD vs KMDW,
    LIMC vs LIML, etc.) instead of trusting a city → station hardcode.
    """
    try:
        result = get_client()._request(
            "GET", "/api/sdk/markets",
            params={
                "tags": "weather",
                "status": "active",
                "limit": 100,
                "include": "resolution_criteria",
            },
        )
        return result.get("markets", [])
    except Exception:
        print("  Failed to fetch markets from Simmer API")
        return []


def execute_trade(market_id: str, side: str, amount: float, reasoning: str = None, signal_data: dict = None) -> dict:
    """Execute a buy trade via Simmer SDK with source tagging."""
    try:
        result = get_client().trade(
            market_id=market_id, side=side, amount=amount, source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
            reasoning=reasoning, signal_data=signal_data, order_type=ORDER_TYPE,
        )
        out = {
            "success": result.success, "trade_id": result.trade_id,
            "shares_bought": result.shares_bought, "shares": result.shares_bought,
            "error": result.error, "simulated": result.simulated,
            "order_status": result.order_status,
        }
        if result.order_status == "live":
            print(f"  [GTC] Order placed on book — waiting for fill (trade {result.trade_id})")
        return out
    except Exception as e:
        return {"error": str(e)}


def execute_sell(market_id: str, shares: float) -> dict:
    """Execute a sell trade via Simmer SDK with source tagging."""
    try:
        result = get_client().trade(
            market_id=market_id, side="yes", action="sell",
            shares=shares, source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
            order_type=ORDER_TYPE,
        )
        out = {
            "success": result.success, "trade_id": result.trade_id,
            "error": result.error, "simulated": result.simulated,
            "order_status": result.order_status,
        }
        if result.order_status == "live":
            print(f"  [GTC] Sell order placed on book — waiting for fill (trade {result.trade_id})")
        return out
    except Exception as e:
        return {"error": str(e)}


def get_positions(venue: str = None) -> list:
    """Get current positions as list of dicts, filtered by venue."""
    try:
        client = get_client()
        # Default to the client's configured venue to avoid cross-venue positions
        effective_venue = venue or client.venue
        positions = client.get_positions(venue=effective_venue)
        from dataclasses import asdict
        return [asdict(p) for p in positions]
    except Exception as e:
        print(f"  Error fetching positions: {e}")
        return []


def calculate_position_size(default_size: float, smart_sizing: bool) -> float:
    """Calculate position size based on portfolio or fall back to default."""
    if not smart_sizing:
        return default_size

    portfolio = get_portfolio()
    if not portfolio:
        print(f"  ⚠️  Smart sizing failed, using default ${default_size:.2f}")
        return default_size

    balance = portfolio.get("balance_usdc", 0)
    if balance <= 0:
        print(f"  ⚠️  No available balance, using default ${default_size:.2f}")
        return default_size

    smart_size = balance * SMART_SIZING_PCT
    smart_size = min(smart_size, MAX_POSITION_USD)
    smart_size = max(smart_size, 1.0)

    print(f"  💡 Smart sizing: ${smart_size:.2f} ({SMART_SIZING_PCT:.0%} of ${balance:.2f} balance)")
    return smart_size


# =============================================================================
# Exit Strategy
# =============================================================================

def check_exit_opportunities(dry_run: bool = False, use_safeguards: bool = True) -> tuple:
    """Check open positions for exit opportunities. Returns: (exits_found, exits_executed)"""
    positions = get_positions()

    if not positions:
        return 0, 0

    weather_positions = []
    for pos in positions:
        question = pos.get("question", "").lower()
        sources = pos.get("sources", [])
        # Check if from weather skill OR has weather keywords
        if TRADE_SOURCE in sources or any(kw in question for kw in ["temperature", "°f", "highest temp", "lowest temp"]):
            weather_positions.append(pos)

    if not weather_positions:
        return 0, 0

    print(f"\n📈 Checking {len(weather_positions)} weather positions for exit...")

    exits_found = 0
    exits_executed = 0

    for pos in weather_positions:
        market_id = pos.get("market_id")
        current_price = pos.get("current_price") or pos.get("price_yes") or 0
        shares = pos.get("shares_yes") or pos.get("shares") or 0
        question = pos.get("question", "Unknown")[:50]

        if shares < MIN_SHARES_PER_ORDER:
            continue

        if current_price >= EXIT_THRESHOLD:
            exits_found += 1
            print(f"  📤 {question}...")
            print(f"     Price ${current_price:.2f} >= exit threshold ${EXIT_THRESHOLD:.2f}")

            # Check safeguards before selling
            if use_safeguards:
                context = get_market_context(market_id)
                should_trade, reasons = check_context_safeguards(context)
                if not should_trade:
                    print(f"     ⏭️  Skipped: {'; '.join(reasons)}")
                    continue
                if reasons:
                    print(f"     ⚠️  Warnings: {'; '.join(reasons)}")

            # Re-fetch fresh share count to avoid selling more than available
            fresh_positions = get_positions()
            fresh_pos = next((p for p in fresh_positions if p.get("market_id") == market_id), None)
            if fresh_pos:
                fresh_shares = fresh_pos.get("shares_yes") or fresh_pos.get("shares") or 0
                if fresh_shares < MIN_SHARES_PER_ORDER:
                    print(f"     ⏭️  Skipped: fresh share count {fresh_shares:.1f} below minimum")
                    continue
                if fresh_shares != shares:
                    print(f"     ℹ️  Share count updated: {shares:.1f} → {fresh_shares:.1f}")
                    shares = fresh_shares

            tag = "SIMULATED" if dry_run else "LIVE"
            print(f"     Selling {shares:.1f} shares ({tag})...")
            result = execute_sell(market_id, shares)

            if result.get("success"):
                exits_executed += 1
                trade_id = result.get("trade_id")
                print(f"     ✅ {'[PAPER] ' if result.get('simulated') else ''}Sold {shares:.1f} shares @ ${current_price:.2f}")

                # Log sell trade context for journal (skip for paper trades)
                if trade_id and JOURNAL_AVAILABLE and not result.get("simulated"):
                    log_trade(
                        trade_id=trade_id,
                        source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
                        thesis=f"Exit: price ${current_price:.2f} reached exit threshold ${EXIT_THRESHOLD:.2f}",
                        action="sell",
                    )
            else:
                error = result.get("error", "Unknown error")
                print(f"     ❌ Sell failed: {error}")
        else:
            print(f"  📊 {question}...")
            print(f"     Price ${current_price:.2f} < exit threshold ${EXIT_THRESHOLD:.2f} - hold")

    return exits_found, exits_executed


# =============================================================================
# Main Strategy Logic
# =============================================================================

def run_weather_strategy(dry_run: bool = True, positions_only: bool = False,
                         show_config: bool = False, smart_sizing: bool = False,
                         use_safeguards: bool = True, use_trends: bool = True,
                         quiet: bool = False, vol_targeting: bool = VOL_TARGETING):
    """Run the weather trading strategy."""
    # Globals declared up-front: balance pre-flight (below) may cap MAX_POSITION_USD.
    global MAX_POSITION_USD

    def log(msg, force=False):
        """Print unless quiet mode is on. force=True always prints."""
        if not quiet or force:
            print(msg)

    log("🌤️  Simmer Weather Trading Skill")
    log("=" * 50)

    if dry_run:
        log("\n  [PAPER MODE] Trades will be simulated with real prices. Use --live for real trades.")

    log(f"\n⚙️  Configuration:")
    log(f"  Entry threshold: {ENTRY_THRESHOLD:.0%} (buy below this)")
    log(f"  Exit threshold:  {EXIT_THRESHOLD:.0%} (sell above this)")
    log(f"  Max position:    ${MAX_POSITION_USD:.2f}")
    log(f"  Max trades/run:  {MAX_TRADES_PER_RUN}")
    log(f"  Locations:       {', '.join(ACTIVE_LOCATIONS)}")
    log(f"  Smart sizing:    {'✓ Enabled' if smart_sizing else '✗ Disabled'}")
    log(f"  Safeguards:      {'✓ Enabled' if use_safeguards else '✗ Disabled'}")
    log(f"  Trend detection: {'✓ Enabled' if use_trends else '✗ Disabled'}")
    log(f"  Vol targeting:   {'✓ Enabled' if vol_targeting else '✗ Disabled'}")
    if vol_targeting:
        log(f"    Target vol:    {TARGET_VOL:.0%} annualized")
        log(f"    Max leverage:  {VOL_MAX_LEVERAGE:.1f}x")
        log(f"    Min alloc:     {VOL_MIN_ALLOCATION:.0%}")
        log(f"    EWMA span:     {VOL_SPAN}")

    if show_config:
        config_path = get_config_path(__file__)
        log(f"\n  Config file: {config_path}")
        log(f"  Config exists: {'Yes' if config_path.exists() else 'No'}")
        log("\n  To change settings, either:")
        log("  1. Create/edit config.json in skill directory:")
        log('     {"entry_threshold": 0.20, "exit_threshold": 0.50, "locations": "NYC,Chicago"}')
        log("  2. Or use --set flag:")
        log("     python weather_trader.py --set entry_threshold=0.20")
        log("  3. Or set environment variables (lowest priority):")
        log("     SIMMER_WEATHER_ENTRY=0.20")
        return

    # Initialize client early to validate API key
    client = get_client(live=not dry_run)

    # Redeem any winning positions before starting the cycle
    try:
        redeemed = client.auto_redeem()
        for r in redeemed:
            if r.get("success"):
                log(f"  💰 Redeemed {r['market_id'][:8]}... ({r.get('side', '?')})")
    except Exception:
        pass  # Non-critical — don't block trading

    # Balance pre-flight: skip cleanly when wallet is underfunded instead of
    # looping on rejected trades. Helper is collateral-agnostic — checks pUSD
    # on V2, USDC.e on V1 per server's exchange_version.
    if not dry_run:
        _preflight = client.ensure_can_trade(min_usd=1.0)
        if not _preflight["ok"]:
            log(f"  ⏸️  insufficient_balance: ${_preflight['balance']:.2f} {_preflight['collateral']} "
                f"(need ≥ $1.00) — skip", force=True)
            return
        if _preflight["max_safe_size"] < MAX_POSITION_USD:
            log(f"  💰 Capping max bet ${MAX_POSITION_USD:.2f} → ${_preflight['max_safe_size']:.2f} "
                f"(balance ${_preflight['balance']:.2f} {_preflight['collateral']})", force=True)
            MAX_POSITION_USD = _preflight["max_safe_size"]

    # Show portfolio if smart sizing enabled
    if smart_sizing:
        log("\n💰 Portfolio:")
        portfolio = get_portfolio()
        if portfolio:
            log(f"  Balance: ${portfolio.get('balance_usdc', 0):.2f}")
            log(f"  Exposure: ${portfolio.get('total_exposure', 0):.2f}")
            log(f"  Positions: {portfolio.get('positions_count', 0)}")
            by_source = portfolio.get('by_source', {})
            if by_source:
                log(f"  By source: {json.dumps(by_source, indent=4)}")

    if positions_only:
        log("\n📊 Current Positions:")
        positions = get_positions()
        if not positions:
            log("  No open positions")
        else:
            for pos in positions:
                log(f"  • {pos.get('question', 'Unknown')[:50]}...")
                sources = pos.get('sources', [])
                log(f"    YES: {pos.get('shares_yes', 0):.1f} | NO: {pos.get('shares_no', 0):.1f} | P&L: ${pos.get('pnl', 0):.2f} | Sources: {sources}")
        return

    log("\n🔍 Discovering new weather markets on Polymarket...")
    newly_imported = discover_and_import_weather_markets(log=log)
    if newly_imported:
        log(f"  Auto-imported {newly_imported} new market(s)")
    else:
        log("  No new markets to import")

    log("\n📡 Fetching weather markets...")
    markets = fetch_weather_markets()
    log(f"  Found {len(markets)} weather markets")

    if not markets:
        log("  No weather markets available")
        return

    events = {}
    for market in markets:
        # Group by event_id if available, otherwise derive from question
        event_key = market.get("event_id")
        if not event_key:
            # Fall back: parse question to derive (location, date) grouping key
            info = parse_weather_event(market.get("event_name") or market.get("question", ""))
            event_key = f"{info['location']}_{info['date']}" if info else "unknown"
        if event_key not in events:
            events[event_key] = []
        events[event_key].append(market)

    log(f"  Grouped into {len(events)} events")

    forecast_cache = {}
    trades_executed = 0
    total_usd_spent = 0.0
    opportunities_found = 0
    skip_reasons = []
    execution_errors = []

    for event_id, event_markets in events.items():
        # Use event_name from API if available, otherwise parse from question
        event_name = event_markets[0].get("event_name") or event_markets[0].get("question", "")
        event_info = parse_weather_event(event_name)

        if not event_info:
            continue

        location = event_info["location"]
        date_str = event_info["date"]
        metric = event_info["metric"]

        if location.upper() not in ACTIVE_LOCATIONS:
            continue

        # Skip range-bucket events (multi-outcome) if binary_only is set
        if BINARY_ONLY and len(event_markets) > 2:
            log(f"  ⏭️  Skipping range event ({len(event_markets)} outcomes) — binary_only=true")
            continue

        log(f"\n📍 {location} {date_str} ({metric} temp)")

        # Resolve the actual oracle station for this event from
        # resolution_criteria (the source of truth Polymarket publishes).
        # All markets in the event share the same oracle, so we read the
        # first market's criteria. If the criteria is missing or names a
        # station we don't know, skip the event with a log line — we'd
        # rather skip than trade against the wrong forecast (which is the
        # bug this code path replaces).
        sample_criteria = event_markets[0].get("resolution_criteria", "")
        parsed = parse_resolution_station(sample_criteria)
        if not parsed:
            log(f"  ⏭️  Skipping — no resolution_criteria on market (need SDK ≥ 2026-05-03)")
            skip_reasons.append("no resolution_criteria")
            continue

        station_id = parsed.get("station_id")
        station_name = parsed.get("station_name") or station_id or "?"

        # Route forecast source by station_id. Cache key is the station_id
        # itself (not the city) so multi-airport cities like NYC/Chicago/
        # Milan/Tokyo/Seoul get distinct forecasts per airport. Unknown
        # stations are skipped (no city-default fallback — that's how the
        # original Dallas bug hid).
        is_international = False
        if station_id and station_id in STATION_ID_TO_NOAA:
            log(f"  Oracle: {station_name} ({station_id}) → NOAA")
        elif station_id and station_id in INTERNATIONAL_STATION_COORDS:
            is_international = True
            _intl_city = INTERNATIONAL_STATION_COORDS[station_id]["city"]
            log(f"  Oracle: {station_name} ({station_id}) → Open-Meteo @ airport coords ({_intl_city})")
        else:
            log(f"  ⏭️  Skipping — station {station_id or 'unknown'} ({station_name}) not in NOAA/Open-Meteo maps")
            skip_reasons.append(f"unknown station {station_id or station_name}")
            continue

        cache_key = station_id  # station-keyed so per-airport forecasts don't collide
        temp_unit = event_info.get("unit", "F")

        if cache_key not in forecast_cache:
            if is_international:
                log(f"  Fetching Open-Meteo forecast for {cache_key}...")
                raw = get_openmeteo_forecast_for_station(cache_key)
                # Normalise to {"high": temp, "low": temp} using Celsius keys
                forecast_cache[cache_key] = {
                    d: {"high": v.get("high_c"), "low": v.get("low_c")}
                    for d, v in raw.items()
                }
            else:
                log(f"  Fetching NOAA forecast for {cache_key}...")
                forecast_cache[cache_key] = get_noaa_forecast_for_station(cache_key)

        forecasts = forecast_cache[cache_key]
        day_forecast = forecasts.get(date_str, {})
        forecast_temp = day_forecast.get(metric)

        if forecast_temp is None:
            log(f"  ⚠️  No forecast available for {date_str}")
            continue

        unit_label = "°C" if is_international else "°F"
        source_label = "Open-Meteo" if is_international else "NOAA"
        log(f"  {source_label} forecast: {forecast_temp}{unit_label}")

        matching_market = None
        for market in event_markets:
            outcome_name = market.get("outcome_name") or market.get("question", "")
            bucket = parse_temperature_bucket(outcome_name)

            if bucket and bucket[0] <= forecast_temp <= bucket[1]:
                matching_market = market
                break

        if not matching_market:
            log(f"  ⚠️  No bucket found for {forecast_temp}{unit_label}")
            continue

        outcome_name = matching_market.get("outcome_name", "")
        price = matching_market.get("external_price_yes") or 0.5
        market_id = matching_market.get("id")

        log(f"  Matching bucket: {outcome_name} @ ${price:.2f}")

        if price < MIN_TICK_SIZE:
            log(f"  ⏸️  Price ${price:.4f} below min tick ${MIN_TICK_SIZE} - skip (market at extreme)")
            skip_reasons.append("price at extreme")
            continue
        if price > (1 - MIN_TICK_SIZE):
            log(f"  ⏸️  Price ${price:.4f} above max tradeable - skip (market at extreme)")
            skip_reasons.append("price at extreme")
            continue

        # Check safeguards with edge analysis
        # NOAA forecasts are ~85% accurate for 1-2 day predictions when in-bucket
        noaa_probability = 0.85
        if use_safeguards:
            context = get_market_context(market_id, my_probability=noaa_probability)
            should_trade, reasons = check_context_safeguards(context)
            if not should_trade:
                log(f"  ⏭️  Safeguard blocked: {'; '.join(reasons)}")
                skip_reasons.append(f"safeguard: {reasons[0]}")
                continue
            if reasons:
                log(f"  ⚠️  Warnings: {'; '.join(reasons)}")

        # Fetch price history once — used for both trend detection and vol targeting
        history = []
        if use_trends or vol_targeting:
            history = get_price_history(market_id)

        # Check price trend
        trend_bonus = ""
        if use_trends and history:
            trend = detect_price_trend(history)
            if trend["is_opportunity"]:
                trend_bonus = f" 📉 (dropped {abs(trend['change_24h']):.0%} in 24h - stronger signal!)"
            elif trend["direction"] == "up":
                trend_bonus = f" 📈 (up {trend['change_24h']:.0%} in 24h)"

        if price < ENTRY_THRESHOLD:
            position_size = calculate_position_size(MAX_POSITION_USD, smart_sizing)

            # Apply volatility targeting
            vol_meta = None
            if vol_targeting and history:
                current_vol = calculate_ewma_vol(history, span=VOL_SPAN)
                position_size, vol_meta = apply_vol_targeting(
                    position_size, current_vol,
                    target_vol=TARGET_VOL,
                    max_leverage=VOL_MAX_LEVERAGE,
                    min_allocation=VOL_MIN_ALLOCATION,
                )
                if current_vol is not None:
                    log(f"  📊 Vol targeting: realized={current_vol:.0%} target={TARGET_VOL:.0%} → {vol_meta['leverage']:.2f}x (${position_size:.2f})")
                else:
                    log(f"  📊 Vol targeting: insufficient price data — using base size")

            min_cost_for_shares = MIN_SHARES_PER_ORDER * price
            if min_cost_for_shares > position_size:
                log(f"  ⚠️  Position size ${position_size:.2f} too small for {MIN_SHARES_PER_ORDER} shares at ${price:.2f}")
                skip_reasons.append("position too small")
                continue

            opportunities_found += 1
            log(f"  ✅ Below threshold (${ENTRY_THRESHOLD:.2f}) - BUY opportunity!{trend_bonus}")

            # Check rate limit
            if trades_executed >= MAX_TRADES_PER_RUN:
                log(f"  ⏸️  Max trades per run ({MAX_TRADES_PER_RUN}) reached - skipping")
                skip_reasons.append("max trades reached")
                continue

            tag = "SIMULATED" if dry_run else "LIVE"
            log(f"  Executing trade ({tag})...", force=True)
            edge = noaa_probability - price
            signal = {
                    "edge": round(edge, 4),
                    "confidence": noaa_probability,
                    "signal_source": "noaa_forecast",
                    "forecast_temp": forecast_temp,
                    "bucket_range": outcome_name,
                    "market_price": round(price, 4),
                    "threshold": ENTRY_THRESHOLD,
            }
            if vol_meta:
                signal["vol_targeting"] = vol_meta
            result = execute_trade(
                market_id, "yes", position_size,
                reasoning=f"NOAA forecasts {forecast_temp}{unit_label} → bucket {outcome_name} underpriced at {price:.0%}",
                signal_data=signal,
            )

            if result.get("success"):
                trades_executed += 1
                total_usd_spent += position_size
                shares = result.get("shares_bought") or result.get("shares") or 0
                trade_id = result.get("trade_id")
                log(f"  ✅ {'[PAPER] ' if result.get('simulated') else ''}Bought {shares:.1f} shares @ ${price:.2f}", force=True)

                # Log trade context for journal (skip for paper trades)
                if trade_id and JOURNAL_AVAILABLE and not result.get("simulated"):
                    # Confidence based on price gap from threshold (guard against div by zero)
                    if ENTRY_THRESHOLD > 0:
                        confidence = min(0.95, (ENTRY_THRESHOLD - price) / ENTRY_THRESHOLD + 0.5)
                    else:
                        confidence = 0.7  # Default confidence if threshold is zero
                    log_trade(
                        trade_id=trade_id,
                        source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
                        thesis=f"{'Open-Meteo' if is_international else 'NOAA'} forecasts {forecast_temp}{unit_label} for {location} on {date_str}, "
                               f"bucket '{outcome_name}' underpriced at ${price:.2f}",
                        confidence=round(confidence, 2),
                        location=location,
                        forecast_temp=forecast_temp,
                        target_date=date_str,
                        metric=metric,
                    )
                # Risk monitors are now auto-set via SDK settings (dashboard)
            else:
                error = result.get("error", "Unknown error")
                log(f"  ❌ Trade failed: {error}", force=True)
                execution_errors.append(error[:120])
        else:
            log(f"  ⏸️  Price ${price:.2f} above threshold ${ENTRY_THRESHOLD:.2f} - skip")

    exits_found, exits_executed = check_exit_opportunities(dry_run, use_safeguards)

    log("\n" + "=" * 50)
    total_trades = trades_executed + exits_executed
    show_summary = not quiet or total_trades > 0
    if show_summary:
        print("📊 Summary:")
        print(f"  Events scanned: {len(events)}")
        print(f"  Entry opportunities: {opportunities_found}")
        print(f"  Exit opportunities:  {exits_found}")
        print(f"  Trades executed:     {total_trades}")

    if dry_run and show_summary:
        print("\n  [PAPER MODE - trades simulated with real prices]")


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simmer Weather Trading Skill")
    parser.add_argument("--live", action="store_true", help="Execute real trades (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", help="(Default) Show opportunities without trading")
    parser.add_argument("--positions", action="store_true", help="Show current positions only")
    parser.add_argument("--config", action="store_true", help="Show current config")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="Set config value (e.g., --set entry_threshold=0.20)")
    parser.add_argument("--smart-sizing", action="store_true", help="Use portfolio-based position sizing")
    parser.add_argument("--no-safeguards", action="store_true", help="Disable context safeguards")
    parser.add_argument("--no-trends", action="store_true", help="Disable price trend detection")
    parser.add_argument("--vol-targeting", action="store_true", help="Enable volatility targeting (dynamic position sizing based on realized vol)")
    parser.add_argument("--quiet", "-q", action="store_true", help="Only output when trades execute or errors occur (ideal for high-frequency runs)")
    args = parser.parse_args()

    # Handle --set config updates
    if args.set:
        updates = {}
        for item in args.set:
            if "=" in item:
                key, value = item.split("=", 1)
                # Try to convert to appropriate type
                if key in CONFIG_SCHEMA:
                    type_fn = CONFIG_SCHEMA[key].get("type", str)
                    try:
                        value = type_fn(value)
                    except (ValueError, TypeError):
                        pass
                updates[key] = value
        if updates:
            updated = update_config(updates, __file__)
            print(f"✅ Config updated: {updates}")
            print(f"   Saved to: {get_config_path(__file__)}")
            # Reload config
            _config = load_config(CONFIG_SCHEMA, __file__, slug="polymarket-weather-trader")
            # Update module-level vars
            globals()["ENTRY_THRESHOLD"] = _config["entry_threshold"]
            globals()["EXIT_THRESHOLD"] = _config["exit_threshold"]
            globals()["MAX_POSITION_USD"] = _config["max_position_usd"]
            globals()["SMART_SIZING_PCT"] = _config["sizing_pct"]
            globals()["MAX_TRADES_PER_RUN"] = _config["max_trades_per_run"]
            globals()["BINARY_ONLY"] = _config["binary_only"]
            globals()["VOL_TARGETING"] = _config["vol_targeting"]
            globals()["TARGET_VOL"] = _config["target_vol"]
            globals()["VOL_MAX_LEVERAGE"] = _config["vol_max_leverage"]
            globals()["VOL_MIN_ALLOCATION"] = _config["vol_min_allocation"]
            globals()["VOL_SPAN"] = _config["vol_span"]
            _locations_str = _config["locations"]
            globals()["ACTIVE_LOCATIONS"] = [loc.strip().upper() for loc in _locations_str.split(",") if loc.strip()]

    # Default to dry-run unless --live is explicitly passed
    dry_run = not args.live

    run_weather_strategy(
        dry_run=dry_run,
        positions_only=args.positions,
        show_config=args.config,
        smart_sizing=args.smart_sizing,
        use_safeguards=not args.no_safeguards,
        use_trends=not args.no_trends,
        quiet=args.quiet,
        vol_targeting=args.vol_targeting or VOL_TARGETING,
    )
