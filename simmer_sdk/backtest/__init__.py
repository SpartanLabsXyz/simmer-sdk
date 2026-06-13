"""Local historical backtesting for Simmer trading skills (SIM-3070).

Backtest an UNMODIFIED skill bundle against historical prediction-market data
before risking capital — the missing "test before capital" leg alongside the
sim-venue, dry-run, and paper-trade modes (which are all live-forward).

``run_backtest`` is the programmatic entrypoint; ``simmer_sdk.cli`` wraps it as
``simmer backtest``. Both run the SAME engine substrate (vendored under
``backtest.replay`` from the simmer backend) that powers the internal
``scripts/replay_run.py run-bundle`` — so a given (bundle, tape, window,
cadence, args) reproduces an identical ``reproducibility.config_hash`` and
therefore identical pnl / hit_rate across the two paths.

Requires the optional extra::

    pip install 'simmer-sdk[backtest]'
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Union

__all__ = ["run_backtest", "resolve_sdk_path", "BacktestError"]

_DEFAULT_ARGS = ["--live", "--quiet"]
_CADENCE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class BacktestError(RuntimeError):
    """Raised for user-facing backtest setup errors (bad paths, missing extra)."""


# -- input parsing (mirrors scripts/replay_run.py so config_hash matches) -----

def _parse_dt(value: Union[str, datetime]) -> datetime:
    """ISO string or datetime -> tz-aware UTC.

    STRING inputs mirror the internal CLI's ``_dt`` EXACTLY — it does
    ``datetime.fromisoformat(s).replace(tzinfo=utc)``, which FORCES UTC and does
    NOT convert an offset, so an offset-bearing ISO string keeps its wall-clock
    time. We must match byte-for-byte (incl. that quirk) or ``config_hash``
    desyncs from ``scripts/replay_run.py`` for the same ``--t0``/``--t1``.

    DATETIME inputs (programmatic; no internal counterpart) get the sane
    treatment: a naive value is pinned to UTC, an aware value is converted.
    """
    if not isinstance(value, datetime):
        return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_cadence(value: Union[str, int, float, timedelta]) -> timedelta:
    """Accept a timedelta, a bare number of minutes, or a ``<n><unit>`` string
    (``90s``, ``15m``, ``12h``, ``30d``)."""
    if isinstance(value, timedelta):
        return value
    if isinstance(value, (int, float)):
        return timedelta(minutes=value)
    s = str(value).strip().lower()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", s)
    if not m:
        raise BacktestError(
            f"cadence {value!r} not understood — use e.g. 15m, 12h, 30d, or minutes"
        )
    qty = float(m.group(1))
    unit = m.group(2) or "m"  # bare number = minutes
    return timedelta(seconds=qty * _CADENCE_UNITS[unit])


def _normalize_args(args: Union[str, list, tuple, None]) -> Optional[list]:
    """Entrypoint CLI args as a list (or None -> harness default --live --quiet).

    A string is split on whitespace, matching the internal ``--args`` contract.
    """
    if args is None:
        return None
    if isinstance(args, str):
        return args.split() or None
    return list(args)


def resolve_sdk_path(explicit: Optional[str] = None) -> str:
    """Directory to put on the skill subprocess's PYTHONPATH so it can
    ``import simmer_sdk``.

    The replay harness builds the subprocess env from a strict allowlist and
    sets ``PYTHONPATH=sdk_path`` explicitly — it does NOT inherit the parent's
    sys.path — so this must point at the dir CONTAINING ``simmer_sdk/``. For an
    installed wheel that's site-packages; for an editable/source checkout it's
    the repo root. Auto-resolved from this package's own location.
    """
    if explicit:
        return os.path.abspath(explicit)
    import simmer_sdk

    return os.path.dirname(os.path.dirname(os.path.abspath(simmer_sdk.__file__)))


def _bundle_skill_version(bundle_dir: str) -> str:
    """metadata.version from the bundle's SKILL.md frontmatter.

    Byte-for-byte the same regex as ``scripts/replay_run.py._bundle_skill_version``
    — it feeds ``skill_version`` into ``config_hash``, so any divergence here
    would break SDK/internal reproducibility.
    """
    path = os.path.join(bundle_dir, "SKILL.md")
    try:
        with open(path) as fh:
            head = fh.read(4096)
        m = re.search(r"^\s*version:\s*['\"]?([0-9][^'\"#\s]*)", head, re.MULTILINE)
        return m.group(1) if m else "unknown"
    except OSError:
        return "unknown"


def _dataset_rev(tape_dir: str) -> str:
    """``hf-slice:<t0>..<t1>:<n>mk`` from the tape manifest, matching the
    internal CLI so ``config_hash`` lines up."""
    import json

    manifest_path = os.path.join(tape_dir, "manifest.json")
    if os.path.exists(manifest_path):
        with open(manifest_path) as fh:
            m = json.load(fh)
        return f"hf-slice:{m.get('t0')}..{m.get('t1')}:{m.get('markets')}mk"
    return "unknown"


# -- public entrypoint --------------------------------------------------------

def run_backtest(
    bundle: str,
    *,
    entrypoint: str,
    tape: str,
    t0: Union[str, datetime],
    t1: Union[str, datetime],
    cadence: Union[str, int, float, timedelta] = "15m",
    balance: float = 1000.0,
    fee_rate: float = 0.0,
    seed: int = 0,
    max_evaluations: int = 50_000,
    args: Union[str, list, tuple, None] = None,
    coverage_ok: bool = False,
    candles: bool = True,
    offline_klines: bool = False,
    sdk_path: Optional[str] = None,
) -> dict:
    """Replay an UNMODIFIED skill bundle over a local historical tape slice.

    Args:
        bundle: path to the skill bundle dir (the thing a user installs).
        entrypoint: script filename inside the bundle to run each tick
            (e.g. ``nothing_ever_happens.py``).
        tape: local dir holding ``markets.parquet`` + ``quant.parquet``
            (+ optional ``manifest.json``) — as produced by
            ``scripts/replay_run.py extract``. Remote/window download is slice 5.
        t0, t1: window bounds (ISO string or datetime; naive => UTC).
        cadence: tick spacing — ``15m`` / ``12h`` / ``30d``, minutes, or timedelta.
        balance: starting balance.
        fee_rate: per-fill fee rate (0 = none; baselines ignore fees in v0).
        seed: RNG seed for the random baseline.
        max_evaluations: hard ticks×markets budget (engine truncates past it).
        args: entrypoint CLI args (string split on whitespace, or list).
            ``None`` => the harness default ``--live --quiet``.
        coverage_ok: assert this window/cadence fits the skill's signal horizon,
            so a 0-trade result is a *verified* no-signal outcome.
        candles: wire the Binance candles plane (needed by crypto-signal skills;
            absent => ``/api/replay-data/candles`` 404s and the skill reads
            "no signal" honestly).
        offline_klines: candles plane uses only pre-cached months (no network).
        sdk_path: dir containing ``simmer_sdk/`` for the subprocess PYTHONPATH;
            auto-resolved to the installed package when omitted.

    Returns:
        The engine report dict (``summary`` / ``baselines`` / ``decisions`` /
        ``fills`` / ``equity_curve`` / ``realism_gaps`` / ``data_plane`` /
        ``reproducibility`` / ``bundle`` / ``coverage_ok`` / ``replay_job``).
    """
    try:
        from .replay.candles_service import build_kline_store
        from .replay.duckdb_store import DuckDBStore
        from .replay.engine import ReplayConfig
        from .replay.harness import bundle_digest, replay_bundle
    except ImportError as exc:  # missing duckdb/fastapi/uvicorn
        raise BacktestError(
            "the backtest engine needs extra dependencies — install with:\n"
            "    pip install 'simmer-sdk[backtest]'"
        ) from exc

    bundle = os.path.abspath(bundle)
    if not os.path.isdir(bundle):
        raise BacktestError(f"bundle dir not found: {bundle}")
    if not os.path.exists(os.path.join(bundle, entrypoint)):
        raise BacktestError(f"entrypoint {entrypoint!r} not found in bundle {bundle}")

    markets_pq = os.path.join(tape, "markets.parquet")
    quant_pq = os.path.join(tape, "quant.parquet")
    for p in (markets_pq, quant_pq):
        if not os.path.exists(p):
            raise BacktestError(
                f"missing tape file: {p}\n"
                "expected a local slice dir with markets.parquet + quant.parquet "
                "(produce one with scripts/replay_run.py extract)"
            )

    t0_dt, t1_dt = _parse_dt(t0), _parse_dt(t1)
    if t1_dt <= t0_dt:
        raise BacktestError(f"t1 ({t1_dt}) must be after t0 ({t0_dt})")
    cadence_td = _parse_cadence(cadence)
    extra_args = _normalize_args(args)
    sdk_path = resolve_sdk_path(sdk_path)

    # store + kline_store both hold OS resources (a DuckDB connection / FD); build
    # them INSIDE the try so the finally closes whatever was opened even if a later
    # constructor throws (e.g. build_kline_store on a bad cache dir would otherwise
    # leak the already-open DuckDB connection).
    store = None
    kline_store = None
    try:
        store = DuckDBStore(markets_pq, quant_pq)
        config = ReplayConfig(
            t0=t0_dt,
            t1=t1_dt,
            cadence=cadence_td,
            starting_balance=balance,
            fee_rate=fee_rate,
            skill_slug=os.path.basename(bundle.rstrip("/")),
            skill_version=_bundle_skill_version(bundle),
            dataset_rev=_dataset_rev(tape),
            seed=seed,
            max_evaluations=max_evaluations,
            # bundle content + invocation change results -> must change config_hash
            params={
                "entrypoint": entrypoint,
                "args": extra_args or _DEFAULT_ARGS,
                "bundle_digest": bundle_digest(bundle),
            },
        )
        kline_store = build_kline_store(offline=offline_klines) if candles else None
        report = replay_bundle(
            store, bundle, entrypoint, config, sdk_path,
            extra_args=extra_args, kline_store=kline_store,
        )
    finally:
        if kline_store is not None:
            kline_store.close()
        if store is not None:
            store.close()

    # Runner-asserted coverage: lets a 0-trade result read as verified
    # no-signal. Default False keeps 0-trade results inconclusive.
    report["coverage_ok"] = bool(coverage_ok)
    report["replay_job"] = {
        "slug": config.skill_slug,
        "version": None,
        "t0": t0_dt.isoformat(),
        "t1": t1_dt.isoformat(),
        "cadence_seconds": int(cadence_td.total_seconds()),
        "args": " ".join(extra_args) if extra_args else None,
        "coverage_ok": bool(coverage_ok),
        "tape_url": None,
        "entrypoint": entrypoint,
    }
    return report
