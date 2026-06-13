"""Fetch a historical tape slice from the public dataset (SIM-3070).

`simmer backtest` needs a "tape" — a local dir of ``markets.parquet`` +
``quant.parquet`` for the window under test. This module fetches one
automatically from the **public, MIT-licensed** Polymarket dataset on
HuggingFace (``SII-WANGZJ/Polymarket_data``), so a user never has to hunt for
data: they just give ``--t0``/``--t1`` and the slice is fetched + cached.

Two reliability gotchas this module handles (the reason a naive "just duckdb
``read_parquet(hf_url)``" snippet fails for users):

1. The HF CDN intermittently aborts ranged parquet *metadata* reads over httpfs
   (``TProtocolException: Invalid data``). So we download the small index file
   (``markets.parquet``, ~160MB) ONCE over plain HTTP and read it locally —
   plain GETs don't hit the httpfs metadata path. Only the big ``quant.parquet``
   (21GB, can't download) is read remotely, with retries.
2. The remote ``quant`` scan is slow (the window join scans row groups) and can
   flake mid-stream — we retry the whole read with backoff and a high duckdb
   ``http_retries``.

Cached under ``~/.simmer/tapes/`` (override ``SIMMER_TAPE_CACHE``). Requires the
``[backtest]`` extra (duckdb). Data coverage ends ~2026-05-05 until the freshness
fetcher lands — `fetch_tape` errors clearly if the window is entirely past it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Union

import requests

HF_BASE = "https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data/resolve/main"
# Last day the published dataset covers (the HF dump). A window starting past
# this resolves to an empty slice — error with a useful message instead.
DATASET_END = datetime(2026, 5, 5, tzinfo=timezone.utc)
_INDEX_MIN_BYTES = 10_000_000  # sanity floor for a complete markets.parquet (~160MB)
# Days of trades to keep BEFORE t0 so each market has an opening price at the
# first tick. Generous (liquid markets trade right up to resolution); also the
# lower bound of the timestamp filter that prunes the 21GB remote scan.
_OPENING_BUFFER_DAYS = 30


class TapeFetchError(RuntimeError):
    """Raised when a tape window can't be fetched (bad window, network, no rows)."""


def cache_root() -> Path:
    root = os.environ.get("SIMMER_TAPE_CACHE") or os.path.join(
        os.path.expanduser("~"), ".simmer", "tapes"
    )
    p = Path(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _iso(value: Union[str, datetime]) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ensure_index(root: Path, *, refresh: bool = False) -> Path:
    """Download the small ``markets.parquet`` index once via plain HTTP (cached).

    Plain GET sidesteps the httpfs ranged-metadata flakiness that breaks a direct
    ``read_parquet(hf_url)`` on the markets file.
    """
    dest = root / "_markets-index.parquet"
    if dest.exists() and dest.stat().st_size >= _INDEX_MIN_BYTES and not refresh:
        return dest
    url = f"{HF_BASE}/markets.parquet"
    tmp = Path(tempfile.mkstemp(dir=str(root), suffix=".part")[1])
    try:
        with requests.get(url, stream=True, timeout=120) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
        if tmp.stat().st_size < _INDEX_MIN_BYTES:
            raise TapeFetchError(
                f"downloaded markets index looks truncated ({tmp.stat().st_size} bytes) — retry"
            )
        os.replace(tmp, dest)  # atomic
    except requests.RequestException as exc:
        raise TapeFetchError(f"could not download the markets index: {exc}") from exc
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def _key(t0: datetime, t1: datetime, max_markets: int, min_volume: float) -> str:
    blob = f"{t0.isoformat()}|{t1.isoformat()}|{max_markets}|{min_volume}"
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def fetch_tape(
    t0: Union[str, datetime],
    t1: Union[str, datetime],
    *,
    max_markets: int = 300,
    min_volume: float = 1000.0,
    cache_dir: Optional[str] = None,
    refresh: bool = False,
    retries: int = 4,
    log=print,
) -> str:
    """Fetch (or reuse a cached) tape slice for ``[t0, t1]`` and return its dir.

    Markets RESOLVING in the window (so settlements happen) ranked by volume,
    plus their trade prints. Cached by (window, max_markets, min_volume) — a
    repeat call is instant.
    """
    try:
        import duckdb
    except ImportError as exc:
        raise TapeFetchError(
            "tape fetching needs duckdb — install with: pip install 'simmer-sdk[backtest]'"
        ) from exc

    t0_dt, t1_dt = _iso(t0), _iso(t1)
    if t1_dt <= t0_dt:
        raise TapeFetchError(f"t1 ({t1_dt.date()}) must be after t0 ({t0_dt.date()})")
    if t0_dt >= DATASET_END:
        raise TapeFetchError(
            f"the public dataset ends ~{DATASET_END.date()}, but the window starts "
            f"{t0_dt.date()} — pick an earlier window (a freshness fetcher for recent "
            "data is a planned follow-up)."
        )
    if t1_dt > DATASET_END:
        log(f"note: dataset ends ~{DATASET_END.date()}; markets resolving after that "
            "won't be in the slice.")

    root = Path(cache_dir) if cache_dir else cache_root()
    root.mkdir(parents=True, exist_ok=True)
    slice_dir = root / _key(t0_dt, t1_dt, max_markets, min_volume)
    markets_pq = slice_dir / "markets.parquet"
    quant_pq = slice_dir / "quant.parquet"
    if markets_pq.exists() and quant_pq.exists() and not refresh:
        log(f"using cached tape: {slice_dir}")
        return str(slice_dir)

    slice_dir.mkdir(parents=True, exist_ok=True)
    index = _ensure_index(root, refresh=refresh)

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    # Make the remote quant scan as resilient as duckdb allows.
    for stmt in ("SET http_retries=5", "SET http_retry_backoff=2",
                 "SET http_timeout=120000", "SET http_keep_alive=true"):
        try:
            con.execute(stmt)
        except duckdb.Error:
            pass  # older duckdb may not know a setting — best-effort

    # 1) markets resolving in-window, ranked by volume, from the LOCAL index.
    log(f"slicing markets for {t0_dt.date()}..{t1_dt.date()} (max {max_markets}, "
        f"min volume {min_volume:,.0f})...")
    con.execute(
        f"""CREATE TABLE mx AS
            SELECT * FROM read_parquet('{index.as_posix()}')
            WHERE end_date BETWEEN ? AND ? AND closed = 1 AND volume > ?
            ORDER BY volume DESC LIMIT ?""",
        [t0_dt, t1_dt, min_volume, max_markets],
    )
    n_markets = con.execute("SELECT count(*) FROM mx").fetchone()[0]
    if not n_markets:
        shutil.rmtree(slice_dir, ignore_errors=True)
        raise TapeFetchError(
            f"no resolved markets with volume > {min_volume:,.0f} found in "
            f"{t0_dt.date()}..{t1_dt.date()} — widen the window or lower --min-volume."
        )
    con.execute(f"COPY mx TO '{markets_pq.as_posix()}' (FORMAT PARQUET)")
    log(f"  {n_markets} markets. fetching their trade prints (remote, can take a "
        "few minutes)...")

    # 2) their quant prints — the big remote read, retried (HF CDN flakes mid-stream).
    #
    # CRITICAL perf: the dataset's quant.parquet is 21GB and time-ordered, so we
    # prune the remote scan to the window's row groups with a timestamp filter.
    # A backtest of [t0, t1] only needs trades IN the window plus a buffer before
    # t0 for opening prices (the first tick reads the latest trade <= t0). Without
    # the filter the market_id join scans the whole file (15+ min over HTTP); with
    # it, tens of seconds. The buffer makes a few illiquid markets price-less at
    # the window start — fine, since slices are volume-ranked (liquid) markets.
    quant_url = f"{HF_BASE}/quant.parquet"
    u0 = int((t0_dt - timedelta(days=_OPENING_BUFFER_DAYS)).timestamp())
    u1 = int(t1_dt.timestamp())
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            con.execute(
                f"""COPY (
                      SELECT q.* FROM read_parquet('{quant_url}') q
                      JOIN mx ON q.market_id = mx.id
                      WHERE q.timestamp BETWEEN {u0} AND {u1}
                    ) TO '{quant_pq.as_posix()}' (FORMAT PARQUET)"""
            )
            break
        except duckdb.Error as exc:
            last_err = exc
            quant_pq.unlink(missing_ok=True)
            if attempt < retries:
                log(f"  remote read flaked (attempt {attempt}/{retries}: "
                    f"{str(exc).splitlines()[0][:80]}); retrying...")
    else:
        shutil.rmtree(slice_dir, ignore_errors=True)
        raise TapeFetchError(
            f"could not read trade prints from the dataset after {retries} attempts "
            f"(HF CDN flakiness): {last_err}. Retry, or narrow the window."
        )

    qn = con.execute(f"SELECT count(*) FROM read_parquet('{quant_pq.as_posix()}')").fetchone()[0]
    con.close()

    with open(slice_dir / "manifest.json", "w") as fh:
        json.dump({
            "source": HF_BASE,
            "t0": t0_dt.date().isoformat(), "t1": t1_dt.date().isoformat(),
            "markets": n_markets, "quant_rows": qn,
            "extracted_with": "simmer_sdk.backtest.tape.fetch_tape",
        }, fh, indent=2)
    log(f"tape ready: {n_markets} markets, {qn:,} prints → {slice_dir}")
    return str(slice_dir)
