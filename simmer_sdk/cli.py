#!/usr/bin/env python3
"""`simmer` command-line entrypoint.

Currently one subcommand:

    # fetch + cache the window slice from the tape service (no local tape needed)
    simmer backtest <bundle> --entrypoint run.py \
        --t0 2026-03-01 --t1 2026-03-08 [--cadence 12h] [--out report.json]

    simmer backtest <bundle> --entrypoint run.py --window 30d   # window by duration
    simmer backtest <bundle> --entrypoint run.py --tape ./slice --t0 ... --t1 ...  # BYO slice
    simmer backtest --demo        # bundled offline demo, no tape needed

Backtest an UNMODIFIED skill bundle against historical prediction-market data
before risking capital. Requires the ``[backtest]`` extra
(``pip install 'simmer-sdk[backtest]'``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Optional


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("simmer-sdk")
    except Exception:
        return "unknown"


# -- demo asset resolution ----------------------------------------------------

def _demo_paths() -> tuple[str, str, str]:
    """(bundle_dir, entrypoint, tape_dir) for the bundled offline demo."""
    import simmer_sdk.backtest as bt

    root = os.path.join(os.path.dirname(os.path.abspath(bt.__file__)), "demo")
    return (
        os.path.join(root, "backtest-demo-favorites"),
        "favorites_demo.py",
        os.path.join(root, "tape"),
    )


# -- summary printing ---------------------------------------------------------

def _fmt_money(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    return f"{x:+,.2f}"


def _fmt_pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _print_summary(report: dict, *, balance: float) -> None:
    s = report["summary"]
    b = report.get("baselines", {})
    dp = report.get("data_plane", {})
    repro = report.get("reproducibility", {})
    bundle = report.get("bundle", {})

    print("\n── backtest summary ─────────────────────────────────────────")
    print(f"  skill        {repro.get('skill', '?')}")
    print(f"  window       {' → '.join(repro.get('window', ['?', '?']))} @ {repro.get('cadence', '?')}")
    print(f"  pnl          {_fmt_money(s.get('pnl'))}   "
          f"(final equity {s.get('final_equity', balance):,.2f} on {balance:,.0f})")
    print(f"  hit rate     {_fmt_pct(s.get('hit_rate'))}   "
          f"({s.get('settlements', 0)} settled)")
    print(f"  max drawdown {_fmt_pct(s.get('max_drawdown'))}")
    print(f"  activity     {s.get('decisions', 0)} decisions · "
          f"{s.get('trades', 0)} trades · {s.get('markets_traded', 0)} markets · "
          f"{s.get('ticks', 0)} ticks")
    print(f"  baselines    buy&hold YES {_fmt_money(b.get('buy_and_hold_yes'))} · "
          f"random {_fmt_money(b.get('random'))}")

    if dp.get("kline_store"):
        print(f"  candle plane {dp.get('candles_served', 0)} served / "
              f"{dp.get('candle_requests', 0)} requested")

    gaps = report.get("realism_gaps", [])
    if gaps:
        print(f"  realism gaps {', '.join(gaps)}")

    # honesty flags
    if s.get("evaluations_exhausted"):
        print("  ⚠  evaluation budget exhausted — window truncated; raise "
              "--max-evaluations or shorten the window")
    failed = bundle.get("failed_ticks", 0)
    if failed:
        print(f"  ⚠  {failed} tick(s) exited non-zero — results UNDER-represent "
              "the skill; see report bundle.tick_logs (not a clean run)")
    elif bundle:
        leaks = bundle.get("known_leaks") or []
        if leaks:
            print(f"  ℹ  known leaks: {'; '.join(leaks)}")

    print(f"  config_hash  {repro.get('config_hash', '?')}")
    print("─────────────────────────────────────────────────────────────")


# -- backtest subcommand ------------------------------------------------------

_WINDOW_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _resolve_window(args: argparse.Namespace) -> tuple[str, str]:
    """Return (t0, t1) ISO dates from explicit --t0/--t1 or a --window duration.

    --window <Nd/Nh/...> anchors to --t1 (or the dataset end until the freshness
    fetcher lands) and walks back. Explicit --t0/--t1 take precedence.
    """
    import re
    from datetime import datetime, timedelta, timezone

    from simmer_sdk.backtest.tape import DATASET_END

    if args.t0 and args.t1:
        return args.t0, args.t1
    if args.window:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd]?)", str(args.window).strip().lower())
        if not m:
            raise ValueError(f"--window {args.window!r} not understood — use e.g. 30d, 12h, 90d")
        span = timedelta(seconds=float(m.group(1)) * _WINDOW_UNITS[m.group(2) or "d"])
        t1 = datetime.fromisoformat(args.t1) if args.t1 else datetime.fromisoformat(DATASET_END)
        t1 = t1.replace(tzinfo=timezone.utc)
        t0 = t1 - span
        return t0.date().isoformat(), t1.date().isoformat()
    raise ValueError("a window is required — pass --t0 and --t1, or --window 30d "
                     "(or --demo for the bundled offline demo)")


def _cmd_backtest(args: argparse.Namespace) -> int:
    try:
        from simmer_sdk.backtest import BacktestError, run_backtest
    except ImportError as exc:
        print(f"error: could not import the backtest engine ({exc})\n"
              "install it with:  pip install 'simmer-sdk[backtest]'", file=sys.stderr)
        return 2

    if args.demo:
        bundle, entrypoint, tape = _demo_paths()
        if not os.path.exists(os.path.join(tape, "markets.parquet")):
            print(f"error: bundled demo tape missing at {tape}", file=sys.stderr)
            return 2
        t0, t1, cadence = "2026-04-28", "2026-05-05", "12h"
        # the demo skill reads no candles — keep the run hermetic + fast.
        candles = False
    else:
        # tape is now OPTIONAL — omit it and the window slice is fetched from the
        # backend tape service and cached. --window derives [t0,t1] for convenience.
        try:
            t0, t1 = _resolve_window(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        missing = [n for n, v in (("bundle", args.bundle),
                                  ("--entrypoint", args.entrypoint)) if not v]
        if missing:
            print("error: " + ", ".join(missing) + " required "
                  "(or pass --demo for the bundled offline demo)", file=sys.stderr)
            return 2
        bundle, entrypoint, tape = args.bundle, args.entrypoint, args.tape
        cadence = args.cadence
        candles = not args.no_candles

    try:
        report = run_backtest(
            bundle,
            entrypoint=entrypoint,
            tape=tape,
            t0=t0,
            t1=t1,
            max_markets=args.max_markets,
            min_volume=args.min_volume,
            base_url=args.base_url,
            cadence=cadence,
            balance=args.balance,
            fee_rate=args.fee_rate,
            seed=args.seed,
            max_evaluations=args.max_evaluations,
            args=args.args,
            coverage_ok=args.coverage_ok,
            candles=candles,
            offline_klines=args.offline_klines,
            sdk_path=args.sdk_path,
        )
    except BacktestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    _print_summary(report, balance=args.balance)

    # The full report JSON is opt-in via --out. (Don't silently drop a file into
    # the user's CWD just for running --demo — the summary already prints.)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"full report → {args.out}")
    elif args.demo:
        print("(pass --out report.json to save the full report)")

    # A run with failed ticks is not a clean result; surface it in the exit code.
    return 1 if report.get("bundle", {}).get("failed_ticks") else 0


# -- parser -------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="simmer", description=__doc__)
    p.add_argument("--version", action="version", version=f"simmer-sdk {_version()}")
    sub = p.add_subparsers(dest="cmd", required=True)

    bt = sub.add_parser(
        "backtest",
        help="replay an unmodified skill bundle over historical data",
        description="Backtest a skill bundle against a local historical tape "
                    "slice (or the bundled --demo). Requires the [backtest] extra.",
    )
    bt.add_argument("bundle", nargs="?", help="path to the skill bundle dir")
    bt.add_argument("--entrypoint", help="script filename inside the bundle to run each tick")
    bt.add_argument("--tape", help="local tape slice dir (markets.parquet + quant.parquet); "
                                   "omit to fetch + cache the window from the tape service")
    bt.add_argument("--t0", help="window start (ISO, e.g. 2026-03-01)")
    bt.add_argument("--t1", help="window end (ISO)")
    bt.add_argument("--max-markets", type=int, default=300, dest="max_markets",
                    help="cap on markets in a fetched slice (default 300; server clamps to 1000)")
    bt.add_argument("--min-volume", type=float, default=1000.0, dest="min_volume",
                    help="min market volume for a fetched slice (default 1000)")
    bt.add_argument("--base-url", default=None, dest="base_url",
                    help="tape-service base URL (default: SIMMER_API_URL env or production)")
    bt.add_argument("--cadence", default="15m", help="tick spacing: 15m / 12h / 30d / minutes (default 15m)")
    bt.add_argument("--balance", type=float, default=1000.0, help="starting balance (default 1000)")
    bt.add_argument("--fee-rate", type=float, default=0.0, dest="fee_rate", help="per-fill fee rate (default 0)")
    bt.add_argument("--seed", type=int, default=0, help="RNG seed for the random baseline (default 0)")
    bt.add_argument("--max-evaluations", type=int, default=50_000, dest="max_evaluations",
                    help="hard ticks×markets budget (default 50000)")
    bt.add_argument("--args", default=None,
                    help="entrypoint CLI args, space-separated (default '--live --quiet')")
    bt.add_argument("--coverage-ok", action="store_true", dest="coverage_ok",
                    help="assert this window/cadence fits the skill's signal horizon "
                         "(lets a 0-trade result read as verified no-signal)")
    bt.add_argument("--no-candles", action="store_true", dest="no_candles",
                    help="don't wire the Binance candles plane (/api/replay-data/candles 404s)")
    bt.add_argument("--offline-klines", action="store_true", dest="offline_klines",
                    help="candles plane uses only pre-cached months (no network)")
    bt.add_argument("--sdk-path", default=None, dest="sdk_path",
                    help="dir containing simmer_sdk/ for the subprocess (auto-resolved by default)")
    bt.add_argument("--out", default=None, help="write the full report JSON here")
    bt.add_argument("--demo", action="store_true", help="run the bundled offline demo (no tape needed)")
    bt.add_argument("--window", default=None,
                    help="window duration to fetch, e.g. 30d / 12h — anchored to --t1 "
                         "or the dataset end. Alternative to explicit --t0/--t1.")
    bt.set_defaults(fn=_cmd_backtest)
    return p


def main(argv: Optional[list] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
