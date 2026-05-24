#!/usr/bin/env python3
"""
Polymarket Research — structured market snapshots with top holders.

Usage:
    python research.py "bitcoin"
    python research.py "election" --top-holders 20
    python research.py "weather" --min-volume 100000 --max-results 10

Requires:
    SIMMER_API_KEY environment variable
"""

import os
import sys
import json
import argparse
from dataclasses import asdict
from datetime import datetime, timezone

sys.stdout.reconfigure(line_buffering=True)

SKILL_SLUG = "polymarket-research"
TRADE_SOURCE = "sdk:pm-research"

_client = None

def get_client():
    global _client
    if _client is None:
        try:
            from simmer_sdk import SimmerClient
        except ImportError:
            print("Error: simmer-sdk not installed. Run: pip install simmer-sdk")
            sys.exit(1)
        api_key = os.environ.get("SIMMER_API_KEY")
        if not api_key:
            print("Error: SIMMER_API_KEY environment variable not set")
            sys.exit(1)
        _client = SimmerClient(api_key=api_key, venue="polymarket", live=False)
    return _client


def research_topic(query, min_volume=10000, max_results=5, top_holders_count=10):
    client = get_client()

    print(f"\n🔍 Searching Polymarket for: \"{query}\"")
    print("=" * 60)

    markets = client.find_markets(query)
    if not markets:
        importable = client.list_importable_markets(q=query, min_volume=min_volume, limit=max_results)
        if importable:
            print(f"\nNo indexed markets found, but {len(importable)} importable:")
            for m in importable:
                print(f"  • {m['question']} (${m.get('volume_24h', 0):,.0f} vol)")
                print(f"    Import: client.import_market(\"{m['url']}\")")
            return
        print(f"\nNo markets found for \"{query}\". Try broader terms.")
        return

    filtered = [m for m in markets if (m.volume_24h or 0) >= min_volume]
    if not filtered:
        print(f"\n{len(markets)} markets found but none above ${min_volume:,.0f} volume.")
        print("Try --min-volume 0 to see all.")
        return

    filtered.sort(key=lambda m: m.volume_24h or 0, reverse=True)
    results = filtered[:max_results]

    for i, market in enumerate(results, 1):
        prob = market.current_probability or 0.5
        print(f"\n{'─' * 60}")
        print(f"{i}. {market.question}")
        print(f"   YES: {prob:.1%} | NO: {1 - prob:.1%}")
        print(f"   Volume (24h): ${market.volume_24h or 0:,.0f}")
        if market.resolves_at:
            print(f"   Resolves: {market.resolves_at[:10]}")
        if market.divergence and abs(market.divergence) > 0.03:
            print(f"   AI divergence: {market.divergence:+.1%} (Simmer AI vs Polymarket)")

        if top_holders_count > 0 and market.polymarket_condition_id:
            holders = client.get_top_holders(
                market.polymarket_condition_id,
                limit=top_holders_count,
            )
            if holders:
                yes_holders = [h for h in holders if h["outcome"] == "Yes"]
                no_holders = [h for h in holders if h["outcome"] == "No"]

                if yes_holders:
                    print(f"\n   Top holders (YES):")
                    for h in yes_holders[:5]:
                        print(f"     {h['display_name']}: {h['amount']:,.0f} shares")

                if no_holders:
                    print(f"\n   Top holders (NO):")
                    for h in no_holders[:5]:
                        print(f"     {h['display_name']}: {h['amount']:,.0f} shares")
            elif market.polymarket_condition_id:
                print(f"\n   Top holders: (none available)")

    print(f"\n{'─' * 60}")
    print(f"Retrieved {len(results)} market(s) at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")


def main():
    parser = argparse.ArgumentParser(description="Polymarket Research")
    parser.add_argument("query", help="Topic to search for")
    parser.add_argument("--min-volume", type=float,
                        default=float(os.environ.get("SIMMER_RESEARCH_MIN_VOLUME", 10000)))
    parser.add_argument("--max-results", type=int,
                        default=int(os.environ.get("SIMMER_RESEARCH_MAX_RESULTS", 5)))
    parser.add_argument("--top-holders", type=int,
                        default=int(os.environ.get("SIMMER_RESEARCH_TOP_HOLDERS", 10)))
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()
    research_topic(args.query, args.min_volume, args.max_results, args.top_holders)


if __name__ == "__main__":
    main()
