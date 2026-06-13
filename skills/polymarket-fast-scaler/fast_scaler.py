#!/usr/bin/env python3
"""
Simmer FastScaler Trading Skill

Trades Polymarket BTC 5-minute fast markets using Binance 1m momentum at window-open
with a magnitude gate and conviction-ladder position sizing.

Strategy invariants (DO NOT change without re-running the backtest):
  - Side-picker:    momentum direction only — up → YES, down → NO. No divergence filter.
  - Magnitude gate: |momentum| >= magnitude_gate_pct (default 0.10%). Below = no trade.
  - Sizing ladder:  3 tiers keyed by |momentum|. Stronger signal → larger position.
  - Fee formula:    fee = shares * 0.07 * p * (1-p). Crypto taker category.
  - Hold policy:    no exit. Every position held to expiry (binary win/lose).

Backtest RETRACTED (2026-06-12): the original +5.04% / 89.4% backtest used the 1m
candle that starts at window-open (a look-ahead candle that only closes 60s into
the window). The signal available at the decision point shows no measured edge.
Unvalidated reference template; see SKILL.md retraction note + DISCLAIMER.md.

Usage:
    python fast_scaler.py              # Dry run (paper prices, no real trades)
    python fast_scaler.py --live       # Execute real trades
    python fast_scaler.py --positions  # Show open fast market positions
    python fast_scaler.py --quiet      # Only output on trades/errors (ideal for cron)
"""

import os
import sys
import json
import argparse
from datetime import datetime, timezone
from urllib.request import urlopen, Request
from urllib.error import HTTPError, URLError
from urllib.parse import quote

# Force line-buffered stdout for cron / Docker / OpenClaw environments
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
    # magnitude_gate_pct defaults to 0.10%. The original 89.4%/+5.04% backtest was
    # retracted (look-ahead; see module docstring) — this is a design default, not a
    # validated profit threshold. Lowering it admits more low-magnitude noise.
    "magnitude_gate_pct": {
        "default": 0.10,
        "env": "SIMMER_FASTSCALER_MAGNITUDE_GATE",
        "type": float,
        "help": "Min |1m BTC momentum %| to open a position. Strategy invariant — don't lower below 0.10.",
    },
    # Conviction ladder thresholds (tier boundaries in |momentum| %)
    "ladder_tier2_pct": {
        "default": 0.20,
        "env": "SIMMER_FASTSCALER_LADDER_T2",
        "type": float,
        "help": "Momentum % threshold to enter tier 2 sizing (default 0.20%)",
    },
    "ladder_tier3_pct": {
        "default": 0.35,
        "env": "SIMMER_FASTSCALER_LADDER_T3",
        "type": float,
        "help": "Momentum % threshold to enter tier 3 (max) sizing (default 0.35%)",
    },
    # Conviction ladder position sizes (USD per trade for each tier)
    "position_tier1_usd": {
        "default": 3.0,
        "env": "SIMMER_FASTSCALER_POS_T1",
        "type": float,
        "help": "Position size for tier 1 (gate <= |mom| < tier2) in USD",
    },
    "position_tier2_usd": {
        "default": 5.0,
        "env": "SIMMER_FASTSCALER_POS_T2",
        "type": float,
        "help": "Position size for tier 2 (tier2 <= |mom| < tier3) in USD",
    },
    "position_tier3_usd": {
        "default": 10.0,
        "env": "SIMMER_FASTSCALER_POS_T3",
        "type": float,
        "help": "Position size for tier 3 (|mom| >= tier3) in USD",
    },
    # Budget controls
    "daily_budget_usd": {
        "default": 30.0,
        "env": "SIMMER_FASTSCALER_DAILY_BUDGET",
        "type": float,
        "help": "Max total USD spend per UTC day across all trades",
    },
    "per_market_cap_usd": {
        "default": 10.0,
        "env": "SIMMER_FASTSCALER_PER_MARKET_CAP",
        "type": float,
        "help": "Max USD allowed on a single market window (prevents runaway on one slot)",
    },
    # Asset / window (v1.0 supports BTC 5m only — other assets/windows need their own backtests)
    "asset": {
        "default": "BTC",
        "env": "SIMMER_FASTSCALER_ASSET",
        "type": str,
        "help": "Asset to trade. BTC is the only backtested asset in v1.0.",
    },
    "window": {
        "default": "5m",
        "env": "SIMMER_FASTSCALER_WINDOW",
        "type": str,
        "help": "Market window. 5m is the only backtested window in v1.0.",
    },
    "order_type": {
        "default": "GTC",
        "env": "SIMMER_FASTSCALER_ORDER_TYPE",
        "type": str,
        "help": "Polymarket order type: GTC (wait for fill) or FAK (cancel if not immediate).",
    },
}

TRADE_SOURCE = "sdk:fastscaler"
SKILL_SLUG = "polymarket-fast-scaler"
_automaton_reported = False

MIN_SHARES_PER_ORDER = 5    # Polymarket minimum order size in shares
MAX_SPREAD_PCT = 0.10       # Skip if CLOB bid-ask spread exceeds this
POLY_FEE_RATE_CRYPTO = 0.07  # Crypto taker fee coefficient (docs.polymarket.com/trading/fees)

ASSET_SYMBOLS = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}

from simmer_sdk.skill import load_config, update_config, get_config_path

cfg = load_config(CONFIG_SCHEMA, __file__, slug=SKILL_SLUG)

MAGNITUDE_GATE_PCT = cfg["magnitude_gate_pct"]
LADDER_T2_PCT = cfg["ladder_tier2_pct"]
LADDER_T3_PCT = cfg["ladder_tier3_pct"]
POS_T1_USD = cfg["position_tier1_usd"]
POS_T2_USD = cfg["position_tier2_usd"]
POS_T3_USD = cfg["position_tier3_usd"]
DAILY_BUDGET = cfg["daily_budget_usd"]
PER_MARKET_CAP = cfg["per_market_cap_usd"]
ASSET = cfg["asset"].upper()
WINDOW = cfg["window"]
ORDER_TYPE = (cfg["order_type"] or "GTC").upper()

_window_seconds = {"5m": 300, "15m": 900, "1h": 3600}
MIN_TIME_REMAINING = max(30, _window_seconds.get(WINDOW, 300) // 10)


# =============================================================================
# Conviction Ladder
# =============================================================================

def conviction_size(momentum_abs_pct, max_override=None):
    """Return position size in USD for the given |momentum| %.

    Tiers:
      T1: gate   <= |m| < t2  → POS_T1_USD (small)
      T2: t2     <= |m| < t3  → POS_T2_USD (medium)
      T3: t3     <= |m|       → POS_T3_USD (max)

    All tiers are capped by PER_MARKET_CAP and the optional max_override.
    """
    if momentum_abs_pct >= LADDER_T3_PCT:
        size = POS_T3_USD
        tier = 3
    elif momentum_abs_pct >= LADDER_T2_PCT:
        size = POS_T2_USD
        tier = 2
    else:
        size = POS_T1_USD
        tier = 1
    size = min(size, PER_MARKET_CAP)
    if max_override is not None:
        size = min(size, max_override)
    return size, tier


# =============================================================================
# Daily Budget Tracking
# =============================================================================

def _get_spend_path():
    from pathlib import Path
    return Path(__file__).parent / "daily_spend.json"


def _load_daily_spend():
    """Load today's spend. Resets automatically at UTC midnight."""
    spend_path = _get_spend_path()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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
    spend_path = _get_spend_path()
    tmp = spend_path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(spend_data, f, indent=2)
    tmp.replace(spend_path)


# =============================================================================
# Auto-redeem cooldown
# =============================================================================

_REDEEM_COOLDOWN_S = 600  # 10 minutes between auto-redeem attempts


def _should_run_auto_redeem() -> bool:
    """Rate-limit auto_redeem to at most once per 10 minutes.

    auto_redeem() makes 2+ sequential API calls (each up to 30s timeout) and,
    for managed wallets with many redeemable positions, can chain many POST
    /api/sdk/redeem calls. On a degraded backend the cumulative timeout can
    consume the entire 1-minute cron window, leaving no time for market
    discovery.  The cooldown file ensures that at worst one cycle per 10
    minutes is spent on redemption, keeping the other cycles < 30s.
    """
    from pathlib import Path
    import time as _time
    cooldown_file = Path(__file__).parent / ".last_auto_redeem"
    now = _time.time()
    if cooldown_file.exists():
        try:
            last = float(cooldown_file.read_text().strip())
            if now - last < _REDEEM_COOLDOWN_S:
                return False
        except (ValueError, IOError):
            pass
    try:
        cooldown_file.write_text(str(now))
    except IOError:
        pass
    return True


# =============================================================================
# API Helpers
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
            print("Get your API key from: simmer.markets/dashboard → SDK tab")
            sys.exit(1)
        venue = os.environ.get("TRADING_VENUE", "polymarket")
        _client = SimmerClient(api_key=api_key, venue=venue, live=live)
    return _client


def _api_request(url, timeout=15):
    """GET request to external APIs (Binance, Polymarket CLOB). Returns JSON or None."""
    try:
        req = Request(url, headers={"User-Agent": "simmer-fastscaler/1.0"})
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
            return {"error": body.get("detail", str(e)), "status_code": e.code}
        except Exception:
            return {"error": str(e), "status_code": e.code}
    except (URLError, Exception) as e:
        return {"error": str(e)}


CLOB_API = "https://clob.polymarket.com"


def fetch_live_midpoint(token_id):
    """Fetch live YES midpoint price from Polymarket CLOB."""
    result = _api_request(f"{CLOB_API}/midpoint?token_id={quote(str(token_id))}", timeout=5)
    if not result or result.get("error"):
        return None
    try:
        return float(result["mid"])
    except (KeyError, ValueError, TypeError):
        return None


def fetch_orderbook_spread(clob_token_ids):
    """Return spread_pct for the YES token, or None on failure."""
    if not clob_token_ids:
        return None
    yes_token = clob_token_ids[0]
    result = _api_request(f"{CLOB_API}/book?token_id={quote(str(yes_token))}", timeout=5)
    if not result or not isinstance(result, dict):
        return None
    bids = result.get("bids", [])
    asks = result.get("asks", [])
    if not bids or not asks:
        return None
    try:
        best_bid = float(bids[0]["price"])
        best_ask = float(asks[0]["price"])
        mid = (best_bid + best_ask) / 2
        return (best_ask - best_bid) / mid if mid > 0 else None
    except (KeyError, ValueError, IndexError, TypeError):
        return None


# =============================================================================
# Market Discovery
# =============================================================================

def discover_fast_markets(asset="BTC", window="5m"):
    """Find active fast markets.

    Primary: Simmer SDK get_fast_markets() — pre-imported, is_live_now computed server-side.
    Fallback: Gamma API — covers import gaps when Simmer DB is behind live slot publishing.
    """
    markets = []
    try:
        client = get_client()
        sdk_markets = client.get_fast_markets(asset=asset, window=window, limit=50)
        for m in sdk_markets or []:
            end_time = _parse_resolves_at(m.resolves_at) if m.resolves_at else None
            clob_tokens = [m.polymarket_token_id] if m.polymarket_token_id else []
            if m.polymarket_no_token_id:
                clob_tokens.append(m.polymarket_no_token_id)
            markets.append({
                "question": m.question,
                "market_id": m.id,
                "end_time": end_time,
                "clob_token_ids": clob_tokens,
                "is_live_now": m.is_live_now,
                "spread_cents": m.spread_cents,
                "liquidity_tier": m.liquidity_tier,
                "source": "simmer",
            })
    except Exception as e:
        print(f"  ⚠️  Simmer fast-markets API failed ({e}), falling back to Gamma")

    has_live = any(m.get("is_live_now") for m in markets)
    if not has_live:
        gamma_markets = _discover_via_gamma(asset, window)
        seen_tokens = {t for m in markets for t in (m.get("clob_token_ids") or [])}
        for gm in gamma_markets:
            gtokens = gm.get("clob_token_ids") or []
            if not any(t in seen_tokens for t in gtokens):
                markets.append(gm)

    return markets


def _discover_via_gamma(asset="BTC", window="5m"):
    """Gamma API fallback for market discovery."""
    patterns = {
        "BTC": ["bitcoin up or down"],
        "ETH": ["ethereum up or down"],
        "SOL": ["solana up or down"],
    }.get(asset, ["bitcoin up or down"])

    url = (
        "https://gamma-api.polymarket.com/markets/keyset"
        "?limit=100&closed=false&tag=crypto&order=endDate&ascending=true"
    )
    result = _api_request(url)
    if not result or not isinstance(result, dict) or result.get("error"):
        return []

    markets = []
    for m in result.get("markets", []):
        q = (m.get("question") or "").lower()
        slug = m.get("slug", "")
        if any(p in q for p in patterns) and f"-{window}-" in slug and not m.get("closed"):
            end_time = _parse_gamma_end_time(m.get("question", ""))
            clob_raw = m.get("clobTokenIds", "[]")
            try:
                clob_tokens = json.loads(clob_raw) if isinstance(clob_raw, str) else clob_raw
            except (json.JSONDecodeError, ValueError):
                clob_tokens = []
            markets.append({
                "question": m.get("question", ""),
                "slug": slug,
                "condition_id": m.get("conditionId", ""),
                "market_id": None,
                "end_time": end_time,
                "clob_token_ids": clob_tokens or [],
                "source": "gamma",
            })
    return markets


def _parse_resolves_at(resolves_at_str):
    try:
        s = resolves_at_str.replace("Z", "+00:00").replace(" ", "T")
        dt = datetime.fromisoformat(s)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except Exception:
        return None


def _parse_gamma_end_time(question):
    import re
    match = re.search(r'(\w+ \d+),.*?-\s*(\d{1,2}:\d{2}(?:AM|PM))\s*ET', question)
    if not match:
        return None
    try:
        from zoneinfo import ZoneInfo
        year = datetime.now(timezone.utc).year
        dt = datetime.strptime(f"{match.group(1)} {year} {match.group(2)}", "%B %d %Y %I:%M%p")
        return dt.replace(tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    except Exception:
        return None


def find_best_fast_market(markets):
    """Select the best market: live now, most time remaining above MIN_TIME_REMAINING."""
    now = datetime.now(timezone.utc)
    max_remaining = _window_seconds.get(WINDOW, 300) * 2
    candidates = []
    for m in markets:
        if m.get("is_live_now") is not None:
            if not m["is_live_now"]:
                continue
            end_time = m.get("end_time")
            if end_time:
                remaining = (end_time - now).total_seconds()
                if remaining > MIN_TIME_REMAINING:
                    candidates.append((remaining, m))
        else:
            end_time = m.get("end_time")
            if not end_time:
                continue
            remaining = (end_time - now).total_seconds()
            if MIN_TIME_REMAINING < remaining < max_remaining:
                candidates.append((remaining, m))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[-1][1]  # last = most time remaining (ascending sort)


# =============================================================================
# Signal — Binance 1m Momentum
# =============================================================================

def _is_replay():
    """True inside the Simmer replay harness (SIMMER_REPLAY=1). Decision data
    must then come ONLY from the Simmer API — a direct-vendor fetch would be
    future data relative to the frozen tick (the exact class behind this
    skill's 89.4% backtest retraction)."""
    return os.environ.get("SIMMER_REPLAY") == "1"


def _momentum_from_closed_candles(candles):
    """Momentum dict from the LAST CLOSED candle, volume context from the
    rest. Same semantics as the legacy index -2 rule: a settled signal,
    never the in-progress candle."""
    if not candles or len(candles) < 1:
        return None
    candle = candles[-1]
    price_open = float(candle["open"])
    price_close = float(candle["close"])
    volume = float(candle["volume"])
    avg_volume = sum(float(c["volume"]) for c in candles) / len(candles)
    momentum_pct = (price_close - price_open) / price_open * 100
    return {
        "momentum_pct": momentum_pct,
        "abs_momentum_pct": abs(momentum_pct),
        # momentum_pct == 0 → "down" is unreachable; magnitude gate filters |mom| > 0
        "direction": "up" if momentum_pct > 0 else "down",
        "price_now": price_close,
        "price_open": price_open,
        "volume": volume,
        "volume_ratio": volume / avg_volume if avg_volume > 0 else 1.0,
    }


def get_binance_1m_momentum(asset="BTC"):
    """Momentum % from the last CLOSED 1-minute candle.

    Primary source: Simmer's data plane (client.get_candles — closed candles
    only, one code path live and under replay). Legacy direct Binance is a
    LIVE-ONLY fallback for servers without the data plane; under replay it
    never fires (returns None → no signal this tick).

    Using 1m lookback (window-open candle) is the backtested signal source.
    Do not increase lookback without re-running the backtest.

    Returns dict with: momentum_pct, direction, price_now, price_open,
    volume_ratio — or None on failure.
    """
    symbol = ASSET_SYMBOLS.get(asset, "BTCUSDT")

    # No API key → no client (get_client() sys.exits). Checked up-front so the
    # sys.exit never fires inside the signal fetch; live falls back to legacy,
    # replay yields no-signal.
    if os.environ.get("SIMMER_API_KEY"):
        try:
            from datetime import datetime, timedelta, timezone

            end = datetime.now(timezone.utc)
            start = end - timedelta(minutes=4)  # ≥3 closed candles for volume context
            plane = get_client().get_candles(symbol, start.isoformat(), end.isoformat())
            if plane:
                return _momentum_from_closed_candles(plane)
            if _is_replay():
                return None  # empty tape window — honest no-signal, never fall back
        except Exception:  # noqa: BLE001 — branch on environment below
            if _is_replay():
                return None
    elif _is_replay():
        return None  # can't reach the plane under replay → no-signal, never legacy

    # ---- legacy LIVE-ONLY direct path (never reached under replay) ----
    # Try global endpoint first; fall back to US endpoint for geo-restricted deployments (HTTP 451)
    result = None
    for base in ("https://api.binance.com", "https://api.binance.us"):
        url = f"{base}/api/v3/klines?symbol={symbol}&interval=1m&limit=3"
        result = _api_request(url)
        if result and not isinstance(result, dict):
            break
        result = None
    if result is None:
        return None
    try:
        # Use the last complete candle (index -2) for a settled signal.
        # Index -1 is the currently forming candle — momentum still evolving.
        if len(result) < 2:
            return None
        candle = result[-2]
        price_open = float(candle[1])
        price_close = float(candle[4])
        volume = float(candle[5])

        # Context: average volume from all fetched candles
        avg_volume = sum(float(c[5]) for c in result) / len(result)

        momentum_pct = (price_close - price_open) / price_open * 100
        return {
            "momentum_pct": momentum_pct,
            "abs_momentum_pct": abs(momentum_pct),
            # momentum_pct == 0 → "down" is unreachable; magnitude gate filters |mom| > 0
            "direction": "up" if momentum_pct > 0 else "down",
            "price_now": price_close,
            "price_open": price_open,
            "volume": volume,
            "volume_ratio": volume / avg_volume if avg_volume > 0 else 1.0,
        }
    except (IndexError, ValueError, KeyError):
        return None


# =============================================================================
# Trade Execution
# =============================================================================

def execute_buy(client, market_id, side, cost_usdc, signal_data=None, dry_run=False):
    """Execute a trade via the Simmer SDK client.

    Returns dict with: success, trade_id, shares_bought, simulated, error
    """
    try:
        result = client.trade(
            market_id=market_id,
            side=side,
            amount=cost_usdc,
            order_type=ORDER_TYPE,
            source=TRADE_SOURCE,
            skill_slug=SKILL_SLUG,
            signal_data=signal_data,
        )
        return {
            "success": result.success,
            "trade_id": result.trade_id,
            "shares_bought": result.shares_bought,
            "simulated": getattr(result, "simulated", dry_run),
            "error": None,
        }
    except Exception as e:
        return {"success": False, "trade_id": None, "shares_bought": 0, "simulated": False, "error": str(e)}


def import_market(slug):
    """Import a Gamma market into Simmer. Returns (market_id, error_str)."""
    url = f"https://polymarket.com/event/{slug}"
    try:
        result = get_client().import_market(url)
    except Exception as e:
        return None, str(e)
    if not result:
        return None, "No response"
    if result.get("error"):
        return None, result.get("error", "Unknown error")
    status = result.get("status")
    market_id = result.get("market_id")
    if status == "resolved":
        alts = result.get("active_alternatives", [])
        return None, f"Resolved. Alt: {alts[0].get('id') if alts else 'none'}"
    if status in ("imported", "already_exists"):
        return market_id, None
    return None, f"Unexpected status: {status}"


def get_positions():
    """Return current positions as list of dicts."""
    try:
        from dataclasses import asdict
        client = get_client()
        positions = client.get_positions(venue=client.venue)
        return [asdict(p) for p in positions]
    except Exception:
        return []


# =============================================================================
# Main Strategy Loop
# =============================================================================

def run_fast_scaler(dry_run=True, positions_only=False, show_config=False, quiet=False):
    """Execute one cycle of the FastScaler conviction-ladder strategy."""
    global _automaton_reported

    def log(msg, force=False):
        if not quiet or force:
            print(msg)

    log("⚡ Simmer FastScaler Trading Skill")
    log("=" * 50)
    if dry_run:
        log("\n  [PAPER MODE] Trades simulated at real prices. Use --live for real trades.")

    log(f"\n⚙️  Configuration:")
    log(f"  Asset:          {ASSET}")
    log(f"  Window:         {WINDOW}")
    log(f"  Magnitude gate: |momentum| >= {MAGNITUDE_GATE_PCT:.2f}% (strategy invariant)")
    log(f"  Ladder:         T1 ${POS_T1_USD:.0f} (<{LADDER_T2_PCT}%) / "
        f"T2 ${POS_T2_USD:.0f} (<{LADDER_T3_PCT}%) / "
        f"T3 ${POS_T3_USD:.0f} (≥{LADDER_T3_PCT}%)")
    log(f"  Per-market cap: ${PER_MARKET_CAP:.0f}")

    daily_spend = _load_daily_spend()
    log(f"  Daily budget:   ${DAILY_BUDGET:.2f} (${daily_spend['spent']:.2f} spent, "
        f"{daily_spend['trades']} trades today)")

    if show_config:
        config_path = get_config_path(__file__)
        log(f"\n  Config file: {config_path}")
        log(f"\n  To change settings:")
        log(f"    python fast_scaler.py --set magnitude_gate_pct=0.12")
        log(f"    python fast_scaler.py --set position_tier3_usd=15")
        return

    # Init client (validates API key early; paper mode when not live)
    client = get_client(live=not dry_run)

    # --- Auto-redeem: collect any winning positions from resolved markets ---
    # Throttled to once per 10 minutes — each call makes 2+ sequential SDK
    # requests; on a slow backend this can consume the full 1-min cron window.
    if _should_run_auto_redeem():
        try:
            redeemed = client.auto_redeem()
            for r in redeemed or []:
                if r.get("success"):
                    log(f"  💰 Redeemed {r.get('market_id', '')[:12]}... ({r.get('side', '?')})")
        except Exception:
            pass  # Non-critical

    # --- Balance pre-flight ---
    if not dry_run:
        preflight = client.ensure_can_trade(min_usd=max(1.0, POS_T1_USD * 0.5))
        if not preflight["ok"]:
            log(
                f"  ⏸️  insufficient_balance: ${preflight['balance']:.2f} {preflight['collateral']} "
                f"(need ≥ $1.00) — skip",
                force=True,
            )
            if os.environ.get("AUTOMATON_MANAGED"):
                print(json.dumps({"automaton": {
                    "signals": 0, "trades_attempted": 0, "trades_executed": 0,
                    "skip_reason": preflight["reason"],
                    "balance_usd": round(preflight["balance"], 2),
                }}))
                _automaton_reported = True
            return

    # --- GTC stale order cleanup ---
    if ORDER_TYPE == "GTC" and not dry_run:
        try:
            open_orders = client.get_open_orders()
            for order in (open_orders or {}).get("orders", []):
                source = (order.get("source") or "").lower()
                slug = (order.get("skill_slug") or "").lower()
                question = (order.get("question") or "").lower()
                # Match only on source/slug attribution. Earlier versions
                # also matched on `"up or down" in question` as a fallback,
                # but that pattern cross-contaminates with mert-sniper and
                # other Crypto-fast-market skills running on the same wallet
                # under the same API key — fast-scaler would cancel their
                # GTC orders at cycle start.
                is_ours = source == TRADE_SOURCE or slug == SKILL_SLUG
                if not is_ours:
                    continue
                oid = order.get("order_id") or order.get("id")
                if oid:
                    res = client.cancel_order(oid)
                    if res.get("success"):
                        log(f"  🧹 Cancelled stale GTC order {oid[:16]}...")
        except Exception as e:
            log(f"  ⚠️  GTC cleanup failed (non-fatal): {e}")

    # --- Show positions mode ---
    if positions_only:
        log("\n📊 FastScaler Positions:")
        positions = get_positions()
        fast = [p for p in positions if "up or down" in (p.get("question") or "").lower()]
        if not fast:
            log("  No open fast market positions")
        for pos in fast:
            log(f"  • {(pos.get('question') or '')[:60]}")
            log(f"    YES: {pos.get('shares_yes', 0):.1f} | NO: {pos.get('shares_no', 0):.1f} | P&L: ${pos.get('pnl', 0):.2f}")
        return

    # --- Step 1: Discover fast markets ---
    log(f"\n🔍 Discovering {ASSET} fast markets...")
    markets = discover_fast_markets(ASSET, WINDOW)
    log(f"  Found {len(markets)} active fast markets")

    if not markets:
        log("  No active fast markets — may be outside market hours or wrong asset/window")
        _emit_automaton(signals=0, attempted=0, executed=0, skip_reason="no_markets", markets_found=0)
        return

    # --- Step 2: Select best market ---
    best = find_best_fast_market(markets)
    if not best:
        now = datetime.now(timezone.utc)
        for m in markets:
            end_time = m.get("end_time")
            if m.get("is_live_now") is False:
                log(f"  Skipped (not live): {(m.get('question') or '')[:50]}...")
            elif end_time:
                secs = (end_time - now).total_seconds()
                log(f"  Skipped ({secs:.0f}s < {MIN_TIME_REMAINING}s min): {(m.get('question') or '')[:50]}...")
        log(f"  No tradeable market (0/{len(markets)} live with enough time)")
        _emit_automaton(signals=0, attempted=0, executed=0, skip_reason="no_live_market")
        return

    end_time = best.get("end_time")
    remaining = (end_time - datetime.now(timezone.utc)).total_seconds() if end_time else 0
    log(f"\n🎯 Selected: {best['question']}")
    log(f"  Expires in: {remaining:.0f}s")

    # --- Dedup: skip if already holding this market ---
    mid = best.get("market_id") or ""
    bq = (best.get("question") or "").lower()
    for pos in get_positions():
        held = (pos.get("shares_yes") or 0) + (pos.get("shares_no") or 0)
        if held <= 0:
            continue
        pq = (pos.get("question") or "").lower()
        if (mid and pos.get("market_id") == mid) or (bq and pq == bq):
            log(f"  ⏸️  Already holding position on this market — skip (dedup)")
            _emit_automaton(signals=0, attempted=0, executed=0, skip_reason="already_holding")
            return

    # --- Fetch live CLOB price ---
    clob_tokens = best.get("clob_token_ids", [])
    if not clob_tokens:
        log(f"  ⏸️  No CLOB token IDs — cannot fetch live price")
        _emit_automaton(signals=0, attempted=0, executed=0, skip_reason="no_clob_tokens")
        return

    market_yes_price = fetch_live_midpoint(clob_tokens[0])
    if market_yes_price is None:
        log(f"  ⏸️  Could not fetch live CLOB price — stale prices are unsafe on fast markets")
        _emit_automaton(signals=0, attempted=0, executed=0, skip_reason="clob_price_unavailable")
        return
    log(f"  Current YES price: ${market_yes_price:.3f} (live CLOB)")

    # --- Check spread ---
    pre_spread_cents = best.get("spread_cents")
    if pre_spread_cents is not None:
        mid_est = market_yes_price if market_yes_price > 0 else 0.5
        spread_pct = (pre_spread_cents / 100.0) / mid_est
        log(f"  Spread: {pre_spread_cents:.1f}¢ ({best.get('liquidity_tier', 'unknown')})")
    else:
        spread_pct = fetch_orderbook_spread(clob_tokens) or 0.0
        log(f"  Spread: {spread_pct:.1%} (live CLOB)")

    if spread_pct > MAX_SPREAD_PCT:
        log(f"  ⏸️  Spread {spread_pct:.1%} > {MAX_SPREAD_PCT:.0%} — illiquid, skip")
        _emit_automaton(signals=0, attempted=0, executed=0, skip_reason="wide_spread")
        return

    # --- Step 3: Get 1m Binance momentum signal ---
    log(f"\n📈 Fetching {ASSET} 1m momentum (Binance)...")
    signal = get_binance_1m_momentum(ASSET)

    if not signal:
        log("  ❌ Failed to fetch Binance price data", force=True)
        _emit_automaton(signals=0, attempted=0, executed=0, skip_reason="signal_fetch_failed")
        return

    momentum_pct = signal["abs_momentum_pct"]
    direction = signal["direction"]
    log(f"  1m candle: ${signal['price_open']:,.2f} → ${signal['price_now']:,.2f}")
    log(f"  Momentum: {signal['momentum_pct']:+.4f}% (|{momentum_pct:.4f}%|)")
    log(f"  Direction: {direction} | Volume ratio: {signal['volume_ratio']:.2f}x")

    # --- Step 4: Magnitude gate (strategy invariant) ---
    if momentum_pct < MAGNITUDE_GATE_PCT:
        log(f"  ⏸️  Momentum {momentum_pct:.4f}% < gate {MAGNITUDE_GATE_PCT:.2f}% — below threshold")
        if not quiet:
            print(f"📊 Summary: No trade (momentum {momentum_pct:.4f}% < gate {MAGNITUDE_GATE_PCT:.2f}%)")
        _emit_automaton(signals=1, attempted=0, executed=0, skip_reason="below_magnitude_gate", momentum_pct=signal["momentum_pct"])
        return

    # Gate passed — compute conviction tier and position size
    side = "yes" if direction == "up" else "no"
    buy_price = market_yes_price if side == "yes" else (1 - market_yes_price)
    position_size, tier = conviction_size(momentum_pct)

    log(f"\n🎯 Signal: {side.upper()} | Tier {tier} (|momentum|={momentum_pct:.4f}%)")

    # Log informational fee estimate (not a gate; the magnitude gate is a noise filter, not a validated EV guarantee)
    fee_per_share = POLY_FEE_RATE_CRYPTO * buy_price * (1 - buy_price)
    fee_pct_of_spend = POLY_FEE_RATE_CRYPTO * (1 - buy_price)
    log(f"  Fee: ${fee_per_share:.4f}/share ({fee_pct_of_spend:.2%} on spend at {buy_price:.3f})")

    # --- Daily budget check ---
    remaining_budget = DAILY_BUDGET - daily_spend["spent"]
    if remaining_budget <= 0:
        log(f"  ⏸️  Daily budget exhausted (${daily_spend['spent']:.2f}/${DAILY_BUDGET:.2f}) — skip")
        _emit_automaton(signals=1, attempted=0, executed=0, skip_reason="daily_budget_exhausted")
        return

    position_size = min(position_size, remaining_budget)
    if position_size < 0.50:
        log(f"  ⏸️  Remaining budget ${position_size:.2f} < $0.50 — skip")
        _emit_automaton(signals=1, attempted=0, executed=0, skip_reason="budget_too_small")
        return

    # --- Minimum order size check ---
    if buy_price > 0 and (MIN_SHARES_PER_ORDER * buy_price) > position_size:
        log(f"  ⚠️  ${position_size:.2f} insufficient for {MIN_SHARES_PER_ORDER} shares at ${buy_price:.3f}")
        _emit_automaton(signals=1, attempted=1, executed=0, skip_reason="position_too_small")
        return

    log(f"  ✅ Placing {side.upper()} ${position_size:.2f} (Tier {tier})", force=True)

    # --- Step 5: Ensure market is in Simmer (import from Gamma if needed) ---
    market_id = best.get("market_id")
    if not market_id:
        log(f"\n🔗 Importing market to Simmer...", force=True)
        market_id, err = import_market(best.get("slug", ""))
        if not market_id:
            log(f"  ❌ Import failed: {err}", force=True)
            _emit_automaton(signals=1, attempted=1, executed=0, skip_reason="import_failed")
            return
        log(f"  ✅ Market ID: {market_id[:16]}...", force=True)
    else:
        log(f"\n🔗 Market ready: {market_id[:16]}...", force=True)

    # --- Step 6: Execute trade ---
    tag = "SIMULATED" if dry_run else "LIVE"
    log(f"  Executing {side.upper()} ${position_size:.2f} ({tag})...", force=True)

    signal_data = {
        "magnitude_pct": round(momentum_pct, 4),
        "direction": direction,
        "tier": tier,
        "price_open": round(signal["price_open"], 2),
        "price_now": round(signal["price_now"], 2),
        "volume_ratio": round(signal["volume_ratio"], 2),
        "market_yes_price": round(market_yes_price, 4),
        "signal_source": "binance_1m",
    }
    trade_result = execute_buy(client, market_id, side, position_size,
                               signal_data=signal_data, dry_run=dry_run)

    if trade_result["success"]:
        shares = trade_result.get("shares_bought") or 0
        is_paper = trade_result.get("simulated") or dry_run
        log(f"  ✅ {'[PAPER] ' if is_paper else ''}Bought {shares:.1f} {side.upper()} shares @ ${buy_price:.3f}",
            force=True)

        if not is_paper:
            daily_spend["spent"] += position_size
            daily_spend["trades"] += 1
            _save_daily_spend(daily_spend)

        tid = trade_result.get("trade_id")
        if tid and JOURNAL_AVAILABLE and not is_paper:
            log_trade(
                trade_id=tid,
                source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
                thesis=f"{ASSET} {direction} {signal['momentum_pct']:+.4f}% → {side.upper()} Tier {tier}",
                confidence=round(min(0.95, 0.5 + momentum_pct * 2), 2),
                asset=ASSET,
                momentum_pct=round(signal["momentum_pct"], 4),
                volume_ratio=round(signal["volume_ratio"], 2),
            )
        _emit_automaton(signals=1, attempted=1, executed=1, amount_usd=position_size, momentum_pct=signal["momentum_pct"], tier=tier)
    else:
        error = (trade_result.get("error") or "Unknown error")[:120]
        log(f"  ❌ Trade failed: {error}", force=True)
        _emit_automaton(signals=1, attempted=1, executed=0,
                        execution_errors=[error])

    if not quiet or trade_result["success"]:
        print(f"\n📊 Summary:")
        print(f"  Market: {best['question'][:55]}")
        print(f"  Signal: {direction} {signal['momentum_pct']:+.4f}% (Tier {tier})")
        print(f"  Action: {'PAPER' if dry_run else ('TRADED' if trade_result['success'] else 'FAILED')}")


def _emit_automaton(signals=0, attempted=0, executed=0, skip_reason=None,
                    amount_usd=0, execution_errors=None, momentum_pct=None,
                    markets_found=None, tier=None):
    """Emit structured automaton JSON for the OpenClaw harness."""
    global _automaton_reported
    if not os.environ.get("AUTOMATON_MANAGED"):
        return
    report = {
        "signals": signals,
        "trades_attempted": attempted,
        "trades_executed": executed,
    }
    if amount_usd:
        report["amount_usd"] = round(amount_usd, 2)
    if skip_reason:
        report["skip_reason"] = skip_reason
    if execution_errors:
        report["execution_errors"] = execution_errors
    if momentum_pct is not None:
        report["momentum_pct"] = round(momentum_pct, 4)
    if markets_found is not None:
        report["markets_found"] = markets_found
    if tier is not None:
        report["tier"] = tier
    print(json.dumps({"automaton": report}))
    _automaton_reported = True


# =============================================================================
# CLI Entry Point
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simmer FastScaler — BTC conviction-ladder strategy")
    parser.add_argument("--live", action="store_true", help="Execute real trades (default: paper mode)")
    parser.add_argument("--dry-run", action="store_true", help="Paper mode (default)")
    parser.add_argument("--positions", action="store_true", help="Show current fast market positions")
    parser.add_argument("--config", action="store_true", help="Show current configuration")
    parser.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="Update a config key (e.g. --set magnitude_gate_pct=0.12)")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="Only output on trades/errors — ideal for cron")
    args = parser.parse_args()

    if args.set:
        updates = {}
        for item in args.set:
            if "=" not in item:
                print(f"Invalid --set format: {item}. Use KEY=VALUE")
                sys.exit(1)
            key, val = item.split("=", 1)
            if key not in CONFIG_SCHEMA:
                print(f"Unknown config key: {key}")
                print(f"Valid keys: {', '.join(CONFIG_SCHEMA.keys())}")
                sys.exit(1)
            type_fn = CONFIG_SCHEMA[key].get("type", str)
            try:
                updates[key] = val.lower() in ("true", "1", "yes") if type_fn == bool else type_fn(val)
            except ValueError:
                print(f"Invalid value for {key}: {val}")
                sys.exit(1)
        update_config(updates, __file__)
        print(f"✅ Config updated: {json.dumps(updates)}")
        sys.exit(0)

    run_fast_scaler(
        dry_run=not args.live,
        positions_only=args.positions,
        show_config=args.config,
        quiet=args.quiet,
    )

    # Fallback automaton report if strategy returned early without emitting one
    if os.environ.get("AUTOMATON_MANAGED") and not _automaton_reported:
        print(json.dumps({"automaton": {"signals": 0, "trades_attempted": 0, "trades_executed": 0,
                                        "skip_reason": "no_signal"}}))
