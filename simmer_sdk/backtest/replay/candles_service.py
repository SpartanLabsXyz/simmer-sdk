# vendored from simmer_v3/replay/candles_service.py @ befeed1b328b
# DO NOT EDIT HERE — regenerate via scripts/sync_replay_engine.py
"""Candles data-plane service (SIM-3070 Phase 1.5C — spec §C).

Serves historical Binance klines from the public data.binance.vision monthly
archive via BinanceKlineStore (closed-candles-only — the look-ahead gate
lives in the store). This is the historical signal plane that unblocks the
Binance trio (fast-loop / fast-scaler / btc-up-down) from hardcoding
`urlopen(api.binance.com)` in their decision paths.

Coverage contract (the `complete` flag): the monthly archive only contains
COMPLETE months, so a window extending past the last archived month is
served partially with `complete: false`. The SDK helper falls back to live
Binance for the uncovered tail ONLY when the server says complete=false —
the replay server always answers complete=true (its tape IS the world), so
a replayed skill can never fall back into future data.

Sync DuckDB store behind asyncio.to_thread; one store singleton per process
with a lock serializing archive downloads (a cold month is a ~10-40MB zip —
no stampedes).
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger("replay.candles")

ALLOWED_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")
# Store is interval-generic (monthly archive has per-interval files); this
# list is the serve-side bound. 30m is btc-up-down's signal interval.
ALLOWED_INTERVALS = ("1m", "5m", "30m", "1h")
MAX_WINDOW = timedelta(days=8)  # bounded response: 8d of 1m = ~11.5K candles
# Public-endpoint warm bound (Codex P2): with a window cap alone, a looping
# caller could force every historical month of every symbol into the cache.
# The floor bounds warmable archives to months we actually replay against.
EARLIEST_START = datetime(2026, 1, 1, tzinfo=timezone.utc)

DEFAULT_KLINES_CACHE_DIR = "/tmp/klines-cache"

_store = None
_store_lock = threading.Lock()


def klines_cache_dir() -> str:
    """The on-disk parquet cache shared by every BinanceKlineStore in this
    process (the HTTP candles singleton AND each per-job replay store)."""
    return os.environ.get("REPLAY_KLINES_CACHE_DIR", DEFAULT_KLINES_CACHE_DIR)


def build_kline_store(offline: bool = False):
    """Construct a FRESH BinanceKlineStore over the shared on-disk cache.

    Use this from callers that own the store lifecycle (the replay engine
    builds one per job and close()s it after). Do NOT use the _get_store()
    singleton there — close()ing it would break the long-lived HTTP candles
    endpoint that shares this process. The on-disk parquet cache is shared,
    so per-job stores still reuse already-materialized months."""
    from .feeds.binance import BinanceKlineStore
    return BinanceKlineStore(klines_cache_dir(), offline=offline)


def _get_store():
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = build_kline_store()
    return _store


def _parse_ts(value: str, name: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError(f"{name} is not ISO-parseable: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def validate_params(symbol: str, interval: str, start: str, end: str) -> tuple:
    """Raises ValueError with a user-facing message on any bad input."""
    sym = (symbol or "").upper()
    if sym not in ALLOWED_SYMBOLS:
        raise ValueError(f"symbol must be one of {ALLOWED_SYMBOLS}")
    if interval not in ALLOWED_INTERVALS:
        raise ValueError(f"interval must be one of {ALLOWED_INTERVALS}")
    t0 = _parse_ts(start, "start")
    t1 = _parse_ts(end, "end")
    if t1 <= t0:
        raise ValueError("end must be after start")
    if t1 - t0 > MAX_WINDOW:
        raise ValueError(f"window too large (max {MAX_WINDOW.days} days per request)")
    if t0 < EARLIEST_START:
        raise ValueError(f"start must be >= {EARLIEST_START.date()} (archive floor)")
    return sym, interval, t0, t1


def _archive_horizon(now: Optional[datetime] = None) -> datetime:
    """First instant NOT covered by the monthly archive — i.e. the start of
    the current month (the in-progress month has no monthly zip yet)."""
    now = now or datetime.now(timezone.utc)
    return datetime(now.year, now.month, 1, tzinfo=timezone.utc)


async def get_candles(symbol: str, interval: str, start: str, end: str,
                      now: Optional[datetime] = None,
                      store=None) -> dict[str, Any]:
    """Validated, bounded, coverage-honest candle read."""
    sym, ivl, t0, t1 = validate_params(symbol, interval, start, end)
    horizon = _archive_horizon(now)

    served_t1 = min(t1, horizon)
    complete = t1 <= horizon
    candles: list[dict] = []
    if served_t1 > t0:
        if store is None:
            store = _get_store()
        klines = await asyncio.to_thread(store.klines, sym, ivl, t0, served_t1)
        candles = [
            {
                "open_time": k.open_time.isoformat(),
                "close_time": k.close_time.isoformat(),
                "open": k.open, "high": k.high, "low": k.low, "close": k.close,
                "volume": k.volume,
            }
            for k in klines
        ]
    return {
        "symbol": sym,
        "interval": ivl,
        "start": t0.isoformat(),
        "end": t1.isoformat(),
        # served_through = "the REQUEST RANGE is covered through here" —
        # i.e. max(start, min(end, horizon)). The SDK's live tail starts
        # exactly at served_through, so when nothing in the window is
        # archived (start past horizon), served_through == start and the
        # tail spans exactly the request. Reporting the raw archive horizon
        # instead would start the tail BEFORE the requested window and
        # truncate it at Binance's 1000-candle page limit (Codex pass-1
        # suggestion declined for this reason — this field is tail-start
        # math, not an archive-coverage audit field).
        "served_through": served_t1.isoformat() if served_t1 > t0 else t0.isoformat(),
        # complete=false → the caller's window extends past archive coverage;
        # the SDK helper may fetch the uncovered tail from live Binance.
        # The REPLAY server's variant of this endpoint always returns
        # complete=true so replayed skills can never fall back to live data.
        "complete": complete,
        "candles": candles,
        "source": "binance-monthly-archive",
    }
