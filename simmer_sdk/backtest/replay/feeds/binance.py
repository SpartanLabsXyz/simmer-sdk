# vendored from simmer_v3/replay/feeds/binance.py @ fc7f82cadfd5
# DO NOT EDIT HERE — regenerate via scripts/sync_replay_engine.py
"""Binance historical klines feed for replay (SIM-3079).

Source: data.binance.vision — monthly zipped CSVs (no API key required).
Do NOT use the live REST API for bulk history.

CSV column layout (standard Binance spot klines, 0-indexed, no header row):
  0  open_time (epoch ms)
  1  open
  2  high
  3  low
  4  close
  5  volume
  6  close_time (epoch ms)
  7  quote_asset_volume (ignored)
  8  number_of_trades (ignored)
  9  taker_buy_base_asset_volume (ignored)
  10 taker_buy_quote_asset_volume (ignored)
  11 ignore

Look-ahead rule: kline_at() returns only candles whose close_time <= T.
An in-progress candle (close_time > T) is never served as closed.
"""
from __future__ import annotations

import calendar
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional

import duckdb


@dataclass(frozen=True)
class Kline:
    """One OHLCV candle from Binance klines history."""

    symbol: str
    interval: str
    open_time: datetime   # UTC: candle open (inclusive)
    close_time: datetime  # UTC: last millisecond of the candle (inclusive)
    open: float
    high: float
    low: float
    close: float
    volume: float


class BinanceKlineStore:
    """Replay-safe Binance klines adapter backed by a local parquet cache.

    Cache layout::

        {cache_dir}/{SYMBOL}/{interval}/{YYYY}-{MM}.parquet

    Data source priority for each (symbol, interval, year-month):
      1. Pre-placed CSV: {cache_dir}/{SYMBOL}/{interval}/{SYMBOL}-{interval}-{YYYY}-{MM}.csv
      2. Pre-placed zip: {cache_dir}/{SYMBOL}/{interval}/{SYMBOL}-{interval}-{YYYY}-{MM}.zip
      3. Download from data.binance.vision/data/spot/monthly/klines/...

    After first parse, the parquet file is written so subsequent reads never
    touch the network or re-parse the CSV.
    """

    _MONTHLY_BASE = "https://data.binance.vision/data/spot/monthly/klines"

    def __init__(self, cache_dir: str | Path, offline: bool = False):
        """offline=True disables the data.binance.vision download fallback —
        only pre-placed CSVs/zips and existing parquet are used. Tests MUST
        set this (a networked test run otherwise pulls the REAL monthly
        archive and 'no data' assertions silently see live history)."""
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.offline = offline
        self._con = duckdb.connect()

    def close(self) -> None:
        """Release the DuckDB connection (workers: call per job)."""
        self._con.close()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def klines(
        self,
        symbol: str,
        interval: str,
        t0: datetime,
        t1: datetime,
    ) -> list[Kline]:
        """Return FULLY CLOSED klines with open_time >= t0 and close_time <= t1,
        ascending. A candle still in progress at t1 is never returned — its
        high/low/close/volume would be future data relative to t1.

        Both t0 and t1 must be timezone-aware.
        """
        if t0.tzinfo is None or t1.tzinfo is None:
            raise ValueError("t0 and t1 must be timezone-aware")
        t0_utc = t0.astimezone(timezone.utc)
        t1_utc = t1.astimezone(timezone.utc)

        self._ensure_months(symbol, interval, t0_utc, t1_utc)
        paths = self._cached_paths_in_window(symbol, interval, t0_utc, t1_utc)
        if not paths:
            return []

        t0_ms = _dt_to_ms(t0_utc)
        t1_ms = _dt_to_ms(t1_utc)
        # Look-ahead gate (holistic-review P1): a candle is only servable once
        # it has CLOSED. Gating on open_time alone returned the in-progress
        # candle at t1 with its FINAL high/low/close/volume — future data when
        # t1 is the frozen tick. Same rule kline_at() always had.
        sql = f"""
            SELECT open_time_ms, close_time_ms, open, high, low, close, volume
            FROM {_read_parquet_expr(paths)}
            WHERE open_time_ms >= {t0_ms} AND close_time_ms <= {t1_ms}
            ORDER BY open_time_ms ASC
        """
        return [_row_to_kline(r, symbol, interval) for r in self._con.execute(sql).fetchall()]

    def kline_at(
        self,
        symbol: str,
        interval: str,
        at: datetime,
    ) -> Optional[Kline]:
        """Return the last FULLY CLOSED candle at `at`.

        Look-ahead rule: only candles whose close_time <= at are eligible.
        A candle still in progress (close_time > at) is never returned.
        `at` must be timezone-aware.
        """
        if at.tzinfo is None:
            raise ValueError("at must be timezone-aware")
        at_utc = at.astimezone(timezone.utc)

        # Ensure month(at) and the preceding month — a candle near a month
        # boundary can close just before the turn (e.g. kline_at(March 1
        # 00:00:30) legitimately needs the last Feb candle).
        self._ensure_months(symbol, interval, _subtract_one_month(at_utc), at_utc)

        paths = self._cached_paths_upto(symbol, interval, at_utc)
        if not paths:
            return None

        at_ms = _dt_to_ms(at_utc)
        sql = f"""
            SELECT open_time_ms, close_time_ms, open, high, low, close, volume
            FROM {_read_parquet_expr(paths)}
            WHERE close_time_ms <= {at_ms}
            ORDER BY close_time_ms DESC
            LIMIT 1
        """
        row = self._con.execute(sql).fetchone()
        if row is None:
            return None
        return _row_to_kline(row, symbol, interval)

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def _ensure_months(
        self, symbol: str, interval: str, t0: datetime, t1: datetime
    ) -> None:
        for year, month in _months_in_window(t0, t1):
            parquet = self._parquet_path(symbol, interval, year, month)
            if not parquet.exists():
                self._materialize_month(symbol, interval, year, month)

    def _materialize_month(
        self, symbol: str, interval: str, year: int, month: int
    ) -> None:
        sym = symbol.upper()
        fname = f"{sym}-{interval}-{year}-{month:02d}"
        slot_dir = self.cache_dir / sym / interval
        slot_dir.mkdir(parents=True, exist_ok=True)
        dest = slot_dir / f"{year}-{month:02d}.parquet"

        # Priority 1: pre-placed CSV (fixtures, pre-staged data)
        csv_path = slot_dir / f"{fname}.csv"
        if csv_path.exists():
            _csv_to_parquet(self._con, csv_path, dest)
            return

        # Priority 2: pre-placed zip archive
        zip_path = slot_dir / f"{fname}.zip"
        if zip_path.exists():
            csv_bytes = _extract_csv_bytes(zip_path.read_bytes())
            _csv_bytes_to_parquet(self._con, csv_bytes, dest, slot_dir, fname)
            return

        # Priority 3: download from data.binance.vision
        if self.offline:
            return  # callers treat the month as unavailable
        url = f"{self._MONTHLY_BASE}/{sym}/{interval}/{fname}.zip"
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                zip_bytes = resp.read()
            csv_bytes = _extract_csv_bytes(zip_bytes)
            _csv_bytes_to_parquet(self._con, csv_bytes, dest, slot_dir, fname)
        except Exception:
            pass  # month unavailable (future, gap, wrong symbol) — callers return []

    def _parquet_path(
        self, symbol: str, interval: str, year: int, month: int
    ) -> Path:
        return self.cache_dir / symbol.upper() / interval / f"{year}-{month:02d}.parquet"

    def _cached_paths_in_window(
        self, symbol: str, interval: str, t0: datetime, t1: datetime
    ) -> list[Path]:
        return [
            p
            for y, m in _months_in_window(t0, t1)
            if (p := self._parquet_path(symbol, interval, y, m)).exists()
        ]

    def _cached_paths_upto(
        self, symbol: str, interval: str, at: datetime
    ) -> list[Path]:
        """All cached parquet files for months up to and including month(at)."""
        sym_dir = self.cache_dir / symbol.upper() / interval
        if not sym_dir.exists():
            return []
        at_ym = f"{at.year}-{at.month:02d}"
        # YYYY-MM lexicographic order is chronological — stem comparison is safe
        return [p for p in sorted(sym_dir.glob("*.parquet")) if p.stem <= at_ym]


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _months_in_window(t0: datetime, t1: datetime) -> list[tuple[int, int]]:
    """Return (year, month) pairs for every calendar month overlapping [t0, t1]."""
    months: list[tuple[int, int]] = []
    y, m = t0.year, t0.month
    while (y, m) <= (t1.year, t1.month):
        months.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return months


def _subtract_one_month(dt: datetime) -> datetime:
    """Return a datetime approximately one calendar month before dt."""
    m = dt.month - 1 or 12
    y = dt.year - (1 if dt.month == 1 else 0)
    day = min(dt.day, calendar.monthrange(y, m)[1])
    return dt.replace(year=y, month=m, day=day)


def _dt_to_ms(dt: datetime) -> int:
    """Convert timezone-aware datetime to epoch milliseconds (integer)."""
    return int(dt.timestamp() * 1000)


def _read_parquet_expr(paths: list[Path]) -> str:
    """DuckDB read_parquet() expression for one or more files."""
    if len(paths) == 1:
        return f"read_parquet('{paths[0]}')"
    list_str = ", ".join(f"'{p}'" for p in paths)
    return f"read_parquet([{list_str}])"


def _csv_to_parquet(con: duckdb.DuckDBPyConnection, csv_path: Path, dest: Path) -> None:
    """Parse Binance klines CSV (no header row) and write parquet.

    Uses read_csv with explicit column names to avoid relying on version-
    dependent auto-naming behaviour (column0/column1/... is not universal).
    Binance klines layout: open_time_ms, open, high, low, close, volume,
    close_time_ms, quote_vol, num_trades, tb_base, tb_quote, ignore_col.
    """
    # Timestamp-unit normalization (load-bearing): Binance's monthly archive
    # switched klines open/close times from MILLISECONDS to MICROSECONDS for
    # newer data (observed on the 2026 BTCUSDT dumps: open_time = 1.775e15, a
    # 16-digit µs value). The rest of this module assumes milliseconds
    # (kline_at builds `at_ms` from dt.timestamp()*1000), so an un-normalized
    # µs file makes every `close_time_ms <= at_ms` comparison false -> 0 klines
    # returned, silently. Normalize to ms at parse time so the stored parquet is
    # always ms regardless of source precision. Threshold 1e14 cleanly separates
    # ms (~1.77e12 this era) from µs (~1.77e15) for ~a century.
    con.execute(f"""
        COPY (
            SELECT
                CASE WHEN open_time_ms  > 100000000000000 THEN open_time_ms  // 1000
                     ELSE open_time_ms  END AS open_time_ms,
                CASE WHEN close_time_ms > 100000000000000 THEN close_time_ms // 1000
                     ELSE close_time_ms END AS close_time_ms,
                open, high, low, close, volume
            FROM read_csv('{csv_path}',
                header=false,
                columns={{
                    'open_time_ms': 'BIGINT',
                    'open': 'DOUBLE',
                    'high': 'DOUBLE',
                    'low': 'DOUBLE',
                    'close': 'DOUBLE',
                    'volume': 'DOUBLE',
                    'close_time_ms': 'BIGINT',
                    'quote_vol': 'DOUBLE',
                    'num_trades': 'BIGINT',
                    'tb_base': 'DOUBLE',
                    'tb_quote': 'DOUBLE',
                    'ignore_col': 'VARCHAR'
                }}
            )
        ) TO '{dest}' (FORMAT PARQUET)
    """)


def _csv_bytes_to_parquet(
    con: duckdb.DuckDBPyConnection,
    csv_bytes: bytes,
    dest: Path,
    slot_dir: Path,
    fname: str,
) -> None:
    tmp = slot_dir / f"_tmp_{fname}.csv"
    tmp.write_bytes(csv_bytes)
    try:
        _csv_to_parquet(con, tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def _extract_csv_bytes(zip_bytes: bytes) -> bytes:
    """Extract the first CSV file from a Binance monthly archive (bytes)."""
    with zipfile.ZipFile(BytesIO(zip_bytes)) as zf:
        csv_names = [n for n in zf.namelist() if n.endswith(".csv")]
        if not csv_names:
            raise ValueError(f"no CSV found in archive (entries: {zf.namelist()})")
        return zf.read(csv_names[0])


def _row_to_kline(row: tuple, symbol: str, interval: str) -> Kline:
    open_time_ms, close_time_ms, open_, high, low, close, volume = row
    return Kline(
        symbol=symbol.upper(),
        interval=interval,
        open_time=datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc),
        close_time=datetime.fromtimestamp(close_time_ms / 1000, tz=timezone.utc),
        open=float(open_),
        high=float(high),
        low=float(low),
        close=float(close),
        volume=float(volume),
    )
