#!/usr/bin/env python3
"""
Simmer Preflight — pre-trade readiness check for autonomous agents.

Run this to verify wallet identity, venue status, balance, and exposure
before submitting a real-money trade. Reads three SDK endpoints and returns
a structured verdict without signing or mutating any state.

Usage:
    python preflight.py                        # check default venue, no planned trade
    python preflight.py --venue polymarket     # check polymarket venue
    python preflight.py --amount 5 --cap 100  # check $5 trade against $100 cap
    python preflight.py --json                 # output raw JSON (for agent ledgers)

Environment:
    SIMMER_API_KEY     required  — your SDK API key
    TRADING_VENUE      optional  — default venue (overridden by --venue)
    EXPOSURE_CAP_USD   optional  — default cap in USD (overridden by --cap)

Exit codes:
    0  — ok_to_trade = True (no blockers)
    1  — blocked (check stdout for blocker codes)
    2  — configuration error (SIMMER_API_KEY missing, invalid venue)
"""

import os
import sys
import json
import argparse
from typing import Optional

# Force line-buffered stdout for cron / Docker / OpenClaw environments
sys.stdout.reconfigure(line_buffering=True)

# Automaton heartbeat output — required when AUTOMATON_MANAGED=1
AUTOMATON_MANAGED = os.environ.get("AUTOMATON_MANAGED") == "1"


def _fmt_wallet(addr: Optional[str]) -> str:
    if not addr:
        return "none"
    return addr[:10] + "…" + addr[-6:] if len(addr) > 20 else addr


def run(
    venue: Optional[str] = None,
    planned_amount: float = 0.0,
    exposure_cap_usd: Optional[float] = None,
    as_json: bool = False,
) -> int:
    """Run the preflight check and return exit code (0 = ok, 1 = blocked)."""
    api_key = os.environ.get("SIMMER_API_KEY")
    if not api_key:
        print("ERROR: SIMMER_API_KEY not set", file=sys.stderr)
        return 2

    _cap = exposure_cap_usd if exposure_cap_usd is not None else float(
        os.environ.get("EXPOSURE_CAP_USD", "100")
    )
    _venue = venue or os.environ.get("TRADING_VENUE", "sim")

    try:
        from simmer_sdk import SimmerClient
    except ImportError:
        print("ERROR: simmer-sdk not installed — run: pip install simmer-sdk>=0.17.13", file=sys.stderr)
        return 2

    try:
        client = SimmerClient(api_key=api_key, venue=_venue)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    pf = client.preflight(
        venue=_venue,
        planned_amount=planned_amount,
        exposure_cap_usd=_cap,
    )

    if as_json:
        import dataclasses
        print(json.dumps(dataclasses.asdict(pf), indent=2))
    else:
        _ok = "✅ OK" if pf.ok_to_trade else "❌ BLOCKED"
        print(f"Preflight {_ok} — id={pf.client_preflight_id}")
        print(f"  Agent:   {pf.agent_id or 'unknown'}  tier={pf.tier or '?'}")
        print(f"  Venue:   {pf.resolved_venue}  signer={pf.signer_status}")
        print(f"  Wallet:  execution={_fmt_wallet(pf.execution_wallet)}  "
              f"deposit={_fmt_wallet(pf.deposit_wallet)}")
        if pf.spendable_balance is not None:
            if pf.resolved_venue == "sim":
                print(f"  Balance: {pf.spendable_balance:.4f} $SIM")
            else:
                print(f"  Balance: ${pf.spendable_balance:.4f}")
        print(f"  Exposure: {pf.open_exposure_total:.4f} open"
              f"  + {pf.planned_amount:.4f} planned"
              f"  / {pf.exposure_cap_usd:.4f} cap")
        if pf.blockers:
            print(f"  Blockers: {', '.join(pf.blockers)}")
        if pf.warnings:
            for w in pf.warnings:
                print(f"  Warning: {w}")
        if pf.pending_alerts:
            for a in pf.pending_alerts:
                _msg = a.get("message", str(a)) if isinstance(a, dict) else str(a)
                print(f"  Alert: {_msg}")

    if AUTOMATON_MANAGED:
        print(json.dumps({
            "automaton": {
                "signals": 1 if pf.ok_to_trade else 0,
                "trades_attempted": 0,
                "ok_to_trade": pf.ok_to_trade,
                "blockers": pf.blockers,
                "client_preflight_id": pf.client_preflight_id,
            }
        }))

    return 0 if pf.ok_to_trade else 1


def main():
    parser = argparse.ArgumentParser(
        description="Simmer pre-trade readiness check",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--venue", default=None,
        help="Trading venue to check (sim / polymarket / kalshi). "
             "Defaults to TRADING_VENUE env var or 'sim'.",
    )
    parser.add_argument(
        "--amount", type=float, default=0.0,
        help="Planned trade size in USD / $SIM. Used for cap math. Default: 0.",
    )
    parser.add_argument(
        "--cap", type=float, default=None,
        help="Exposure cap in USD. Defaults to EXPOSURE_CAP_USD env or 100.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json",
        help="Output full result as JSON instead of human-readable summary.",
    )
    args = parser.parse_args()

    sys.exit(run(
        venue=args.venue,
        planned_amount=args.amount,
        exposure_cap_usd=args.cap,
        as_json=args.as_json,
    ))


if __name__ == "__main__":
    main()
