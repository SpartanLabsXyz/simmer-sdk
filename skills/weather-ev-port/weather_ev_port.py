#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Simmer Weather-EV Trading Skill (work-in-progress port)

EV-gated + fractional-Kelly weather trader for Polymarket. Uses ECMWF/HRRR forecasts
(Open-Meteo) and METAR observations, scores mispriced temperature buckets, sizes with
fractional Kelly, enters with pre-entry live-ask revalidation, manages exits with 20%
stop + trailing-stop-to-breakeven + forecast-change exit.

Strategy math + forecast pipeline + airport-station city table derived from
alteregoeth-ai/weatherbot (MIT). See ATTRIBUTION.md and LICENSE.UPSTREAM.

This is a WORK-IN-PROGRESS port. Placeholder slug `weather-ev-port`. Not published.

Usage:
    python weather_ev_port.py            # Dry run — show opportunities, no trades
    python weather_ev_port.py --live     # Execute real trades via Simmer SDK
    python weather_ev_port.py --positions  # Show current Simmer positions only
    python weather_ev_port.py status     # Local balance + open positions from state.json
    python weather_ev_port.py report     # Full resolved-market report

Requires:
    SIMMER_API_KEY environment variable (https://simmer.markets/dashboard -> SDK)
    TRADING_VENUE=sim  (recommended for initial dogfood — paper trading on Simmer LMSR)
"""

import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Force line-buffered stdout so output is visible in non-TTY environments.
sys.stdout.reconfigure(line_buffering=True)

# =============================================================================
# CONFIG — env vars > config.json > defaults
# =============================================================================

from simmer_sdk.skill import load_config

CONFIG_SCHEMA = {
    "min_ev":           {"env": "SIMMER_WEATHER_EV_MIN_EV",           "default": 0.05,   "type": float,
                          "help": "Minimum expected value to enter. AlterEgo's bot uses 0.05-0.10."},
    "max_price":        {"env": "SIMMER_WEATHER_EV_MAX_PRICE",        "default": 0.45,   "type": float,
                          "help": "Max entry price — pay no more than this per share."},
    "kelly_fraction":   {"env": "SIMMER_WEATHER_EV_KELLY_FRACTION",   "default": 0.25,   "type": float,
                          "help": "Fractional Kelly multiplier (AlterEgo default 0.25)."},
    "max_bet_usd":      {"env": "SIMMER_WEATHER_EV_MAX_BET_USD",      "default": 20.0,   "type": float,
                          "help": "Max dollar size per trade."},
    "min_bet_usd":      {"env": "SIMMER_WEATHER_EV_MIN_BET_USD",      "default": 0.50,   "type": float,
                          "help": "Skip trades below this size."},
    "min_volume":       {"env": "SIMMER_WEATHER_EV_MIN_VOLUME",       "default": 500.0,  "type": float,
                          "help": "Skip markets below this lifetime volume."},
    "min_hours":        {"env": "SIMMER_WEATHER_EV_MIN_HOURS",        "default": 2.0,    "type": float,
                          "help": "Skip markets resolving in less than this."},
    "max_hours":        {"env": "SIMMER_WEATHER_EV_MAX_HOURS",        "default": 72.0,   "type": float,
                          "help": "Skip markets resolving more than this far out."},
    "max_slippage":     {"env": "SIMMER_WEATHER_EV_MAX_SLIPPAGE",     "default": 0.03,   "type": float,
                          "help": "Skip markets with ask-bid spread above this."},
    "stop_loss_pct":    {"env": "SIMMER_WEATHER_EV_STOP_LOSS_PCT",    "default": 0.20,   "type": float,
                          "help": "Stop-loss drawdown (0.20 = 20%)."},
    "trailing_trigger": {"env": "SIMMER_WEATHER_EV_TRAILING_TRIGGER", "default": 0.20,   "type": float,
                          "help": "Move stop to breakeven when up this much (0.20 = 20%)."},
    "balance_start":    {"env": "SIMMER_WEATHER_EV_BALANCE",          "default": 1000.0, "type": float,
                          "help": "Starting balance for Kelly sizing baseline (local bookkeeping)."},
    "locations":        {"env": "SIMMER_WEATHER_EV_LOCATIONS",        "default": "nyc,chicago,miami,dallas,seattle,atlanta", "type": str,
                          "help": "Comma-separated city slugs. Default = 6 US cities."},
    "calibration_min":  {"env": "SIMMER_WEATHER_EV_CALIBRATION_MIN",  "default": 30,     "type": int,
                          "help": "Min resolved markets per city+source before sigma update."},
    "scan_sleep_sec":   {"env": "SIMMER_WEATHER_EV_SCAN_SLEEP",       "default": 0.3,    "type": float,
                          "help": "Sleep between city scans to avoid rate limits."},
}

_config = load_config(CONFIG_SCHEMA, __file__, slug="weather-ev-port")

MIN_EV            = _config["min_ev"]
MAX_PRICE         = _config["max_price"]
KELLY_FRACTION    = _config["kelly_fraction"]
MAX_BET           = _config["max_bet_usd"]
MIN_BET           = _config["min_bet_usd"]
MIN_VOLUME        = _config["min_volume"]
MIN_HOURS         = _config["min_hours"]
MAX_HOURS         = _config["max_hours"]
MAX_SLIPPAGE      = _config["max_slippage"]
STOP_LOSS_PCT     = _config["stop_loss_pct"]
TRAILING_TRIGGER  = _config["trailing_trigger"]
BALANCE_START     = _config["balance_start"]
CALIBRATION_MIN   = _config["calibration_min"]
SCAN_SLEEP        = _config["scan_sleep_sec"]

SIGMA_F_DEFAULT = 2.0  # Fahrenheit sigma default (°F)
SIGMA_C_DEFAULT = 1.2  # Celsius sigma default (°C)

SKILL_SLUG   = "weather-ev-port"
TRADE_SOURCE = "sdk:weather-ev-port"  # convention: sdk:<skill-slug> (docs.simmer.markets/skills/building)
ORDER_TYPE   = "GTC"  # Good-till-cancelled — weather markets are illiquid

# Storage — lives next to the skill, not the user's cwd
_SKILL_DIR       = Path(__file__).parent
DATA_DIR         = _SKILL_DIR / "data"
MARKETS_DIR      = DATA_DIR / "markets"
STATE_FILE       = DATA_DIR / "state.json"
CALIBRATION_FILE = DATA_DIR / "calibration.json"
IMPORT_CACHE_FILE = DATA_DIR / "imported.json"  # Polymarket market_id → Simmer indexing state
DATA_DIR.mkdir(exist_ok=True)
MARKETS_DIR.mkdir(exist_ok=True)

# =============================================================================
# SimmerClient singleton — lazy-init so tests can import math without SDK
# =============================================================================

_client = None
_LIVE_MODE = False  # set by main() from --live flag

def get_client():
    global _client
    if _client is None:
        try:
            from simmer_sdk import SimmerClient
        except ImportError:
            print("Error: simmer-sdk not installed. Run: pip install simmer-sdk", file=sys.stderr)
            sys.exit(1)
        api_key = os.environ.get("SIMMER_API_KEY")
        if not api_key:
            print("Error: SIMMER_API_KEY environment variable not set", file=sys.stderr)
            print("Get your API key from https://simmer.markets/dashboard → SDK tab", file=sys.stderr)
            sys.exit(1)
        # Weather markets live on Polymarket. `venue=sim` (Simmer LMSR) has
        # distinct markets — it can't route Polymarket market IDs. Dogfood path
        # is `venue=polymarket` with `live=False` (SDK paper mode against real
        # Polymarket data, no USDC moves).
        venue = os.environ.get("TRADING_VENUE", "polymarket")
        _client = SimmerClient(api_key=api_key, venue=venue, live=_LIVE_MODE)
    return _client

# =============================================================================
# LOCATIONS — airport-station city table (from alteregoeth-ai/weatherbot, MIT)
# Airport coordinates matter: Polymarket weather markets resolve on specific airport
# stations, not city centers. Using city-center coords causes systematic bias.
# =============================================================================

LOCATIONS = {
    "nyc":          {"lat": 40.7772,  "lon":  -73.8726, "name": "New York City", "station": "KLGA", "unit": "F", "region": "us"},
    "chicago":      {"lat": 41.9742,  "lon":  -87.9073, "name": "Chicago",       "station": "KORD", "unit": "F", "region": "us"},
    "miami":        {"lat": 25.7959,  "lon":  -80.2870, "name": "Miami",         "station": "KMIA", "unit": "F", "region": "us"},
    "dallas":       {"lat": 32.8471,  "lon":  -96.8518, "name": "Dallas",        "station": "KDAL", "unit": "F", "region": "us"},
    "seattle":      {"lat": 47.4502,  "lon": -122.3088, "name": "Seattle",       "station": "KSEA", "unit": "F", "region": "us"},
    "atlanta":      {"lat": 33.6407,  "lon":  -84.4277, "name": "Atlanta",       "station": "KATL", "unit": "F", "region": "us"},
    "london":       {"lat": 51.5048,  "lon":    0.0495, "name": "London",        "station": "EGLC", "unit": "C", "region": "eu"},
    "paris":        {"lat": 48.9962,  "lon":    2.5979, "name": "Paris",         "station": "LFPG", "unit": "C", "region": "eu"},
    "munich":       {"lat": 48.3537,  "lon":   11.7750, "name": "Munich",        "station": "EDDM", "unit": "C", "region": "eu"},
    "ankara":       {"lat": 40.1281,  "lon":   32.9951, "name": "Ankara",        "station": "LTAC", "unit": "C", "region": "eu"},
    "seoul":        {"lat": 37.4691,  "lon":  126.4505, "name": "Seoul",         "station": "RKSI", "unit": "C", "region": "asia"},
    "tokyo":        {"lat": 35.7647,  "lon":  140.3864, "name": "Tokyo",         "station": "RJTT", "unit": "C", "region": "asia"},
    "shanghai":     {"lat": 31.1443,  "lon":  121.8083, "name": "Shanghai",      "station": "ZSPD", "unit": "C", "region": "asia"},
    "singapore":    {"lat":  1.3502,  "lon":  103.9940, "name": "Singapore",     "station": "WSSS", "unit": "C", "region": "asia"},
    "lucknow":      {"lat": 26.7606,  "lon":   80.8893, "name": "Lucknow",       "station": "VILK", "unit": "C", "region": "asia"},
    "tel-aviv":     {"lat": 32.0114,  "lon":   34.8867, "name": "Tel Aviv",      "station": "LLBG", "unit": "C", "region": "asia"},
    "toronto":      {"lat": 43.6772,  "lon":  -79.6306, "name": "Toronto",       "station": "CYYZ", "unit": "C", "region": "ca"},
    "sao-paulo":    {"lat": -23.4356, "lon":  -46.4731, "name": "Sao Paulo",     "station": "SBGR", "unit": "C", "region": "sa"},
    "buenos-aires": {"lat": -34.8222, "lon":  -58.5358, "name": "Buenos Aires",  "station": "SAEZ", "unit": "C", "region": "sa"},
    "wellington":   {"lat": -41.3272, "lon":  174.8052, "name": "Wellington",    "station": "NZWN", "unit": "C", "region": "oc"},
}

TIMEZONES = {
    "nyc": "America/New_York", "chicago": "America/Chicago",
    "miami": "America/New_York", "dallas": "America/Chicago",
    "seattle": "America/Los_Angeles", "atlanta": "America/New_York",
    "london": "Europe/London", "paris": "Europe/Paris",
    "munich": "Europe/Berlin", "ankara": "Europe/Istanbul",
    "seoul": "Asia/Seoul", "tokyo": "Asia/Tokyo",
    "shanghai": "Asia/Shanghai", "singapore": "Asia/Singapore",
    "lucknow": "Asia/Kolkata", "tel-aviv": "Asia/Jerusalem",
    "toronto": "America/Toronto", "sao-paulo": "America/Sao_Paulo",
    "buenos-aires": "America/Argentina/Buenos_Aires", "wellington": "Pacific/Auckland",
}

MONTHS = ["january", "february", "march", "april", "may", "june",
          "july", "august", "september", "october", "november", "december"]

def active_locations():
    """Resolve `locations` config into a {slug: meta} subset."""
    slugs = [s.strip().lower() for s in (_config.get("locations") or "").split(",") if s.strip()]
    return {s: LOCATIONS[s] for s in slugs if s in LOCATIONS}

# =============================================================================
# MATH — derived line-for-line from alteregoeth-ai/weatherbot (MIT)
# Unit-tested in tests/test_strategy.py.
# =============================================================================

def norm_cdf(x):
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

def bucket_prob(forecast, t_low, t_high, sigma=None):
    """Probability that the realized temperature falls in [t_low, t_high].

    - Regular buckets (both bounds finite): 1.0 if forecast in bucket, else 0.0.
      The gate at entry time is the bucket match; sigma enters via calibration.
    - Edge buckets (t_low = -999 or t_high = 999): normal-CDF tail.
    """
    s = sigma if sigma is not None else 2.0
    if t_low == -999:
        return norm_cdf((t_high - float(forecast)) / s)
    if t_high == 999:
        return 1.0 - norm_cdf((t_low - float(forecast)) / s)
    return 1.0 if in_bucket(forecast, t_low, t_high) else 0.0

def calc_ev(p, price):
    """Expected value of buying at `price` when true probability is `p`."""
    if price <= 0 or price >= 1:
        return 0.0
    return round(p * (1.0 / price - 1.0) - (1.0 - p), 4)

def calc_kelly(p, price):
    """Fractional-Kelly bet fraction. Returns non-negative fraction of bankroll."""
    if price <= 0 or price >= 1:
        return 0.0
    b = 1.0 / price - 1.0
    f = (p * b - (1.0 - p)) / b
    return round(min(max(0.0, f) * KELLY_FRACTION, 1.0), 4)

def bet_size(kelly, balance):
    """Convert Kelly fraction to dollar size, capped by MAX_BET."""
    raw = kelly * balance
    return round(min(raw, MAX_BET), 2)

def in_bucket(forecast, t_low, t_high):
    """Whether `forecast` falls inside [t_low, t_high]."""
    if t_low == t_high:
        return round(float(forecast)) == round(t_low)
    return t_low <= float(forecast) <= t_high

# =============================================================================
# CALIBRATION — stub: load + lookup. Updater (run_calibration) is deferred to a
# follow-up ticket so we don't land it untested.
# =============================================================================

_cal: dict = {}

def load_calibration():
    global _cal
    if CALIBRATION_FILE.exists():
        try:
            _cal = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))
        except Exception:
            _cal = {}
    return _cal

def get_sigma(city_slug, source="ecmwf"):
    key = f"{city_slug}_{source}"
    if key in _cal:
        return _cal[key]["sigma"]
    return SIGMA_F_DEFAULT if LOCATIONS[city_slug]["unit"] == "F" else SIGMA_C_DEFAULT

# =============================================================================
# HTTP helper (stdlib, no requests dep)
# =============================================================================

def _http_get_json(url, timeout=8, retries=3, label=""):
    last_err = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "simmer-weather-ev/0.1"})
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, json.JSONDecodeError, TimeoutError) as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    if label:
        print(f"  [HTTP] {label}: {last_err}")
    return None

# =============================================================================
# FORECASTS
# =============================================================================

def get_ecmwf(city_slug, dates):
    """ECMWF via Open-Meteo with bias correction. Works for all cities."""
    loc = LOCATIONS[city_slug]
    unit = loc["unit"]
    temp_unit = "fahrenheit" if unit == "F" else "celsius"
    params = {
        "latitude": loc["lat"], "longitude": loc["lon"],
        "daily": "temperature_2m_max", "temperature_unit": temp_unit,
        "forecast_days": 7, "timezone": TIMEZONES.get(city_slug, "UTC"),
        "models": "ecmwf_ifs025", "bias_correction": "true",
    }
    url = f"https://api.open-meteo.com/v1/forecast?{urlencode(params)}"
    data = _http_get_json(url, label=f"ECMWF {city_slug}")
    if not data or "error" in data:
        return {}
    result = {}
    for date, temp in zip(data.get("daily", {}).get("time", []),
                          data.get("daily", {}).get("temperature_2m_max", [])):
        if date in dates and temp is not None:
            result[date] = round(temp, 1) if unit == "C" else round(temp)
    return result

def get_hrrr(city_slug, dates):
    """HRRR+GFS seamless via Open-Meteo. US cities, ~48h horizon."""
    loc = LOCATIONS[city_slug]
    if loc["region"] != "us":
        return {}
    params = {
        "latitude": loc["lat"], "longitude": loc["lon"],
        "daily": "temperature_2m_max", "temperature_unit": "fahrenheit",
        "forecast_days": 3, "timezone": TIMEZONES.get(city_slug, "UTC"),
        "models": "gfs_seamless",
    }
    url = f"https://api.open-meteo.com/v1/forecast?{urlencode(params)}"
    data = _http_get_json(url, label=f"HRRR {city_slug}")
    if not data or "error" in data:
        return {}
    result = {}
    for date, temp in zip(data.get("daily", {}).get("time", []),
                          data.get("daily", {}).get("temperature_2m_max", [])):
        if date in dates and temp is not None:
            result[date] = round(temp)
    return result

def get_metar(city_slug):
    """Current observed temperature from METAR station. D+0 only."""
    loc = LOCATIONS[city_slug]
    station = loc["station"]
    unit = loc["unit"]
    url = f"https://aviationweather.gov/api/data/metar?ids={station}&format=json"
    data = _http_get_json(url, label=f"METAR {city_slug}")
    if not data or not isinstance(data, list) or not data:
        return None
    temp_c = data[0].get("temp")
    if temp_c is None:
        return None
    try:
        if unit == "F":
            return round(float(temp_c) * 9 / 5 + 32)
        return round(float(temp_c), 1)
    except (TypeError, ValueError):
        return None

def take_forecast_snapshot(city_slug, dates):
    """Fetch forecasts from all sources. Returns {date: {ecmwf, hrrr, metar, best, best_source}}."""
    now_str = datetime.now(timezone.utc).isoformat()
    ecmwf = get_ecmwf(city_slug, dates)
    hrrr  = get_hrrr(city_slug, dates)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    loc = LOCATIONS[city_slug]
    d_plus_2 = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%d")

    snapshots = {}
    for date in dates:
        snap = {
            "ts":    now_str,
            "ecmwf": ecmwf.get(date),
            "hrrr":  hrrr.get(date) if date <= d_plus_2 else None,
            "metar": get_metar(city_slug) if date == today else None,
        }
        # Best forecast: HRRR for US D+0/D+1, else ECMWF
        if loc["region"] == "us" and snap["hrrr"] is not None:
            snap["best"], snap["best_source"] = snap["hrrr"], "hrrr"
        elif snap["ecmwf"] is not None:
            snap["best"], snap["best_source"] = snap["ecmwf"], "ecmwf"
        else:
            snap["best"], snap["best_source"] = None, None
        snapshots[date] = snap
    return snapshots

# =============================================================================
# POLYMARKET — Gamma API (public, read-only). Order placement goes through
# Simmer SDK / PolyNode. Gamma is only for market discovery + bestAsk/bestBid
# re-validation at entry time.
# =============================================================================

def get_polymarket_event(city_slug, month, day, year):
    slug = f"highest-temperature-in-{city_slug}-on-{month}-{day}-{year}"
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    data = _http_get_json(url, label=f"Gamma event {slug}")
    if data and isinstance(data, list) and data:
        return data[0]
    return None

def get_market_best_ask_bid(market_id):
    """Fetch current bestAsk and bestBid from Gamma. Returns (ask, bid, spread)."""
    url = f"https://gamma-api.polymarket.com/markets/{market_id}"
    data = _http_get_json(url, timeout=5, retries=2, label=f"Gamma market {market_id}")
    if not data:
        return None, None, None
    try:
        ask = float(data.get("bestAsk", 0) or 0)
        bid = float(data.get("bestBid", 0) or 0)
        spread = round(ask - bid, 4) if ask and bid else None
        return ask, bid, spread
    except (TypeError, ValueError):
        return None, None, None

def parse_temp_range(question):
    """Parse a temperature-range question into (t_low, t_high). Edge buckets use ±999."""
    if not question:
        return None
    num = r'(-?\d+(?:\.\d+)?)'
    if re.search(r'or below', question, re.IGNORECASE):
        m = re.search(num + r'[°]?[FC] or below', question, re.IGNORECASE)
        if m:
            return (-999.0, float(m.group(1)))
    if re.search(r'or higher', question, re.IGNORECASE):
        m = re.search(num + r'[°]?[FC] or higher', question, re.IGNORECASE)
        if m:
            return (float(m.group(1)), 999.0)
    m = re.search(r'between ' + num + r'-' + num + r'[°]?[FC]', question, re.IGNORECASE)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = re.search(r'be ' + num + r'[°]?[FC] on', question, re.IGNORECASE)
    if m:
        v = float(m.group(1))
        return (v, v)
    return None

def hours_to_resolution(end_date_str):
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        return max(0.0, (end - datetime.now(timezone.utc)).total_seconds() / 3600)
    except Exception:
        return 999.0

# =============================================================================
# STORAGE — per-market JSON files + shared state.json
# =============================================================================

def market_path(city_slug, date_str):
    return MARKETS_DIR / f"{city_slug}_{date_str}.json"

def load_market(city_slug, date_str):
    p = market_path(city_slug, date_str)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None

def save_market(market):
    p = market_path(market["city"], market["date"])
    p.write_text(json.dumps(market, indent=2, ensure_ascii=False), encoding="utf-8")

def load_all_markets():
    markets = []
    for f in MARKETS_DIR.glob("*.json"):
        try:
            markets.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return markets

def new_market(city_slug, date_str, event, hours):
    loc = LOCATIONS[city_slug]
    return {
        "city":               city_slug,
        "city_name":          loc["name"],
        "date":               date_str,
        "unit":               loc["unit"],
        "station":            loc["station"],
        "event_end_date":     event.get("endDate", ""),
        "hours_at_discovery": round(hours, 1),
        "status":             "open",
        "position":           None,
        "actual_temp":        None,
        "resolved_outcome":   None,
        "pnl":                None,
        "forecast_snapshots": [],
        "market_snapshots":   [],
        "all_outcomes":       [],
        "created_at":         datetime.now(timezone.utc).isoformat(),
    }

def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "balance":          BALANCE_START,
        "starting_balance": BALANCE_START,
        "total_trades":     0,
        "wins":             0,
        "losses":           0,
        "peak_balance":     BALANCE_START,
    }

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

# =============================================================================
# SDK EXECUTION — all real order placement goes through SimmerClient / PolyNode
# =============================================================================

def execute_buy(market_id, side, amount, reasoning, signal_data):
    """Place a buy via Simmer SDK. Returns dict with success, shares, error."""
    try:
        client = get_client()
        result = client.trade(
            market_id=market_id,
            side=side,
            amount=amount,
            action="buy",
            order_type=ORDER_TYPE,
            source=TRADE_SOURCE,
            skill_slug=SKILL_SLUG,
            reasoning=reasoning,
            signal_data=signal_data,
        )
        return {
            "success": result.success,
            "trade_id": result.trade_id,
            "shares": result.shares_bought,
            "error": result.error,
            "simulated": result.simulated,
            "order_status": result.order_status,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def execute_sell(market_id, side, shares, reasoning=None):
    """Sell `shares` via Simmer SDK."""
    try:
        client = get_client()
        result = client.trade(
            market_id=market_id,
            side=side,
            action="sell",
            shares=shares,
            order_type=ORDER_TYPE,
            source=TRADE_SOURCE,
            skill_slug=SKILL_SLUG,
            reasoning=reasoning,
        )
        return {
            "success": result.success,
            "trade_id": result.trade_id,
            "error": result.error,
            "simulated": result.simulated,
            "order_status": result.order_status,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

_import_cache: dict = {}

def load_import_cache():
    global _import_cache
    if IMPORT_CACHE_FILE.exists():
        try:
            _import_cache = json.loads(IMPORT_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _import_cache = {}
    return _import_cache

def save_import_cache():
    IMPORT_CACHE_FILE.write_text(json.dumps(_import_cache, indent=2), encoding="utf-8")

def ensure_market_indexed(market_id, condition_id=None, polymarket_url=None):
    """Ensure a Polymarket market is indexed by Simmer so SDK paper/live trade works.

    Returns (simmer_market_id, error). On error, simmer_market_id is None and
    error is a human-readable string. Hits cache → check (free) → import (quota).
    """
    cached = _import_cache.get(market_id)
    if cached and cached.get("simmer_market_id"):
        return cached["simmer_market_id"], None

    try:
        client = get_client()
    except SystemExit:
        return None, "SDK client unavailable"

    # Free pre-check first (no quota cost)
    try:
        kwargs = {}
        if condition_id:
            kwargs["condition_id"] = condition_id
        elif polymarket_url:
            kwargs["url"] = polymarket_url
        else:
            return None, "no condition_id or url to check"
        check = client.check_market_exists(**kwargs)
        if check and check.get("exists"):
            simmer_id = check.get("market_id")
            _import_cache[market_id] = {
                "simmer_market_id": simmer_id,
                "via": "check",
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            save_import_cache()
            return simmer_id, None
    except Exception as e:
        # Check failures are non-fatal — fall through to import attempt
        print(f"  [check] {market_id}: {e}")

    # Not indexed — attempt import (consumes quota)
    if not polymarket_url:
        return None, "no URL to import (need polymarket_url)"
    try:
        result = client.import_market(polymarket_url)
    except Exception as e:
        return None, f"import_market threw: {e}"
    if not result:
        return None, "import_market returned None"
    if result.get("error"):
        return None, result.get("error")
    status = result.get("status")
    if status not in ("imported", "already_exists"):
        # Common: status='resolved' for past markets
        return None, f"import status={status}"
    simmer_id = result.get("market_id")
    _import_cache[market_id] = {
        "simmer_market_id": simmer_id,
        "via": status,
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }
    save_import_cache()
    return simmer_id, None

def sdk_positions_for_skill():
    """List Simmer positions tagged with this skill's source."""
    try:
        client = get_client()
        from dataclasses import asdict
        positions = client.get_positions(venue=client.venue)
        pdicts = []
        for p in positions:
            d = asdict(p)
            if d.get("source") == TRADE_SOURCE or d.get("skill_slug") == SKILL_SLUG:
                pdicts.append(d)
        return pdicts
    except Exception as e:
        print(f"  Error fetching SDK positions: {e}")
        return []

# =============================================================================
# CORE SCAN LOOP
# =============================================================================

def scan_and_update(dry_run=False):
    """One scan cycle: forecasts, market discovery, entry/exit decisions.

    - dry_run=True: print opportunities, don't call SDK or persist state.
    - dry_run=False: always call SDK. Paper vs real-money is controlled by the
      module-level _LIVE_MODE flag (set by main() from --live) which is passed
      to SimmerClient(live=...). live=False → SDK paper mode; live=True → real.
    """
    load_calibration()
    load_import_cache()

    now    = datetime.now(timezone.utc)
    state  = load_state()
    balance = state["balance"]

    new_pos = closed = resolved = skipped = 0

    cities = active_locations()
    if not cities:
        print("  No active cities configured.")
        return 0, 0, 0

    for city_slug, loc in cities.items():
        unit_sym = "F" if loc["unit"] == "F" else "C"
        print(f"  -> {loc['name']}...", end=" ", flush=True)

        try:
            dates = [(now + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(4)]
            snapshots = take_forecast_snapshot(city_slug, dates)
            time.sleep(SCAN_SLEEP)
        except Exception as e:
            print(f"skipped ({e})")
            continue

        for i, date in enumerate(dates):
            dt = datetime.strptime(date, "%Y-%m-%d")
            event = get_polymarket_event(city_slug, MONTHS[dt.month - 1], dt.day, dt.year)
            if not event:
                continue

            end_date = event.get("endDate", "")
            hours = hours_to_resolution(end_date) if end_date else 0
            horizon = f"D+{i}"

            mkt = load_market(city_slug, date)
            if mkt is None:
                if hours < MIN_HOURS or hours > MAX_HOURS:
                    continue
                mkt = new_market(city_slug, date, event, hours)

            if mkt["status"] == "resolved":
                continue

            # Collect outcomes from the event. Track condition_id + slug so we
            # can route through Simmer's import layer at trade time.
            outcomes = []
            event_slug = event.get("slug") or ""
            for market in event.get("markets", []):
                question = market.get("question", "")
                mid      = str(market.get("id", ""))
                volume   = float(market.get("volume", 0) or 0)
                rng      = parse_temp_range(question)
                if not rng:
                    continue
                try:
                    prices = json.loads(market.get("outcomePrices", "[0.5,0.5]"))
                    bid = float(prices[0])
                    ask = float(prices[1]) if len(prices) > 1 else bid
                except Exception:
                    continue
                outcomes.append({
                    "question":     question,
                    "market_id":    mid,
                    "condition_id": market.get("conditionId"),
                    "polymarket_url": f"https://polymarket.com/event/{event_slug}" if event_slug else None,
                    "range":        rng,
                    "bid":          round(bid, 4),
                    "ask":          round(ask, 4),
                    "price":        round(bid, 4),
                    "spread":       round(ask - bid, 4),
                    "volume":       round(volume, 0),
                })
            outcomes.sort(key=lambda x: x["range"][0])
            mkt["all_outcomes"] = outcomes

            # Forecast + market snapshots
            snap = snapshots.get(date, {})
            mkt["forecast_snapshots"].append({
                "ts": snap.get("ts"), "horizon": horizon, "hours_left": round(hours, 1),
                "ecmwf": snap.get("ecmwf"), "hrrr": snap.get("hrrr"), "metar": snap.get("metar"),
                "best": snap.get("best"), "best_source": snap.get("best_source"),
            })
            top = max(outcomes, key=lambda x: x["price"]) if outcomes else None
            mkt["market_snapshots"].append({
                "ts": snap.get("ts"),
                "top_bucket": f"{top['range'][0]}-{top['range'][1]}{unit_sym}" if top else None,
                "top_price":  top["price"] if top else None,
            })

            forecast_temp = snap.get("best")
            best_source   = snap.get("best_source")

            # --- Stop-loss + trailing stop --------------------------------
            pos = mkt.get("position")
            if pos and pos.get("status") == "open":
                cur_bid = None
                for o in outcomes:
                    if o["market_id"] == pos["market_id"]:
                        cur_bid = o.get("bid", o["price"])
                        break
                if cur_bid is not None:
                    entry = pos["entry_price"]
                    stop  = pos.get("stop_price", entry * (1 - STOP_LOSS_PCT))
                    # Trailing: if up TRAILING_TRIGGER, move stop to breakeven
                    if cur_bid >= entry * (1 + TRAILING_TRIGGER) and stop < entry:
                        pos["stop_price"] = entry
                        pos["trailing_activated"] = True
                    if cur_bid <= stop:
                        reason = "trailing_stop" if pos.get("trailing_activated") and cur_bid >= entry * 0.99 else "stop_loss"
                        _close_position(mkt, pos, cur_bid, reason, dry_run)
                        pnl = pos.get("pnl") or 0
                        balance += pos["cost"] + pnl
                        closed += 1
                        print(f"\n    [{reason.upper()}] {loc['name']} {date} | "
                              f"entry ${entry:.3f} exit ${cur_bid:.3f} | PnL: {'+' if pnl>=0 else ''}{pnl:.2f}", end=" ")

            # --- Forecast-change exit -------------------------------------
            pos = mkt.get("position")
            if pos and pos.get("status") == "open" and forecast_temp is not None:
                old_low  = pos["bucket_low"]
                old_high = pos["bucket_high"]
                buffer = 2.0 if loc["unit"] == "F" else 1.0
                mid_bucket = (old_low + old_high) / 2 if old_low != -999 and old_high != 999 else forecast_temp
                forecast_far = abs(forecast_temp - mid_bucket) > (abs(mid_bucket - old_low) + buffer)
                if not in_bucket(forecast_temp, old_low, old_high) and forecast_far:
                    cur_bid = None
                    for o in outcomes:
                        if o["market_id"] == pos["market_id"]:
                            cur_bid = o.get("bid", o["price"])
                            break
                    if cur_bid is not None:
                        _close_position(mkt, pos, cur_bid, "forecast_changed", live, dry_run)
                        pnl = pos.get("pnl") or 0
                        balance += pos["cost"] + pnl
                        closed += 1
                        print(f"\n    [FORECAST CHG] {loc['name']} {date} | PnL: {'+' if pnl>=0 else ''}{pnl:.2f}", end=" ")

            # --- Open new position ----------------------------------------
            if not mkt.get("position") and forecast_temp is not None and hours >= MIN_HOURS:
                sigma = get_sigma(city_slug, best_source or "ecmwf")
                matched = None
                for o in outcomes:
                    t_low, t_high = o["range"]
                    if in_bucket(forecast_temp, t_low, t_high):
                        matched = o
                        break

                if matched and matched["volume"] >= MIN_VOLUME:
                    t_low, t_high = matched["range"]
                    p  = bucket_prob(forecast_temp, t_low, t_high, sigma)
                    ev = calc_ev(p, matched["ask"])
                    if ev >= MIN_EV:
                        kelly = calc_kelly(p, matched["ask"])
                        size  = bet_size(kelly, balance)
                        if size >= MIN_BET:
                            signal = {
                                "market_id":    matched["market_id"],
                                "condition_id": matched.get("condition_id"),
                                "polymarket_url": matched.get("polymarket_url"),
                                "question":     matched["question"],
                                "bucket_low":   t_low,
                                "bucket_high":  t_high,
                                "entry_price":  matched["ask"],
                                "bid_at_entry": matched["bid"],
                                "spread":       matched["spread"],
                                "cost":         size,
                                "shares":       round(size / matched["ask"], 2),
                                "p":            round(p, 4),
                                "ev":           round(ev, 4),
                                "kelly":        round(kelly, 4),
                                "forecast_temp":forecast_temp,
                                "forecast_src": best_source,
                                "sigma":        sigma,
                                "opened_at":    snap.get("ts"),
                                "status":       "open",
                                "pnl":          None,
                                "exit_price":   None,
                                "close_reason": None,
                                "closed_at":    None,
                                "stop_price":   round(matched["ask"] * (1 - STOP_LOSS_PCT), 4),
                                "trailing_activated": False,
                            }
                            # Pre-entry live-ask revalidation
                            real_ask, real_bid, real_spread = get_market_best_ask_bid(matched["market_id"])
                            if real_ask and real_bid:
                                if real_spread and real_spread > MAX_SLIPPAGE:
                                    skipped += 1
                                    print(f"\n    [SKIP] {loc['name']} {date} — real spread ${real_spread:.3f} > {MAX_SLIPPAGE}", end=" ")
                                    signal = None
                                elif real_ask >= MAX_PRICE:
                                    skipped += 1
                                    print(f"\n    [SKIP] {loc['name']} {date} — real ask ${real_ask:.3f} >= {MAX_PRICE}", end=" ")
                                    signal = None
                                else:
                                    signal["entry_price"]  = real_ask
                                    signal["bid_at_entry"] = real_bid
                                    signal["spread"]       = real_spread
                                    signal["shares"]       = round(size / real_ask, 2)
                                    signal["ev"]           = round(calc_ev(p, real_ask), 4)
                                    signal["stop_price"]   = round(real_ask * (1 - STOP_LOSS_PCT), 4)

                            if signal:
                                _open_position(mkt, signal, dry_run)
                                if signal.get("status") == "open":
                                    balance -= signal["cost"]
                                    state["total_trades"] += 1
                                    new_pos += 1
                                    bucket_label = f"{t_low}-{t_high}{unit_sym}"
                                    src_tag = (best_source or "ecmwf").upper()
                                    print(f"\n    [BUY] {loc['name']} {horizon} {date} | {bucket_label} | "
                                          f"${signal['entry_price']:.3f} | EV {signal['ev']:+.2f} | "
                                          f"${signal['cost']:.2f} ({src_tag})", end=" ")

            # Market closed by time
            if hours < 0.5 and mkt["status"] == "open":
                mkt["status"] = "closed"

            if not dry_run:
                save_market(mkt)
            time.sleep(0.1)

        print("ok")

    # --- Resolution sweep: reconcile closed markets against Polymarket ---
    for mkt in load_all_markets():
        if mkt["status"] == "resolved":
            continue
        pos = mkt.get("position")
        if not pos or pos.get("status") != "open":
            continue
        # Check market resolution via Gamma
        url = f"https://gamma-api.polymarket.com/markets/{pos['market_id']}"
        data = _http_get_json(url, timeout=5, retries=2, label=f"resolve {pos['market_id']}")
        if not data:
            continue
        if not data.get("closed"):
            continue
        try:
            prices = json.loads(data.get("outcomePrices", "[0.5,0.5]"))
            yes_price = float(prices[0])
        except Exception:
            continue
        won = yes_price >= 0.95
        lost = yes_price <= 0.05
        if not won and not lost:
            continue
        entry = pos["entry_price"]
        shares = pos["shares"]
        size = pos["cost"]
        pnl = round(shares * (1 - entry), 2) if won else round(-size, 2)
        balance += size + pnl
        pos["exit_price"]   = 1.0 if won else 0.0
        pos["pnl"]          = pnl
        pos["close_reason"] = "resolved"
        pos["closed_at"]    = now.isoformat()
        pos["status"]       = "closed"
        mkt["pnl"] = pnl
        mkt["status"] = "resolved"
        mkt["resolved_outcome"] = "win" if won else "loss"
        if won:
            state["wins"] += 1
        else:
            state["losses"] += 1
        print(f"    [{'WIN' if won else 'LOSS'}] {mkt['city_name']} {mkt['date']} | "
              f"PnL: {'+' if pnl>=0 else ''}{pnl:.2f}")
        resolved += 1
        save_market(mkt)

    state["balance"]      = round(balance, 2)
    state["peak_balance"] = max(state.get("peak_balance", balance), balance)
    if not dry_run:
        save_state(state)

    return new_pos, closed, resolved

def _open_position(mkt, signal, dry_run):
    """Open a position. Always routes through SimmerClient.trade() unless dry_run.

    Paper vs real-money is decided by SimmerClient's live= param (set from
    --live). Mutates signal in place; caller reads signal['status'] to decide
    whether to debit the local balance. Successful open → status='open'.
    SDK failure → status='failed' + sdk_error.
    """
    if dry_run:
        mkt["position"] = signal
        return

    # Ensure the Polymarket market is indexed by Simmer (free check, then import-on-miss).
    simmer_market_id, idx_err = ensure_market_indexed(
        signal["market_id"],
        condition_id=signal.get("condition_id"),
        polymarket_url=signal.get("polymarket_url"),
    )
    if not simmer_market_id:
        signal["status"] = "failed"
        signal["sdk_error"] = f"index failed: {idx_err}"
        mkt["position"] = signal
        return
    signal["simmer_market_id"] = simmer_market_id

    reasoning = (f"EV {signal['ev']:+.2f} | forecast {signal['forecast_temp']}° "
                 f"via {signal['forecast_src']} | bucket {signal['bucket_low']}-{signal['bucket_high']}")
    signal_data = {
        "edge": signal["ev"],
        "confidence": signal["p"],
        "signal_source": signal["forecast_src"],
        "forecast_temp": signal["forecast_temp"],
        "kelly": signal["kelly"],
        "sigma": signal["sigma"],
    }
    result = execute_buy(simmer_market_id, "yes", signal["cost"], reasoning, signal_data)
    if not result.get("success"):
        signal["status"] = "failed"
        signal["sdk_error"] = result.get("error")
        mkt["position"] = signal
        return
    signal["shares"] = result.get("shares") or signal["shares"]
    signal["sdk_trade_id"] = result.get("trade_id")
    signal["sdk_order_status"] = result.get("order_status")
    signal["sdk_simulated"] = result.get("simulated", False)
    mkt["position"] = signal

def _close_position(mkt, pos, exit_bid, reason, dry_run):
    """Close a position — mirrors AlterEgo's in-code stop logic.

    Always routes the sell through SimmerClient.trade() unless dry_run.
    SDK's live= flag (set at init from --live) controls paper vs real-money.
    """
    pnl = round((exit_bid - pos["entry_price"]) * pos["shares"], 2)
    pos["closed_at"]    = datetime.now(timezone.utc).isoformat()
    pos["close_reason"] = reason
    pos["exit_price"]   = exit_bid
    pos["pnl"]          = pnl
    if not dry_run:
        # Use Simmer's market_id (cached at open time) — pos["market_id"] is the
        # Polymarket ID, which the SDK doesn't know how to route.
        sell_market_id = pos.get("simmer_market_id") or pos["market_id"]
        result = execute_sell(sell_market_id, "yes", pos["shares"], reasoning=f"exit: {reason}")
        pos["sdk_sell_trade_id"] = result.get("trade_id")
        pos["sdk_sell_status"]   = result.get("order_status")
        pos["sdk_sell_simulated"] = result.get("simulated", False)
    pos["status"] = "closed"

# =============================================================================
# REPORTS
# =============================================================================

def print_status():
    state = load_state()
    markets = load_all_markets()
    open_pos = [m for m in markets if m.get("position") and m["position"].get("status") == "open"]
    resolved = [m for m in markets if m["status"] == "resolved" and m.get("pnl") is not None]

    bal = state["balance"]
    start = state["starting_balance"]
    ret_pct = (bal - start) / start * 100 if start else 0
    wins, losses = state["wins"], state["losses"]
    total = wins + losses

    print(f"\n{'='*55}")
    print(f"  WEATHER-EV PORT — STATUS")
    print(f"{'='*55}")
    print(f"  Balance:     ${bal:,.2f}  (start ${start:,.2f}, {'+' if ret_pct>=0 else ''}{ret_pct:.1f}%)")
    if total:
        print(f"  Trades:      {total} | W: {wins} | L: {losses} | WR: {wins/total:.0%}")
    else:
        print("  No trades yet")
    print(f"  Open:        {len(open_pos)}")
    print(f"  Resolved:    {len(resolved)}")

    if open_pos:
        print(f"\n  Open positions:")
        total_unrealized = 0.0
        for m in open_pos:
            pos = m["position"]
            unit_sym = "F" if m["unit"] == "F" else "C"
            label = f"{pos['bucket_low']}-{pos['bucket_high']}{unit_sym}"
            cur = pos["entry_price"]
            for o in m.get("all_outcomes", []):
                if o["market_id"] == pos["market_id"]:
                    cur = o["price"]
                    break
            unrealized = round((cur - pos["entry_price"]) * pos["shares"], 2)
            total_unrealized += unrealized
            pnl_str = f"{'+' if unrealized>=0 else ''}{unrealized:.2f}"
            src = (pos.get("forecast_src") or "?").upper()
            print(f"    {m['city_name']:<16} {m['date']} | {label:<14} | "
                  f"entry ${pos['entry_price']:.3f} -> ${cur:.3f} | PnL: {pnl_str} | {src}")
        sign = "+" if total_unrealized >= 0 else ""
        print(f"\n  Unrealized PnL: {sign}{total_unrealized:.2f}")
    print(f"{'='*55}\n")

def print_report():
    markets = load_all_markets()
    resolved = [m for m in markets if m["status"] == "resolved" and m.get("pnl") is not None]
    print(f"\n{'='*55}\n  WEATHER-EV PORT — FULL REPORT\n{'='*55}")
    if not resolved:
        print("  No resolved markets yet.")
        return
    total_pnl = sum(m["pnl"] for m in resolved)
    wins = [m for m in resolved if m["resolved_outcome"] == "win"]
    losses = [m for m in resolved if m["resolved_outcome"] == "loss"]
    print(f"\n  Total resolved: {len(resolved)}")
    print(f"  Wins: {len(wins)} | Losses: {len(losses)} | WR: {len(wins)/len(resolved):.0%}")
    print(f"  Total PnL: {'+' if total_pnl>=0 else ''}{total_pnl:.2f}")
    # Per-city breakdown
    by_city = {}
    for m in resolved:
        c = m["city"]
        by_city.setdefault(c, []).append(m)
    print(f"\n  Per-city breakdown:")
    for c, lst in sorted(by_city.items()):
        pnl = sum(m["pnl"] for m in lst)
        print(f"    {LOCATIONS[c]['name']:<16} n={len(lst):3d} | PnL: {'+' if pnl>=0 else ''}{pnl:.2f}")
    print(f"{'='*55}\n")

def print_sdk_positions():
    positions = sdk_positions_for_skill()
    if not positions:
        print("  No Simmer positions tagged to this skill.")
        return
    print(f"\n  Simmer SDK positions (source={TRADE_SOURCE}):")
    for p in positions:
        mid = p.get("market_id", "?")
        shares = p.get("shares", 0)
        avg = p.get("avg_price", 0)
        print(f"    {mid[:16]:<16} shares={shares:.2f} avg=${avg:.3f}")

# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Weather-EV port (WIP)")
    parser.add_argument("subcommand", nargs="?", default="scan",
                        choices=["scan", "status", "report", "positions"])
    parser.add_argument("--live", action="store_true", help="Execute real trades via Simmer SDK")
    parser.add_argument("--dry-run", action="store_true", help="Show opportunities only, no local state writes")
    args = parser.parse_args()

    if args.subcommand == "status":
        print_status()
        return
    if args.subcommand == "report":
        print_report()
        return
    if args.subcommand == "positions":
        print_sdk_positions()
        return

    mode = "LIVE" if args.live else ("DRY-RUN" if args.dry_run else "PAPER (local)")
    print(f"\n  Weather-EV port | mode: {mode} | venue: {os.environ.get('TRADING_VENUE', 'polymarket')}")
    print(f"  Cities: {list(active_locations().keys())}")
    print(f"  MIN_EV={MIN_EV} KELLY_FRAC={KELLY_FRACTION} MAX_BET=${MAX_BET} STOP={STOP_LOSS_PCT:.0%}\n")

    global _LIVE_MODE
    _LIVE_MODE = args.live
    new_pos, closed, resolved = scan_and_update(dry_run=args.dry_run)
    print(f"\n  Scan complete: opened={new_pos} closed={closed} resolved={resolved}\n")
    print_status()

if __name__ == "__main__":
    main()
