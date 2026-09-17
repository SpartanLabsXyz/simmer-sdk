#!/usr/bin/env python3
"""
Simmer BTC Up-Down Trader

Trades Polymarket BTC daily/weekly UP or DOWN markets using CEX momentum signals,
with empirically-anchored exit discipline:
  - Exit before resolution (don't hold to settlement)
  - Exit on volume spike (smart money signal)
  - Exit when target profit % captured (lock-in discipline)

Usage:
    python strategy.py              # Dry run (show positions + opportunities)
    python strategy.py --live       # Execute real trades
    python strategy.py --monitor    # Only check exits on open positions
    python strategy.py --positions  # Show current positions
    python strategy.py --config     # Show current config
    python strategy.py --set KEY=VALUE  # Update config

Requires:
    SIMMER_API_KEY environment variable (get from simmer.markets/dashboard)
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import quote

# Force line-buffered stdout for non-TTY environments (cron, Docker, OpenClaw)
sys.stdout.reconfigure(line_buffering=True)

# Optional: Trade Journal integration
try:
    from tradejournal import log_trade
    JOURNAL_AVAILABLE = True
except ImportError:
    try:
        from skills.tradejournal import log_trade
        JOURNAL_AVAILABLE = True
    except ImportError:
        JOURNAL_AVAILABLE = False
        def log_trade(*args, **kwargs):
            pass

# =============================================================================
# Configuration (config.json > env vars > defaults)
# =============================================================================

CONFIG_SCHEMA = {
    # Entry knobs
    "entry_threshold": {
        "default": 0.05,
        "env": "SIMMER_BTCUD_ENTRY_THRESHOLD",
        "type": float,
        "help": "Min price divergence from 50¢ to trigger entry",
    },
    "min_momentum_pct": {
        "default": 0.3,
        "env": "SIMMER_BTCUD_MOMENTUM_THRESHOLD",
        "type": float,
        "help": "Min BTC % move in lookback window to trigger",
    },
    "max_position": {
        "default": 10.0,
        "env": "SIMMER_BTCUD_MAX_POSITION_USD",
        "type": float,
        "help": "Max $ per position",
    },
    "lookback_minutes": {
        "default": 30,
        "env": "SIMMER_BTCUD_LOOKBACK_MINUTES",
        "type": int,
        "help": "Minutes of BTC price history for momentum signal",
    },
    "daily_budget": {
        "default": 50.0,
        "env": "SIMMER_BTCUD_DAILY_BUDGET_USD",
        "type": float,
        "help": "Max total entry spend per UTC day",
    },
    "min_hours_to_resolution": {
        "default": 4.0,
        "env": "SIMMER_BTCUD_MIN_HOURS_TO_RESOLUTION",
        "type": float,
        "help": "Skip entry if market resolves in fewer than this many hours",
    },
    # -------------------------------------------------------------------------
    # Exit discipline knobs
    # -------------------------------------------------------------------------
    "exit_before_resolution_hours": {
        "default": 1.0,
        "env": "SIMMER_BTCUD_EXIT_BEFORE_RESOLUTION_HOURS",
        "type": float,
        "help": (
            "Exit position this many hours before scheduled resolution. "
            "Default 1.0h — empirically, >90%% of profitable exits happen well "
            "before resolution; holding to settlement adds tail risk with little "
            "expected upside. Set to 0 to disable time-cap exits."
        ),
    },
    "volume_spike_exit_multiplier": {
        "default": 3.0,
        "env": "SIMMER_BTCUD_VOLUME_SPIKE_MULTIPLIER",
        "type": float,
        "help": (
            "Exit when current 10-minute CLOB volume exceeds N× the rolling "
            "baseline. Default 3.0 — a 3× spike in the 10 minutes before "
            "resolution is a strong signal that informed traders are positioning. "
            "Set to 0 to disable volume-spike exits."
        ),
    },
    "target_hit_capture_pct": {
        "default": 0.85,
        "env": "SIMMER_BTCUD_TARGET_HIT_CAPTURE_PCT",
        "type": float,
        "help": (
            "Exit when (current_price - entry_price) / (1.0 - entry_price) "
            "reaches this fraction for YES positions (mirror for NO). "
            "Default 0.85 — captures 85%% of the theoretical max move and "
            "exits before the final squeeze premium evaporates. "
            "Set to 0 to disable target-hit exits."
        ),
    },
    # Volume spike window config
    "volume_baseline_windows": {
        "default": 6,
        "env": "SIMMER_BTCUD_VOLUME_BASELINE_WINDOWS",
        "type": int,
        "help": "Number of 10-minute windows used to compute rolling volume baseline",
    },
}

TRADE_SOURCE = "sdk:btcupdown"
SKILL_SLUG = "polymarket-btc-up-down-trader"
_automaton_reported = False

# Polymarket CLOB endpoint
CLOB_API = "https://clob.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"

# Minimum time remaining to bother with an exit trade (avoid dust fills)
MIN_EXIT_TIME_REMAINING_SEC = 60

MIN_SHARES_PER_ORDER = 5


# =============================================================================
# Imports
# =============================================================================

from simmer_sdk.skill import load_config, update_config, get_config_path

cfg = load_config(CONFIG_SCHEMA, __file__, slug=SKILL_SLUG)

ENTRY_THRESHOLD = cfg["entry_threshold"]
MIN_MOMENTUM_PCT = cfg["min_momentum_pct"]
MAX_POSITION_USD = cfg["max_position"]
_automaton_max = os.environ.get("AUTOMATON_MAX_BET")
if _automaton_max:
    MAX_POSITION_USD = min(MAX_POSITION_USD, float(_automaton_max))
LOOKBACK_MINUTES = cfg["lookback_minutes"]
DAILY_BUDGET = cfg["daily_budget"]
MIN_HOURS_TO_RESOLUTION = cfg["min_hours_to_resolution"]

# Exit discipline
EXIT_BEFORE_RESOLUTION_HOURS = cfg["exit_before_resolution_hours"]
VOLUME_SPIKE_EXIT_MULTIPLIER = cfg["volume_spike_exit_multiplier"]
TARGET_HIT_CAPTURE_PCT = cfg["target_hit_capture_pct"]
VOLUME_BASELINE_WINDOWS = cfg["volume_baseline_windows"]


# =============================================================================
# HTTP helpers
# =============================================================================

_LIVE_VENDOR_HOSTS = ("gamma-api.polymarket.com", "clob.polymarket.com")


class MarketFetchError(RuntimeError):
    """Replay listing failed. Fail-closed so a 0-eval tick is not bundle.clean."""


class ReplayClockError(RuntimeError):
    """SIMMER_REPLAY_NOW was set but unusable. Wall clock would be look-ahead."""


def _is_live_vendor_url(url):
    return any(host in url for host in _LIVE_VENDOR_HOSTS)


def _api_request(url, method="GET", data=None, headers=None, timeout=15):
    """HTTP request to external APIs. Returns parsed JSON or error dict."""
    if _is_replay() and _is_live_vendor_url(url):
        raise MarketFetchError(f"live vendor blocked under replay: {url}")
    try:
        req_headers = headers or {}
        if "User-Agent" not in req_headers:
            req_headers["User-Agent"] = "simmer-btcupdown/1.0"
        body = None
        if data:
            body = json.dumps(data).encode("utf-8")
            req_headers["Content-Type"] = "application/json"
        req = Request(url, data=body, headers=req_headers, method=method)
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        try:
            error_body = json.loads(e.read().decode("utf-8"))
            return {"error": error_body.get("detail", str(e)), "status_code": e.code}
        except Exception:
            return {"error": str(e), "status_code": e.code}
    except URLError as e:
        return {"error": f"Connection error: {e.reason}"}
    except Exception as e:
        return {"error": str(e)}


# =============================================================================
# SimmerClient
# =============================================================================

_client = None


def get_client(live=True):
    """Lazy-init SimmerClient singleton."""
    global _client
    if _client is None:
        try:
            from simmer_sdk import SimmerClient
        except ImportError:
            print("Error: simmer-sdk not installed. Run: pip install simmer-sdk")
            sys.exit(1)
        api_key = os.environ.get("SIMMER_API_KEY")
        if not api_key:
            print("Error: SIMMER_API_KEY environment variable not set")
            print("Get your API key from: simmer.markets/dashboard -> SDK tab")
            sys.exit(1)
        _client = SimmerClient(api_key=api_key, venue="polymarket", live=live)
    return _client


# =============================================================================
# Replay clock / discovery / price plane (SIM-5442)
# =============================================================================

def _is_replay():
    """True inside the Simmer replay harness (SIMMER_REPLAY=1). Decision data
    must then come ONLY from the Simmer API — a direct-vendor fetch would be
    future data relative to the frozen tick."""
    return os.environ.get("SIMMER_REPLAY") == "1"


def _clock():
    """Wall clock, or the replay frozen tick (`SIMMER_REPLAY_NOW`).

    Horizon and daily-spend date used `datetime.now()`. Under replay that is
    the real present, so a historical tape is already resolved and the skill
    look-aheads to today's live 'Bitcoin Up or Down' market. The harness
    already sets `SIMMER_REPLAY_NOW` per tick.
    """
    if _is_replay():
        raw = os.environ.get("SIMMER_REPLAY_NOW")
        if raw:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError as exc:
                raise ReplayClockError(
                    f"SIMMER_REPLAY_NOW is not a valid timestamp: {raw!r}"
                ) from exc
    return datetime.now(timezone.utc)


def _btc_markets_params():
    """Replay listing params. Live Gamma is a different HTTP surface.

    Replay rejects tags/status (sdk 0.25.3/0.25.4, 422). `q` is the only
    filter the replay server applies. Tape questions are 'Bitcoin Up or Down
    on …'. A tight `q` keeps ETH/SOL/hourly rows off the eval budget.
    """
    return {"q": "bitcoin up or down", "limit": 200}


def _is_btc_updown_text(question, title, slug):
    """Daily/weekly BTC UP/DOWN only — not ETH/SOL, not 5m/15m fast markets."""
    blob = f"{question} {title} {slug}"
    if "up or down" not in blob and "up-or-down" not in blob:
        return False
    if any(marker in blob for marker in ("5m", "15m", "5-minute", "15-minute")):
        return False
    return "bitcoin" in blob or "btc" in blob


def _parse_end_dt(market):
    end_date = (
        market.get("endDate")
        or market.get("end_date_iso")
        or market.get("resolves_at")
        or market.get("end_date")
    )
    if not end_date:
        return None
    try:
        if isinstance(end_date, (int, float)):
            return datetime.fromtimestamp(end_date, tz=timezone.utc)
        return datetime.fromisoformat(str(end_date).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _normalize_btc_markets(rows):
    """Shared filter + horizon. Clock is `_clock()` so replay honors the tick."""
    now = _clock()
    markets = []
    for m in rows:
        question = (m.get("question") or "").lower()
        title = (m.get("groupItemTitle") or m.get("event_name") or "").lower()
        slug = (m.get("slug") or "").lower()
        if not _is_btc_updown_text(question, title, slug):
            continue
        end_dt = _parse_end_dt(m)
        if not end_dt:
            continue
        hours_to_resolution = (end_dt - now).total_seconds() / 3600.0
        if hours_to_resolution <= 0:
            continue
        m["_hours_to_resolution"] = hours_to_resolution
        m["_end_dt"] = end_dt
        markets.append(m)
    return markets


def _market_yes_price(market):
    """Replay listings expose `yes_price` / `current_probability`, not CLOB.

    Missing tape price is None (skip) — do not invent 0.50. Live callers
    still use `fetch_live_midpoint`.
    """
    if not market:
        return None
    for key in ("yes_price", "current_probability"):
        val = market.get(key)
        if val is None:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    return None


def _market_id(market):
    if _is_replay():
        return market.get("id") or market.get("polymarket_condition_id")
    return market.get("conditionId") or market.get("id")


def _yes_token_id(market):
    tokens = market.get("clobTokenIds") or market.get("tokens") or []
    if isinstance(tokens, str):
        try:
            tokens = json.loads(tokens)
        except json.JSONDecodeError:
            tokens = [tokens]
    if tokens:
        return tokens[0]
    return market.get("polymarket_token_id")


def _trade_kwargs(**kwargs):
    return kwargs


def _trade_succeeded(result):
    """True only on an explicit success. Failed fills must not burn daily spend."""
    if result is None:
        return False
    if isinstance(result, dict):
        if result.get("error"):
            return False
        return bool(result.get("success"))
    if getattr(result, "error", None) and not getattr(result, "success", False):
        return False
    return bool(getattr(result, "success", False))


# =============================================================================
# Market discovery
# =============================================================================

def fetch_btc_updown_markets():
    """
    Discover active BTC UP/DOWN markets.

    Live: Gamma (volume-sorted; Gamma ignores server-side q/tags).
    Replay (`SIMMER_REPLAY=1`): Simmer tape via `/api/sdk/markets?q=bitcoin up or down`
    — no live Gamma, no tags/status (those 422 on replay).
    Filters out fast (5m/15m) markets — those are for polymarket-fast-loop.
    """
    if _is_replay():
        return _fetch_btc_updown_markets_replay()
    return _fetch_btc_updown_markets_live()


def _fetch_btc_updown_markets_live():
    # Fetch top 200 by 24h volume — BTC UP/DOWN markets surface near the top.
    # Client-side filter handles the rest. Gamma ignores q/tags.
    url = (
        f"{GAMMA_API}/markets"
        "?active=true&closed=false&limit=200"
        "&order=volume24hr&ascending=false"
    )
    data = _api_request(url)
    if not isinstance(data, list):
        data = (data or {}).get("markets", [])
    if not isinstance(data, list):
        return []
    return _normalize_btc_markets(data)


def _fetch_btc_updown_markets_replay():
    """Tape listing. A failed request raises; empty 200 is an honest empty tape."""
    try:
        result = get_client()._request(
            "GET", "/api/sdk/markets", params=_btc_markets_params()
        )
    except MarketFetchError:
        raise
    except Exception as exc:
        print("  Failed to fetch markets from Simmer API")
        raise MarketFetchError("Failed to fetch markets from Simmer API") from exc
    if not isinstance(result, dict):
        print("  Failed to fetch markets from Simmer API")
        raise MarketFetchError("Failed to fetch markets from Simmer API")
    rows = result.get("markets", [])
    if not isinstance(rows, list):
        print("  Failed to fetch markets from Simmer API")
        raise MarketFetchError("Failed to fetch markets from Simmer API")
    return _normalize_btc_markets(rows)


def _fetch_replay_market(client, market_id):
    """Single-market tape row. Live Gamma is never used under replay."""
    try:
        result = client._request("GET", f"/api/sdk/context/{market_id}")
    except Exception:
        return None
    if not isinstance(result, dict):
        return None
    market = result.get("market") or result
    if not isinstance(market, dict) or market.get("error"):
        return None
    return market


# =============================================================================
# Price signals
# =============================================================================


def fetch_btc_momentum(lookback_minutes):
    """
    BTC momentum over the last `lookback_minutes` of CLOSED 1m candles.

    Primary source: Simmer's data plane (client.get_candles — closed candles
    only, one code path live and under replay; current_price becomes the last
    CLOSED close, <=60s behind the old intra-candle read). Legacy direct
    Binance is a LIVE-ONLY fallback for servers without the data plane; under
    replay it never fires.

    Returns (momentum_pct, direction, current_price) or (None, None, None).
    """
    # No API key → no client (get_client() sys.exits). Checked up-front so the
    # sys.exit never fires inside the signal fetch.
    if os.environ.get("SIMMER_API_KEY"):
        try:
            end = datetime.now(timezone.utc)
            start = end - timedelta(minutes=max(lookback_minutes + 1, 5))
            candles = get_client().get_candles("BTCUSDT", start.isoformat(), end.isoformat())
            if candles and len(candles) >= 2:
                open_price = float(candles[0]["open"])
                close_price = float(candles[-1]["close"])
                momentum_pct = (close_price - open_price) / open_price * 100
                direction = "up" if momentum_pct > 0 else "down"
                return abs(momentum_pct), direction, close_price
            if _is_replay():
                return None, None, None  # honest no-signal — never fall back under replay
        except Exception:  # noqa: BLE001 — branch on environment below
            if _is_replay():
                return None, None, None
    elif _is_replay():
        return None, None, None  # can't reach the plane under replay → no-signal

    # ---- legacy LIVE-ONLY direct path (never reached under replay) ----
    interval = "1m"
    limit = max(lookback_minutes + 1, 5)
    url = f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval={interval}&limit={limit}"
    data = _api_request(url, timeout=10)
    if not data or isinstance(data, dict):
        return None, None, None
    try:
        open_price = float(data[0][1])   # open of oldest candle
        close_price = float(data[-1][4]) # close of most recent candle
        momentum_pct = (close_price - open_price) / open_price * 100
        direction = "up" if momentum_pct > 0 else "down"
        return abs(momentum_pct), direction, close_price
    except (IndexError, ValueError, TypeError):
        return None, None, None


def fetch_live_midpoint(token_id):
    """Fetch live midpoint price from Polymarket CLOB for a token.

    Under replay this is look-ahead — return None and never hit CLOB.
    Entry/exit use `_market_yes_price` from the tape listing instead.
    """
    if _is_replay():
        return None
    result = _api_request(f"{CLOB_API}/midpoint?token_id={quote(str(token_id))}", timeout=5)
    if not result or isinstance(result, dict) and result.get("error"):
        return None
    try:
        return float(result["mid"])
    except (KeyError, ValueError, TypeError):
        return None


# =============================================================================
# Exit trigger evaluation
# =============================================================================

def check_time_cap_exit(hours_to_resolution, end_dt):
    """
    Trigger: time_cap
    Fire when the market is within EXIT_BEFORE_RESOLUTION_HOURS of resolution.

    Returns (should_exit: bool, reason: str | None)
    """
    if EXIT_BEFORE_RESOLUTION_HOURS <= 0:
        return False, None

    now = _clock()
    seconds_remaining = (end_dt - now).total_seconds()

    if seconds_remaining < MIN_EXIT_TIME_REMAINING_SEC:
        # Too close — skip exit trade (not worth executing)
        return False, None

    if hours_to_resolution <= EXIT_BEFORE_RESOLUTION_HOURS:
        return True, "time_cap"

    return False, None


def fetch_10m_volume(token_id):
    """
    Fetch approximate 10-minute trade volume for a CLOB token.
    Uses the /trades endpoint filtered to the last 10 minutes.
    Returns total USDC volume (float) or None on error.
    """
    since_ts = int((datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp())
    url = f"{CLOB_API}/trades?token_id={quote(str(token_id))}&after={since_ts}&limit=500"
    data = _api_request(url, timeout=10)
    if not data or isinstance(data, dict) and data.get("error"):
        return None
    if not isinstance(data, list):
        data = (data if isinstance(data, dict) else {}).get("data", [])
    if not isinstance(data, list):
        return None
    try:
        total = sum(float(t.get("size", 0)) * float(t.get("price", 0)) for t in data)
        return total
    except (ValueError, TypeError):
        return None


def fetch_volume_history_windows(token_id, n_windows, window_minutes=10):
    """
    Fetch n_windows of 10-minute volume history for baseline computation.
    Returns list of floats (volume per window), oldest first.
    """
    volumes = []
    now = datetime.now(timezone.utc)
    for i in range(n_windows, 0, -1):
        start = now - timedelta(minutes=window_minutes * i)
        end = now - timedelta(minutes=window_minutes * (i - 1))
        url = (
            f"{CLOB_API}/trades?token_id={quote(str(token_id))}"
            f"&after={int(start.timestamp())}&before={int(end.timestamp())}&limit=500"
        )
        data = _api_request(url, timeout=10)
        if not data or isinstance(data, dict) and data.get("error"):
            continue
        if not isinstance(data, list):
            data = (data if isinstance(data, dict) else {}).get("data", [])
        if not isinstance(data, list):
            continue
        try:
            vol = sum(float(t.get("size", 0)) * float(t.get("price", 0)) for t in data)
            volumes.append(vol)
        except (ValueError, TypeError):
            continue
    return volumes


def check_volume_spike_exit(token_id):
    """
    Trigger: volume_spike
    Fire when current 10-minute volume exceeds VOLUME_SPIKE_EXIT_MULTIPLIER ×
    the rolling baseline (average of VOLUME_BASELINE_WINDOWS prior windows).

    Returns (should_exit: bool, reason: str | None, details: dict)
    """
    if VOLUME_SPIKE_EXIT_MULTIPLIER <= 0:
        return False, None, {}

    current_vol = fetch_10m_volume(token_id)
    if current_vol is None:
        return False, None, {"error": "Could not fetch current volume"}

    history = fetch_volume_history_windows(token_id, VOLUME_BASELINE_WINDOWS)
    if not history:
        return False, None, {"error": "Could not fetch volume history"}

    baseline = sum(history) / len(history)
    if baseline <= 0:
        # No baseline volume — conservative, don't fire
        return False, None, {"current_vol": current_vol, "baseline": 0}

    ratio = current_vol / baseline
    details = {
        "current_vol": round(current_vol, 2),
        "baseline_vol": round(baseline, 2),
        "ratio": round(ratio, 2),
        "threshold": VOLUME_SPIKE_EXIT_MULTIPLIER,
    }

    if ratio >= VOLUME_SPIKE_EXIT_MULTIPLIER:
        return True, "volume_spike", details

    return False, None, details


def check_target_hit_exit(entry_price, current_price, side):
    """
    Trigger: target_hit
    For YES positions: fire when (current - entry) / (1.0 - entry) >= TARGET_HIT_CAPTURE_PCT
    For NO positions:  fire when (entry - current) / entry >= TARGET_HIT_CAPTURE_PCT

    Args:
        entry_price: float, price at which we entered (0-1 scale)
        current_price: float, live midpoint (0-1 scale)
        side: "YES" or "NO"

    Returns (should_exit: bool, reason: str | None, details: dict)
    """
    if TARGET_HIT_CAPTURE_PCT <= 0:
        return False, None, {}

    if entry_price is None or current_price is None:
        return False, None, {}

    if side == "YES":
        max_gain = 1.0 - entry_price
        if max_gain <= 0:
            return False, None, {}
        captured = (current_price - entry_price) / max_gain
    else:  # NO — we bought NO shares, price is YES price, our value moves opposite
        max_gain = entry_price  # NO bought at (1-entry_price) in YES space
        if max_gain <= 0:
            return False, None, {}
        # If we bought NO at implied entry_price (YES), our value = 1 - current_price
        # entry value as NO holder = 1 - entry_price
        # current value = 1 - current_price
        captured = (entry_price - current_price) / max_gain

    details = {
        "entry_price": round(entry_price, 4),
        "current_price": round(current_price, 4),
        "side": side,
        "captured_pct": round(captured * 100, 1),
        "threshold_pct": round(TARGET_HIT_CAPTURE_PCT * 100, 1),
    }

    if captured >= TARGET_HIT_CAPTURE_PCT:
        return True, "target_hit", details

    return False, None, details


def evaluate_exit_triggers(position, end_dt, yes_token_id, current_price=None):
    """
    Run all three exit triggers on a position.
    Returns (should_exit: bool, exit_reason: str | None, details: dict).
    Priority: time_cap > target_hit > volume_spike

    `current_price` is the YES mid (0-1). Replay passes tape `yes_price`;
    live fetches CLOB. Volume spike is live-only — CLOB trades under replay
    would be look-ahead.
    """
    now = _clock()
    hours_to_resolution = (end_dt - now).total_seconds() / 3600.0

    # 1. Time cap (highest priority — hard deadline)
    fired, reason = check_time_cap_exit(hours_to_resolution, end_dt)
    if fired:
        return True, reason, {"hours_to_resolution": round(hours_to_resolution, 2)}

    if current_price is None and not _is_replay():
        current_price = fetch_live_midpoint(yes_token_id)

    # 2. Target hit (second priority — lock in gains)
    entry_price = position.get("entry_price")
    side = position.get("side", "YES")
    fired, reason, details = check_target_hit_exit(entry_price, current_price, side)
    if fired:
        return True, reason, details

    # 3. Volume spike (third priority — smart money signal). Live CLOB only.
    if not _is_replay():
        fired, reason, details = check_volume_spike_exit(yes_token_id)
        if fired:
            return True, reason, details

    return False, None, {
        "hours_to_resolution": round(hours_to_resolution, 2),
        "current_price": current_price,
    }


# =============================================================================
# Daily budget tracking
# =============================================================================

def _get_spend_path():
    return Path(__file__).parent / "daily_spend.json"


def _load_daily_spend():
    spend_path = _get_spend_path()
    today = _clock().strftime("%Y-%m-%d")
    if spend_path.exists():
        try:
            with open(spend_path) as f:
                data = json.load(f)
            if data.get("date") == today:
                return data
        except (json.JSONDecodeError, IOError):
            pass
    return {"date": today, "spent": 0.0, "trades": 0}


def _save_daily_spend(spend_data):
    with open(_get_spend_path(), "w") as f:
        json.dump(spend_data, f, indent=2)


# =============================================================================
# Core trading logic
# =============================================================================

def _pos_get(pos, *names, default=None):
    """Read a field from a Position dataclass or a dict. 0 is a real value."""
    if isinstance(pos, dict):
        for name in names:
            if name in pos and pos[name] is not None:
                return pos[name]
        return default
    for name in names:
        val = getattr(pos, name, None)
        if val is not None:
            return val
    return default


def _position_is_ours(sources):
    """Live: require sdk:btcupdown. Replay: the session only runs this skill."""
    if sources is None or sources == "" or sources == []:
        return _is_replay()
    if isinstance(sources, str):
        return sources == TRADE_SOURCE or "btcupdown" in sources
    if isinstance(sources, (list, tuple)):
        return any(
            s == TRADE_SOURCE or (s and "btcupdown" in str(s))
            for s in sources
        )
    return "btcupdown" in str(sources)


def _normalize_open_position(pos):
    """SDK Position / replay row → {market_id, side, quantity, entry_price}.

    Live `client.get_positions()` returns Position dataclasses (shares_yes /
    shares_no / sources / cost_basis). Replay `/api/sdk/positions` has the
    same share fields and no source/side/quantity/entry_price. Dicts from
    older tests still work.

    Entry is YES-scale for `check_target_hit_exit`. Live `avg_cost` is the
    held-side price (`cost_basis / total_shares`). Using it raw on a NO
    book made target_hit miss. Derive from cost_basis first; `avg_cost`
    is a last resort and is flipped for NO (`1 - avg_cost`).
    """
    market_id = _pos_get(pos, "market_id", "marketId", "conditionId")
    shares_yes = float(_pos_get(pos, "shares_yes", default=0) or 0)
    shares_no = float(_pos_get(pos, "shares_no", default=0) or 0)
    side = _pos_get(pos, "side")
    quantity = _pos_get(pos, "quantity")
    if side is None or quantity is None:
        if shares_yes > 0 and shares_no <= 0:
            side = "YES"
            quantity = shares_yes
        elif shares_no > 0 and shares_yes <= 0:
            side = "NO"
            quantity = shares_no
        elif shares_yes > 0 or shares_no > 0:
            if shares_yes >= shares_no:
                side = "YES"
                quantity = shares_yes
            else:
                side = "NO"
                quantity = shares_no
        else:
            return None
    else:
        side = str(side).upper()
        quantity = float(quantity)
    if not market_id or quantity <= 0:
        return None
    if not _position_is_ours(_pos_get(pos, "sources", "source")):
        return None
    # YES-scale fields only. Do not read avg_cost here — it is held-side.
    entry_price = _pos_get(pos, "entry_price", "avgPrice", "avg_price")
    if entry_price is None:
        cost_basis = _pos_get(pos, "cost_basis")
        held_avg = None
        if cost_basis is not None and quantity > 0:
            held_avg = float(cost_basis) / quantity
        else:
            raw_avg = _pos_get(pos, "avg_cost")
            if raw_avg is not None:
                held_avg = float(raw_avg)
        if held_avg is not None:
            entry_price = (1.0 - held_avg) if side == "NO" else held_avg
    return {
        "market_id": market_id,
        "side": side,
        "quantity": quantity,
        "entry_price": float(entry_price) if entry_price is not None else None,
        "source": TRADE_SOURCE,
    }


def get_open_positions(client):
    """Fetch open BTC UP/DOWN positions tagged with our source.

    Accepts Position dataclasses (live SDK) and replay share-rows. Never
    calls `.get` on a dataclass — that crashed the exit monitor after the
    first fill (AttributeError) and killed every later tick.
    """
    try:
        positions = client.get_positions() or []
    except Exception as e:
        print(f"  Warning: could not fetch positions: {e}")
        return []
    out = []
    for pos in positions:
        norm = _normalize_open_position(pos)
        if norm:
            out.append(norm)
    return out


def run_exit_monitor(client, live=False, quiet=False):
    """
    Check all open BTC UP/DOWN positions against exit triggers.
    Close positions where any trigger fires.
    """
    if not quiet:
        print("\n🔍 Checking open positions for exit triggers...")

    positions = get_open_positions(client)
    if not positions:
        if not quiet:
            print("  No open BTC UP/DOWN positions.")
        return

    closed = 0
    for pos in positions:
        market_id = pos.get("marketId") or pos.get("market_id") or pos.get("conditionId")
        side = pos.get("side", "YES")
        quantity = float(pos.get("quantity", 0))
        # NOTE: entry_price is always the YES-side price at entry (0–1 scale), regardless
        # of whether this position is YES or NO. check_target_hit_exit() expects this:
        # for NO positions, the YES price was high at entry and has since fallen.
        entry_price = pos.get("entry_price") or pos.get("avgPrice") or pos.get("avg_price")

        if not market_id or quantity <= 0:
            continue

        # Live: Gamma for resolution time and token IDs. Replay: tape context.
        if _is_replay():
            market_data = _fetch_replay_market(client, market_id)
        else:
            market_data = _api_request(f"{GAMMA_API}/markets/{market_id}", timeout=10)
            if not market_data or not isinstance(market_data, dict) or market_data.get("error"):
                market_data = _api_request(
                    f"{GAMMA_API}/markets?conditionId={market_id}&limit=1", timeout=10
                )
                if isinstance(market_data, list) and market_data:
                    market_data = market_data[0]
                elif isinstance(market_data, dict) and "markets" in market_data:
                    markets = market_data.get("markets", [])
                    market_data = markets[0] if markets else {}

        if not market_data or not isinstance(market_data, dict):
            if not quiet:
                print(f"  ⚠️  Could not fetch market data for {market_id} — skipping")
            continue

        end_dt = _parse_end_dt(market_data)
        if not end_dt:
            continue

        yes_token_id = _yes_token_id(market_data)
        if not yes_token_id and not _is_replay():
            continue

        pos_with_entry = dict(pos)
        if entry_price is not None:
            pos_with_entry["entry_price"] = float(entry_price)
            pos_with_entry["side"] = side

        question = market_data.get("question", market_id)
        replay_price = _market_yes_price(market_data) if _is_replay() else None

        if not quiet:
            h = (end_dt - _clock()).total_seconds() / 3600
            print(f"\n  📊 {question[:60]}")
            print(f"     Side: {side} | Qty: {quantity:.1f} | Hours left: {h:.1f}h")

        should_exit, exit_reason, details = evaluate_exit_triggers(
            pos_with_entry, end_dt, yes_token_id, current_price=replay_price
        )

        if should_exit:
            print(f"  🚪 EXIT triggered: {exit_reason} | {details}")
            if live:
                try:
                    result = client.trade(
                        **_trade_kwargs(
                            market_id=market_id,
                            side=side.lower(),
                            action="sell",
                            shares=quantity,
                            venue="polymarket",
                            source=TRADE_SOURCE,
                            skill_slug=SKILL_SLUG,
                        )
                    )
                    log_trade(
                        market_id=market_id,
                        side=side,
                        quantity=quantity,
                        action="exit",
                        exit_reason=exit_reason,
                        source=TRADE_SOURCE,
                        details=details,
                    )
                    print(f"  ✅ Closed: {result}")
                    closed += 1
                except Exception as e:
                    print(f"  ❌ Exit failed: {e}")
            else:
                print(f"  [DRY RUN] Would close {quantity:.1f} {side} shares")
                closed += 1
        else:
            if not quiet:
                print(f"  ✅ Hold — no exit trigger fired | {details}")

    if not quiet:
        print(f"\n  Exits fired: {closed}/{len(positions)}")

    return closed


def run_entry_scan(client, live=False, quiet=False):
    """
    Discover BTC UP/DOWN markets and enter positions where signal is strong.
    """
    if not quiet:
        print("\n🔭 Scanning for BTC UP/DOWN entry opportunities...")

    spend = _load_daily_spend()
    if spend["spent"] >= DAILY_BUDGET:
        print(f"  ⏸️  Daily budget exhausted (${spend['spent']:.2f} / ${DAILY_BUDGET:.2f})")
        return

    momentum, direction, btc_price = fetch_btc_momentum(LOOKBACK_MINUTES)
    if momentum is None:
        print("  ⚠️  Could not fetch BTC momentum — skipping entry scan")
        return

    if not quiet:
        print(f"  BTC: ${btc_price:,.0f} | Momentum: {momentum:+.2f}% ({direction})")

    if momentum < MIN_MOMENTUM_PCT:
        if not quiet:
            print(f"  ⏸️  Momentum {momentum:.2f}% < minimum {MIN_MOMENTUM_PCT:.2f}% — no entry")
        return

    markets = fetch_btc_updown_markets()
    if not quiet:
        print(f"  Found {len(markets)} active BTC UP/DOWN markets")

    entered = 0
    for m in sorted(markets, key=lambda x: x.get("_hours_to_resolution", 0)):
        hours_left = m.get("_hours_to_resolution", 0)
        end_dt = m.get("_end_dt")

        # Skip if resolves too soon (not enough time for the trade to develop)
        if hours_left < MIN_HOURS_TO_RESOLUTION:
            continue

        if _is_replay():
            live_price = _market_yes_price(m)
        else:
            yes_token = _yes_token_id(m)
            if not yes_token:
                continue
            live_price = fetch_live_midpoint(yes_token)
        if live_price is None:
            continue

        # Determine trade side based on momentum direction.
        # Entry requires the market to DISAGREE with momentum:
        #   up momentum → buy YES, only when YES is cheap (live_price < 0.50)
        #   down momentum → buy NO, only when NO is cheap (live_price > 0.50)
        # edge = how far the cheap side is from 50¢ (the no-information price).
        # If the market already agrees with momentum, edge is negative → skip.
        if direction == "up":
            side = "YES"
            edge = 0.50 - live_price   # positive only when YES is below 50¢
        else:
            side = "NO"
            edge = live_price - 0.50   # positive only when YES is above 50¢ (NO is cheap)

        if edge < ENTRY_THRESHOLD:
            if not quiet:
                print(
                    f"  ⏸️  {m.get('question', '')[:50]}: edge {edge:.3f} < {ENTRY_THRESHOLD:.3f} — skip"
                )
            continue

        budget_remaining = DAILY_BUDGET - spend["spent"]
        trade_size = min(MAX_POSITION_USD, budget_remaining)
        if trade_size < 1.0:
            break

        question = m.get("question", m.get("slug", "Unknown"))
        market_id = _market_id(m)

        if not quiet:
            print(f"\n  📈 Entry: {question[:60]}")
            print(f"     Side: {side} | Price: {live_price:.3f} | Size: ${trade_size:.2f} | {hours_left:.1f}h left")

        if live:
            try:
                result = client.trade(
                    **_trade_kwargs(
                        market_id=market_id,
                        side=side.lower(),
                        action="buy",
                        amount=trade_size,
                        venue="polymarket",
                        source=TRADE_SOURCE,
                        skill_slug=SKILL_SLUG,
                    )
                )
                if not _trade_succeeded(result):
                    err = None
                    if isinstance(result, dict):
                        err = result.get("error")
                    else:
                        err = getattr(result, "error", None)
                    print(f"  ❌ Entry failed: {err or result}")
                else:
                    log_trade(
                        market_id=market_id,
                        side=side,
                        amount_usd=trade_size,
                        action="entry",
                        entry_price=live_price,
                        source=TRADE_SOURCE,
                    )
                    spend["spent"] += trade_size
                    spend["trades"] += 1
                    _save_daily_spend(spend)
                    print(f"  ✅ Entered: {result}")
                    entered += 1
            except Exception as e:
                print(f"  ❌ Entry failed: {e}")
        else:
            print(f"  [DRY RUN] Would buy {side} ~${trade_size:.2f}")
            entered += 1

        # One entry per scan cycle to avoid over-concentration
        break

    if not quiet:
        print(f"\n  Entries: {entered}")

    return entered


# =============================================================================
# Automaton output (required for AUTOMATON_MANAGED=1 harness)
# =============================================================================

def _emit_automaton_output(positions, markets, config_snapshot):
    """Emit the JSON automaton block on stdout when managed by harness."""
    global _automaton_reported
    if _automaton_reported:
        return
    _automaton_reported = True

    block = {
        "automaton": {
            "skill": SKILL_SLUG,
            "version": "1.2.3",
            "status": "running",
            "open_positions": len(positions),
            "active_markets_found": len(markets),
            "config": {
                "exit_before_resolution_hours": EXIT_BEFORE_RESOLUTION_HOURS,
                "volume_spike_exit_multiplier": VOLUME_SPIKE_EXIT_MULTIPLIER,
                "target_hit_capture_pct": TARGET_HIT_CAPTURE_PCT,
                "max_position_usd": MAX_POSITION_USD,
                "daily_budget_usd": DAILY_BUDGET,
            },
        }
    }
    print(json.dumps(block))


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Simmer BTC Up-Down Trader — exit-disciplined daily/weekly UP/DOWN markets"
    )
    parser.add_argument("--live", action="store_true", help="Execute real trades (default: dry run)")
    parser.add_argument("--monitor", action="store_true", help="Only run exit monitor, skip entry scan")
    parser.add_argument("--positions", action="store_true", help="Show open positions and exit trigger status")
    parser.add_argument("--config", action="store_true", help="Show current configuration")
    parser.add_argument(
        "--set",
        metavar="KEY=VALUE",
        nargs="+",
        help="Update config (e.g. --set exit_before_resolution_hours=2.0)",
    )
    parser.add_argument("--quiet", action="store_true", help="Only output on trades/errors")
    args = parser.parse_args()

    # Handle config update
    if args.set:
        updates = {}
        for kv in args.set:
            if "=" not in kv:
                print(f"Invalid --set format: {kv} (expected KEY=VALUE)")
                continue
            k, v = kv.split("=", 1)
            if k in CONFIG_SCHEMA:
                cast = CONFIG_SCHEMA[k]["type"]
                try:
                    updates[k] = cast(v)
                except ValueError:
                    print(f"Invalid value for {k}: {v}")
            else:
                print(f"Unknown config key: {k}")
        if updates:
            update_config(updates, __file__, slug=SKILL_SLUG)
            print(f"Config updated: {updates}")
        return

    # Show config
    if args.config:
        config_path = get_config_path(__file__)
        print("⚙️  BTC Up-Down Trader Configuration")
        print("=" * 50)
        print(f"Config file: {config_path}")
        print()
        print("Exit discipline:")
        print(f"  exit_before_resolution_hours  = {EXIT_BEFORE_RESOLUTION_HOURS}h")
        print(f"  volume_spike_exit_multiplier  = {VOLUME_SPIKE_EXIT_MULTIPLIER}x")
        print(f"  target_hit_capture_pct        = {TARGET_HIT_CAPTURE_PCT * 100:.0f}%")
        print()
        print("Entry:")
        print(f"  entry_threshold               = {ENTRY_THRESHOLD}")
        print(f"  min_momentum_pct              = {MIN_MOMENTUM_PCT}%")
        print(f"  max_position                  = ${MAX_POSITION_USD:.2f}")
        print(f"  daily_budget                  = ${DAILY_BUDGET:.2f}")
        print(f"  min_hours_to_resolution       = {MIN_HOURS_TO_RESOLUTION}h")
        return

    if not args.quiet:
        mode = "LIVE" if args.live else "DRY RUN"
        print(f"🪙  Simmer BTC Up-Down Trader [{mode}]")
        print("=" * 50)

    client = get_client(live=args.live)

    # Emit automaton block if managed
    if os.environ.get("AUTOMATON_MANAGED") == "1":
        try:
            positions = get_open_positions(client)
            markets = fetch_btc_updown_markets()
        except Exception:
            positions, markets = [], []
        _emit_automaton_output(positions, markets, cfg)

    # --positions mode
    if args.positions:
        positions = get_open_positions(client)
        if not positions:
            print("No open BTC UP/DOWN positions.")
            return
        print(f"\n📊 Open BTC UP/DOWN positions ({len(positions)}):\n")
        for p in positions:
            print(f"  {p.get('market_id', 'unknown')} | {p.get('side')} | qty: {p.get('quantity')}")
        return

    # Run exit monitor first (always)
    run_exit_monitor(client, live=args.live, quiet=args.quiet)

    # Entry scan (skip if --monitor flag)
    if not args.monitor:
        run_entry_scan(client, live=args.live, quiet=args.quiet)

    spend = _load_daily_spend()
    if not args.quiet:
        print(f"\n💰 Daily spend: ${spend['spent']:.2f} / ${DAILY_BUDGET:.2f} ({spend['trades']} trades)")


if __name__ == "__main__":
    main()
