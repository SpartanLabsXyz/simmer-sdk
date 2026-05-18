#!/usr/bin/env python3
"""
Sports-trader status — balance + sports-tagged open positions.

Usage:
    python scripts/status.py
    python scripts/status.py --positions   # Detailed position list
"""

import argparse
import os
import sys

sys.stdout.reconfigure(line_buffering=True)

TRADE_SOURCE = "sdk:sports-trader"


def main():
    parser = argparse.ArgumentParser(description="Simmer sports-trader status")
    parser.add_argument("--positions", action="store_true", help="Show detailed positions")
    args = parser.parse_args()

    api_key = os.environ.get("SIMMER_API_KEY")
    if not api_key:
        print("❌ SIMMER_API_KEY not set. Get one from simmer.markets/dashboard → SDK tab.")
        sys.exit(1)

    try:
        from simmer_sdk import SimmerClient
    except ImportError:
        print("❌ simmer-sdk not installed. Run: pip install simmer-sdk")
        sys.exit(1)

    venue = os.environ.get("TRADING_VENUE", "polymarket")
    client = SimmerClient(api_key=api_key, venue=venue)

    try:
        portfolio = client.get_portfolio()
    except Exception as e:
        print(f"❌ Failed to fetch portfolio: {e}")
        sys.exit(1)

    if isinstance(portfolio, dict):
        balance = portfolio.get("balance_usdc") or portfolio.get("balance") or 0.0
        exposure = portfolio.get("total_exposure", 0.0)
        positions_count = portfolio.get("positions_count", 0)
    else:
        balance = getattr(portfolio, "balance_usdc", 0.0)
        exposure = getattr(portfolio, "total_exposure", 0.0)
        positions_count = getattr(portfolio, "positions_count", 0)

    print("=" * 50)
    print("🏀 SPORTS TRADER STATUS")
    print("=" * 50)
    print(f"  Available balance: ${balance:,.2f}")
    print(f"  Total exposure:    ${exposure:,.2f}")
    print(f"  Open positions:    {positions_count}")

    try:
        positions = client.get_positions(source=TRADE_SOURCE)
    except Exception as e:
        print(f"\n⚠️  Failed to fetch sports positions: {e}")
        return

    print(f"\n  Sports-trader positions: {len(positions)}")

    if args.positions and positions:
        print("\n" + "=" * 50)
        for p in positions:
            question = (getattr(p, "market_question", "") or "")[:60]
            shares = getattr(p, "shares", 0.0)
            avg = getattr(p, "avg_price", 0.0)
            print(f"  {question:<62} {shares:>8.2f} shares @ {avg:.3f}")


if __name__ == "__main__":
    main()
