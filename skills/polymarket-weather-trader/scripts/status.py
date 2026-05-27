#!/usr/bin/env python3
"""
Simmer Account Status

Shows wallet balance, positions, and recent activity.

Usage:
    python scripts/status.py
    python scripts/status.py --positions  # Detailed position list
"""

import sys
import argparse

sys.stdout.reconfigure(line_buffering=True)


def format_usd(amount: float) -> str:
    return f"${amount:,.2f}"


def main():
    parser = argparse.ArgumentParser(description="Check Simmer account status")
    parser.add_argument("--positions", action="store_true", help="Show detailed positions")
    args = parser.parse_args()

    try:
        from simmer_sdk import SimmerClient
    except ImportError:
        print("Error: simmer-sdk not installed. Run: pip install --upgrade simmer-sdk")
        sys.exit(1)

    try:
        client = SimmerClient.from_env()
    except Exception as e:
        print(f"Error: {e}")
        print("   Set SIMMER_API_KEY or get your key from: https://simmer.markets/dashboard")
        sys.exit(1)

    print("Fetching account status...\n")

    try:
        portfolio = client.get_portfolio()
    except Exception as e:
        print(f"API Error: {e}")
        sys.exit(1)

    balance = portfolio.get("balance_usdc", 0)
    exposure = portfolio.get("total_exposure", 0)
    positions_count = portfolio.get("positions_count", 0)
    pnl_total = portfolio.get("pnl_total")
    pnl_24h = portfolio.get("pnl_24h")

    print("=" * 50)
    print("ACCOUNT SUMMARY")
    print("=" * 50)
    print(f"  Available Balance:  {format_usd(balance)}")
    print(f"  Total Exposure:     {format_usd(exposure)}")
    print(f"  Open Positions:     {positions_count}")

    if pnl_total is not None:
        sign = "+" if pnl_total >= 0 else ""
        print(f"  Total PnL:          {sign}{format_usd(pnl_total)}")

    if pnl_24h is not None:
        sign = "+" if pnl_24h >= 0 else ""
        print(f"  24h PnL:            {sign}{format_usd(pnl_24h)}")

    concentration = portfolio.get("concentration", {})
    top_market_pct = concentration.get("top_market_pct", 0)
    if top_market_pct > 0.5:
        print(f"\n  WARNING: High concentration: {top_market_pct:.0%} in top market")

    by_source = portfolio.get("by_source", {})
    if by_source:
        print("\n  By Source:")
        for source, data in by_source.items():
            src_positions = data.get("positions", 0)
            src_exposure = data.get("exposure", 0)
            print(f"      {source}: {src_positions} positions, {format_usd(src_exposure)}")

    print("=" * 50)

    if args.positions:
        print("\nOPEN POSITIONS")
        print("=" * 50)

        try:
            positions = client.get_positions()
        except Exception as e:
            print(f"API Error: {e}")
            sys.exit(1)

        if not positions:
            print("  No open positions")
        else:
            for pos in positions:
                question = getattr(pos, "question", None) or getattr(pos, "market_id", "Unknown")
                if len(question) > 50:
                    question = question[:47] + "..."

                shares_yes = getattr(pos, "shares_yes", 0) or 0
                shares_no = getattr(pos, "shares_no", 0) or 0
                current_price = getattr(pos, "current_price", 0) or 0
                cost_basis = getattr(pos, "cost_basis", 0) or 0
                pnl = getattr(pos, "pnl", 0) or 0

                if shares_yes > 0:
                    side = "YES"
                    shares = shares_yes
                elif shares_no > 0:
                    side = "NO"
                    shares = shares_no
                else:
                    continue

                indicator = "+" if pnl >= 0 else ""
                print(f"\n  {question}")
                print(f"    {side}: {shares:.2f} shares, cost ${cost_basis:.2f}")
                print(f"    Current: {current_price:.1%} | PnL: {indicator}{format_usd(pnl)}")

        print("\n" + "=" * 50)

    if balance == 0:
        print("\nTip: Deposit funds at https://simmer.markets/dashboard")

    print()


if __name__ == "__main__":
    main()
