"""Self-serve tape download for `simmer backtest` (SIM-3070 slice 5).

`simmer backtest` needs a *tape* — a local dir of ``markets.parquet`` +
``quant.parquet`` for the window under test. This module fetches one from
Simmer's backend (``POST /api/backtest/tape``), which slices the canonical
Polymarket dataset server-side and returns presigned URLs to a small per-window
slice (tens of MB). The slice is cached under ``~/.simmer/tapes/`` so a repeat
window is instant.

Why not fetch from HuggingFace directly: the public dump's CDN serves the 21GB
``quant.parquet`` at ~0.44 MB/s, so client-side window slicing over httpfs times
out. The backend stages the dataset into fast object storage once and slices
intra-datacenter (SIM-3070, 2026-06-14). ``--tape <local>`` stays as a BYO
escape hatch for power users who have their own slice.

No ``[backtest]`` extra needed here — fetching is plain HTTP (only the engine,
which runs the tape, needs duckdb).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional, Union
from datetime import datetime

import requests

# Last day the canonical dataset covers, until the freshness fetcher (slice 6)
# grows it. A window starting past this errors server-side too, but checking
# locally gives a clearer message without a round-trip.
DATASET_END = "2026-05-05"
_TAPE_ENDPOINT = "/api/backtest/tape"


class TapeFetchError(RuntimeError):
    """Raised when a tape window can't be fetched (bad window, network, server)."""


def cache_root() -> Path:
    root = os.environ.get("SIMMER_TAPE_CACHE") or os.path.join(
        os.path.expanduser("~"), ".simmer", "tapes"
    )
    p = Path(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _resolve_base_url(base_url: Optional[str]) -> str:
    url = base_url or os.getenv("SIMMER_API_URL") or "https://api.simmer.markets"
    return url.rstrip("/")


def _iso_date(value: Union[str, datetime]) -> str:
    """ISO date string for the request body (accepts str or datetime)."""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)


def _download(url: str, dest: Path, *, timeout: int = 300) -> None:
    tmp = Path(tempfile.mkstemp(dir=str(dest.parent), suffix=".part")[1])
    try:
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
        os.replace(tmp, dest)  # atomic
    finally:
        tmp.unlink(missing_ok=True)


def fetch_tape(
    t0: Union[str, datetime],
    t1: Union[str, datetime],
    *,
    max_markets: int = 300,
    min_volume: float = 1000.0,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    cache_dir: Optional[str] = None,
    refresh: bool = False,
    timeout: int = 300,
    log=print,
) -> str:
    """Fetch (or reuse a cached) tape slice for ``[t0, t1]``; return its local dir.

    Calls ``POST {base_url}/api/backtest/tape`` (Simmer API key required — the
    same ``SIMMER_API_KEY`` you trade with); downloads the presigned
    markets.parquet + quant.parquet + manifest.json into ``~/.simmer/tapes/<key>/``.
    The server keys the slice by (dataset_rev, window, max_markets, min_volume),
    so the cache key is stable across callers.
    """
    base = _resolve_base_url(base_url)
    key = api_key or os.getenv("SIMMER_API_KEY")
    if not key:
        raise TapeFetchError(
            "a Simmer API key is required to fetch a tape — set SIMMER_API_KEY "
            "(the same key you trade with) or pass api_key=..., or use --tape "
            "<local-dir> / --demo to skip the download."
        )
    payload = {
        "t0": _iso_date(t0),
        "t1": _iso_date(t1),
        "max_markets": int(max_markets),
        "min_volume": float(min_volume),
    }
    log(f"requesting tape slice {payload['t0']}..{payload['t1']} "
        f"(max {max_markets} markets, min volume {min_volume:,.0f}) from {base}...")
    try:
        resp = requests.post(
            base + _TAPE_ENDPOINT, json=payload,
            headers={"Authorization": f"Bearer {key}"}, timeout=60,
        )
    except requests.RequestException as exc:
        raise TapeFetchError(f"could not reach the tape service at {base}: {exc}") from exc

    if resp.status_code in (401, 403):
        raise TapeFetchError(_detail(resp, "tape request rejected — check SIMMER_API_KEY"))
    if resp.status_code == 429:
        raise TapeFetchError(_detail(resp, "backtest tape rate limit reached — wait a minute "
                                           "(cached windows are free)"))
    if resp.status_code == 422:
        raise TapeFetchError(_detail(resp, "invalid backtest window"))
    if resp.status_code == 503:
        raise TapeFetchError(_detail(resp, "the backtest tape service is unavailable"))
    if not resp.ok:
        raise TapeFetchError(_detail(resp, f"tape request failed ({resp.status_code})"))

    body = resp.json()
    key = body.get("key")
    urls = body.get("urls") or {}
    if not key or not urls.get("markets") or not urls.get("quant"):
        raise TapeFetchError(f"malformed tape response from {base}: {body!r}")

    root = Path(cache_dir) if cache_dir else cache_root()
    root.mkdir(parents=True, exist_ok=True)
    slice_dir = root / key
    markets_pq = slice_dir / "markets.parquet"
    quant_pq = slice_dir / "quant.parquet"
    if markets_pq.exists() and quant_pq.exists() and not refresh:
        log(f"using cached tape: {slice_dir}")
        return str(slice_dir)

    slice_dir.mkdir(parents=True, exist_ok=True)
    n_markets = body.get("markets")
    n_quant = body.get("quant_rows")
    log(f"downloading slice ({n_markets} markets, "
        f"{f'{n_quant:,}' if isinstance(n_quant, int) else '?'} prints)"
        f"{' [server cache hit]' if body.get('cached') else ''}...")
    try:
        _download(urls["markets"], markets_pq, timeout=timeout)
        _download(urls["quant"], quant_pq, timeout=timeout)
        if urls.get("manifest"):
            try:
                _download(urls["manifest"], slice_dir / "manifest.json", timeout=timeout)
            except Exception:
                pass  # manifest is best-effort (only feeds dataset_rev labeling)
    except requests.RequestException as exc:
        # don't leave a half-downloaded slice that a later run would treat as cached
        import shutil

        shutil.rmtree(slice_dir, ignore_errors=True)
        raise TapeFetchError(f"failed downloading the tape slice: {exc}") from exc

    log(f"tape ready → {slice_dir}")
    return str(slice_dir)


def _detail(resp, fallback: str) -> str:
    try:
        return resp.json().get("detail") or fallback
    except Exception:
        return fallback
