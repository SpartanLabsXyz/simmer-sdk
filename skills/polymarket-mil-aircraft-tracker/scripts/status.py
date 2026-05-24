#!/usr/bin/env python3
"""Status dashboard for polymarket-mil-aircraft-tracker."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from pref_client import get_account_status, get_military_aircraft
from regions import filter_aircraft_by_regions, load_regions

STATE_DIR = os.path.expanduser("~/.simmer/milaircraft-tracker")
STATE_FILE = os.path.join(STATE_DIR, "state.json")


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def print_status():
    state = load_state()
    last_tick = state.get("last_tick_at", "never")
    daily_pnl = float(state.get("daily_pnl", 0.0) or 0.0)
    daily_trades = int(state.get("daily_trades", 0) or 0)

    regions = load_regions()
    aircraft = get_military_aircraft()
    clusters = filter_aircraft_by_regions(aircraft, regions)

    account = get_account_status() or {}
    quota_used = account.get("used", account.get("quota_used", "?"))
    quota_limit = account.get("limit", account.get("quota_limit", "?"))
    tier = account.get("tier", "unknown")

    print("MILITARY AIRCRAFT TRACKER - STATUS")
    print("=" * 65)
    print(f"  Last tick: {last_tick}")
    print(f"  Aircraft fetched now: {len(aircraft)}")
    print()

    print("CLUSTER STATE")
    print("-" * 65)
    print(f"{'Region':<25} {'Tracked':<9} {'Threshold':<11} {'Status':<10}")
    print("-" * 65)
    for name, data in clusters.items():
        status = "FIRED" if data["fired"] else "idle"
        print(f"  {name:<23} {data['count']:<9} {data['cluster_threshold']:<11} {status:<10}")
    print()

    positions = state.get("open_positions", [])
    if positions:
        print(f"OPEN POSITIONS ({len(positions)})")
        print("-" * 65)
        for pos in positions:
            market = pos.get("market", pos.get("market_id", "?"))[:40]
            print(f"  {market:<42} {pos.get('side', '?'):<5} ${float(pos.get('size', 0) or 0):.2f}")
        print()

    print("24h ACTIVITY")
    print("-" * 65)
    print(f"  Daily trades: {daily_trades}")
    print(f"  Daily P&L: ${daily_pnl:+.2f}")
    print(f"  Pref quota: {quota_used} / {quota_limit} ({tier})")
    print()


if __name__ == "__main__":
    print_status()
