#!/usr/bin/env python3
"""Demo skill for `simmer backtest --demo` — NOT a real strategy.

Buys a small fixed YES position on the most liquid favorites each tick, skipping
markets it already holds. Pure stdlib + requests against the replay server the
harness points us at via SIMMER_API_URL; no SDK client, no wallet, no network
beyond the local replay app. The point is to exercise the whole backtest
pipeline end-to-end offline and show a non-trivial report.
"""

import os
import sys

import requests

BASE = os.environ["SIMMER_API_URL"].rstrip("/")
KEY = os.environ.get("SIMMER_API_KEY", "sk_replay")
HEADERS = {"Authorization": f"Bearer {KEY}"}

# Favorites band + sizing. Mid-to-high YES price = "the market thinks this is
# likely" without being already-decided (>0.95 is no edge, just settlement).
MIN_YES = 0.50
MAX_YES = 0.92
STAKE = 25.0
MAX_BUYS_PER_TICK = 3


def _held_market_ids() -> set:
    r = requests.get(f"{BASE}/api/sdk/positions", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return {p["market_id"] for p in r.json().get("positions", [])}


def main() -> int:
    quiet = "--quiet" in sys.argv
    held = _held_market_ids()

    r = requests.get(f"{BASE}/api/sdk/markets", params={"limit": 50},
                     headers=HEADERS, timeout=30)
    r.raise_for_status()
    markets = r.json().get("markets", [])

    # Most liquid favorites first.
    candidates = sorted(
        (m for m in markets
         if m.get("yes_price") is not None
         and MIN_YES <= m["yes_price"] <= MAX_YES
         and m["id"] not in held),
        key=lambda m: m.get("volume", 0.0),
        reverse=True,
    )

    bought = 0
    for m in candidates[:MAX_BUYS_PER_TICK]:
        resp = requests.post(
            f"{BASE}/api/sdk/trade",
            json={
                "market_id": m["id"],
                "side": "yes",
                "action": "buy",
                "amount": STAKE,
                "reasoning": f"demo: favorite @ yes={m['yes_price']:.2f}",
                "skill_slug": "backtest-demo-favorites",
                "source": "backtest-demo",
            },
            headers=HEADERS,
            timeout=30,
        )
        if resp.ok and resp.json().get("success"):
            bought += 1
        elif not quiet:
            print(f"  skip {m['slug']}: {resp.text[:120]}")

    if not quiet:
        print(f"demo tick: {len(candidates)} favorites available, bought {bought}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
