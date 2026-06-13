# vendored from simmer_v3/replay/harness.py @ 96544b0f6a6c
# DO NOT EDIT HERE — regenerate via scripts/sync_replay_engine.py
"""Unmodified-bundle replay harness (SIM-3070).

Runs an ACTUAL skill bundle (script entrypoint) against the replay server:

  1. Copies the bundle to a temp dir — byte-identical, never edited. State
     files the skill writes (daily_spend.json etc.) land in the copy.
  2. Provides `gamma_api.py` in the temp dir — a replay-backed implementation
     of the Gamma discovery client that skills expect THE USER to supply
     (NEH's install docs say "copy gamma_api.py from polymarket-ai-divergence").
     Ours synthesizes Gamma-shaped events from the frozen replay server over
     the same HTTP surface, so discovery is look-ahead-safe by construction.
  3. Serves the replay app on 127.0.0.1:<port> via uvicorn (real HTTP — the
     SDK uses `requests`, no ASGI shortcut), and points the skill at it via
     SIMMER_API_URL (SDK >= 0.17.30) + PYTHONPATH to the local SDK checkout.
  4. Executes the bundle once per tick as a subprocess inside the engine loop.

Known wall-clock leak (documented): bundles that call datetime.now() for
local state (NEH's daily-spend reset) see real time, not the frozen tick.
Decision logic prices/expiries all come from the server (frozen); the leak
affects only local budget-reset cadence. Flagged in the report.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .engine import ReplayConfig, ReplayEngine
from .server import ReplaySession, create_app
from .store import HistoricalStore

GAMMA_SHIM = '''\
"""Replay-backed gamma_api — provided by the Simmer replay harness.

Synthesizes Gamma-shaped events from the frozen replay server. One event per
market (standalone), binary Yes/No outcomes. Same module name + interface the
skill expects its user to install.
"""

import os

import requests

_BASE = os.environ["SIMMER_API_URL"].rstrip("/")
_HEADERS = {"Authorization": "Bearer " + os.environ.get("SIMMER_API_KEY", "sk_replay")}


class GammaClient:
    def __init__(self, *a, **kw):
        pass

    def get_events(self, active=True, closed=False, limit=50, after_cursor=None,
                   order=None, ascending=False, **kw):
        if after_cursor:  # single page — replay store returns one ranked page
            return [], None
        r = requests.get(_BASE + "/api/sdk/markets", params={"limit": limit},
                         headers=_HEADERS, timeout=30)
        r.raise_for_status()
        events = []
        for m in r.json().get("markets", []):
            yes = m.get("yes_price")
            no = m.get("no_price")
            # Fidelity: real Gamma never lists a market with no orderbook price.
            # The replay server returns null prices for markets with no tape
            # print <= the frozen tick; skip them rather than emit nulls that a
            # skill's numeric filters would choke on (surfaced a latent None bug
            # in NEH's fetch_candidate_markets during the 2026-03 smoke).
            if yes is None or no is None:
                continue
            events.append({
                "slug": m.get("slug", ""),
                "tags": [],
                "category": "",
                "liquidity": m.get("volume", 0.0),
                "volume_24h": m.get("volume", 0.0),
                "markets": [{
                    "question": m.get("question", ""),
                    "outcomes": ["Yes", "No"],
                    "condition_id": m.get("polymarket_condition_id", ""),
                    "no_price": no,
                    "yes_price": yes,
                    "end_date": m.get("resolves_at", ""),
                    "category": "",
                    "tags": [],
                }],
            })
        return events, None
'''


# Holistic-review P1: bundles must NOT inherit the host process environment —
# on a Railway worker os.environ holds DATABASE_URL, wallet-encryption keys,
# API secrets. Build the subprocess env from a strict allowlist instead.
_ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ",
                  "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")


def _subprocess_env(base_url: str, sdk_path: str) -> dict:
    env = {k: os.environ[k] for k in _ENV_ALLOWLIST if k in os.environ}
    env.update({
        "SIMMER_API_URL": base_url,
        "SIMMER_API_KEY": "sk_replay",
        # Explicit replay marker: skills gate their legacy direct-vendor
        # fallbacks (e.g. api.binance.com when the candles plane is absent)
        # on this — a fallback firing under replay would be look-ahead.
        "SIMMER_REPLAY": "1",
        "PYTHONPATH": sdk_path,
        "TRADING_VENUE": "polymarket",
        "PYTHONUNBUFFERED": "1",
    })
    return env


def bundle_digest(bundle_dir: str) -> str:
    """sha256 over the bundle's file tree (relpath + bytes), for the
    reproducibility hash — two replays of different bundle content must not
    collide on config_hash."""
    import hashlib

    h = hashlib.sha256()
    root = Path(bundle_dir)
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            data = p.read_bytes()
            # length-prefixed framing — bare relpath+bytes concatenation is
            # ambiguous (file "a" with content "bc" == file "ab" with "c")
            rel = str(p.relative_to(root)).encode()
            h.update(f"{len(rel)}:".encode() + rel)
            h.update(f"{len(data)}:".encode() + data)
    return h.hexdigest()[:16]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SessionServer:
    """uvicorn thread serving the replay app for one session."""

    def __init__(self, session: ReplaySession):
        import uvicorn

        self.port = _free_port()
        config = uvicorn.Config(create_app(session), host="127.0.0.1",
                                port=self.port, log_level="error")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self, timeout: float = 10.0) -> None:
        self._thread.start()
        deadline = time.time() + timeout
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("replay server failed to start")
            time.sleep(0.05)

    def stop(self) -> None:
        self._server.should_exit = True
        # Generous join: a candle request can still be draining at run-end (a
        # cold Binance month download is up to ~60s). The CALLER closes the
        # kline_store immediately after stop() returns, so we must let an
        # in-flight /api/replay-data/candles request finish against the live
        # DuckDB connection first — a short cap would return mid-drain and
        # close() would race a live read (use-after-close). Returns immediately
        # when nothing is in flight (the common case).
        self._thread.join(timeout=70)


class BundleStrategy:
    """Engine Strategy that executes a skill bundle subprocess per tick."""

    def __init__(self, bundle_dir: str, entrypoint: str, base_url: str,
                 sdk_path: str, extra_args: Optional[list[str]] = None,
                 tick_timeout: float = 180.0):
        self.workdir = Path(tempfile.mkdtemp(prefix="replay-bundle-"))
        shutil.copytree(bundle_dir, self.workdir / "bundle", dirs_exist_ok=True)
        (self.workdir / "bundle" / "gamma_api.py").write_text(GAMMA_SHIM)
        self.entry = self.workdir / "bundle" / entrypoint
        if not self.entry.exists():
            raise FileNotFoundError(self.entry)
        self.base_url = base_url
        self.sdk_path = sdk_path
        self.extra_args = extra_args or ["--live", "--quiet"]
        self.tick_timeout = tick_timeout
        self.tick_logs: list[dict] = []

    def on_tick(self, api, now: datetime) -> None:
        env = _subprocess_env(self.base_url, self.sdk_path)
        proc = subprocess.run(
            [sys.executable, str(self.entry), *self.extra_args],
            cwd=str(self.entry.parent), env=env,
            capture_output=True, text=True, timeout=self.tick_timeout,
        )
        self.tick_logs.append({
            "ts": now.isoformat(), "exit": proc.returncode,
            "stdout_tail": proc.stdout[-2000:], "stderr_tail": proc.stderr[-2000:],
        })


def replay_bundle(store: HistoricalStore, bundle_dir: str, entrypoint: str,
                  config: ReplayConfig, sdk_path: str,
                  extra_args: Optional[list[str]] = None,
                  kline_store=None) -> dict:
    """Replay an unmodified skill bundle. Returns the engine report with a
    `bundle` section (per-tick subprocess logs + wall-clock-leak flag).

    extra_args are the entrypoint's own CLI args (default ["--live","--quiet"]).
    Skills differ — NEH takes --quiet, mert-sniper doesn't — so the caller picks.

    kline_store: optional BinanceKlineStore powering /api/replay-data/candles
    (end-clamped to the frozen tick) for skills whose signal is Binance candles
    — the trio (fast-loop / fast-scaler / btc-up-down). Absent → the candles
    endpoint 404s on the served SessionServer. The caller owns its lifecycle.
    """
    engine = ReplayEngine(store, _Placeholder(), config, kline_store=kline_store)
    server = SessionServer(engine.session)
    server.start()
    strategy = BundleStrategy(bundle_dir, entrypoint, server.base_url, sdk_path,
                              extra_args=extra_args)
    engine.strategy = strategy
    try:
        report = engine.run()
    finally:
        server.stop()
        shutil.rmtree(strategy.workdir, ignore_errors=True)  # holistic-review P1: don't leak workdirs across jobs
    failed = [t for t in strategy.tick_logs if t["exit"] != 0]
    report["bundle"] = {
        "entrypoint": entrypoint,
        "mode": "unmodified-bundle-subprocess",
        "tick_logs": strategy.tick_logs,
        "failed_ticks": len(failed),
        # A report with failed ticks is NOT publishable — the skill didn't
        # actually run on those ticks, so summary numbers under-represent it.
        "clean": not failed,
        "known_leaks": ["bundle-local wall clock (e.g. daily-spend reset cadence)"],
    }
    return report


class _Placeholder:
    def on_tick(self, api, now):  # replaced before run()
        raise RuntimeError("strategy not bound")
