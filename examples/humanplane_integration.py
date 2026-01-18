"""
Humanplane + Simmer SDK Integration Example

This shows how to adapt humanplane's RL trading bot to execute on Simmer markets.

Key changes from original humanplane:
1. Replace paper trading with Simmer SDK calls
2. Map 15-min Polymarket markets to imported Simmer markets
3. Use Simmer's LMSR pricing (no orderbook)

Usage:
    export SIMMER_API_KEY="sk_live_..."
    python humanplane_integration.py --asset BTC
"""

import os
import sys
import time
from dataclasses import dataclass
from typing import Optional, Dict
from enum import Enum

# Add simmer SDK to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from simmer_sdk import SimmerClient


class Action(Enum):
    HOLD = 0
    BUY = 1   # Buy YES (bullish)
    SELL = 2  # Buy NO (bearish)


@dataclass
class Position:
    """Active position on Simmer."""
    market_id: str
    side: str  # "yes" or "no"
    shares: float
    entry_price: float
    entry_cost: float


class SimmerExecutor:
    """
    Executes trades on Simmer markets.

    Replaces humanplane's paper trading with real SDK execution.
    """

    def __init__(self, api_key: str, base_url: str = "https://api.simmer.markets"):
        self.client = SimmerClient(api_key=api_key, base_url=base_url)
        self.positions: Dict[str, Position] = {}  # market_id -> Position
        self.total_pnl = 0.0
        self.trade_count = 0

    def get_active_markets(self, asset: str = "BTC") -> list:
        """
        Get Simmer markets for a given asset.

        For 15-min up/down markets, filter by question containing the asset.
        """
        markets = self.client.get_markets(
            status="active",
            import_source="polymarket",
            limit=50
        )

        # Filter by asset (e.g., "BTC", "ETH")
        asset_markets = [
            m for m in markets
            if asset.upper() in m.question.upper()
        ]

        return asset_markets

    def execute_action(
        self,
        market_id: str,
        action: Action,
        current_price: float,
        trade_size: float = 10.0
    ) -> Optional[dict]:
        """
        Execute a trade based on RL action.

        Args:
            market_id: Simmer market ID
            action: BUY (yes), SELL (no), or HOLD
            current_price: Current YES probability (0-1)
            trade_size: Dollar amount to trade

        Returns:
            Trade result dict or None if HOLD
        """
        if action == Action.HOLD:
            return None

        # Check if we have an existing position
        existing = self.positions.get(market_id)

        if existing:
            # Close existing position by trading opposite side
            # On Simmer LMSR, we just trade the opposite to reduce exposure
            close_side = "no" if existing.side == "yes" else "yes"

            result = self.client.trade(
                market_id=market_id,
                side=close_side,
                amount=existing.entry_cost  # Exit with same size
            )

            if result.success:
                # Calculate P&L
                exit_value = result.shares_bought * (
                    current_price if close_side == "yes" else (1 - current_price)
                )
                pnl = exit_value - existing.entry_cost
                self.total_pnl += pnl
                self.trade_count += 1

                print(f"  CLOSE {existing.side.upper()} -> P&L: ${pnl:+.2f}")

                del self.positions[market_id]

            return result

        else:
            # Open new position
            side = "yes" if action == Action.BUY else "no"

            result = self.client.trade(
                market_id=market_id,
                side=side,
                amount=trade_size
            )

            if result.success:
                self.positions[market_id] = Position(
                    market_id=market_id,
                    side=side,
                    shares=result.shares_bought,
                    entry_price=current_price if side == "yes" else (1 - current_price),
                    entry_cost=result.cost
                )

                print(f"  OPEN {side.upper()} ${trade_size:.0f} @ {result.new_price:.3f}")
                self.trade_count += 1

            return result

    def get_position(self, market_id: str) -> Optional[Position]:
        """Get current position for a market."""
        return self.positions.get(market_id)

    def close_all_positions(self, markets_prices: Dict[str, float]):
        """Close all open positions (e.g., at end of session)."""
        for market_id, pos in list(self.positions.items()):
            price = markets_prices.get(market_id, 0.5)
            close_action = Action.SELL if pos.side == "yes" else Action.BUY
            self.execute_action(market_id, close_action, price)

    def print_summary(self):
        """Print trading summary."""
        print(f"\n{'='*50}")
        print(f"Total P&L: ${self.total_pnl:+.2f}")
        print(f"Trades: {self.trade_count}")
        print(f"Open positions: {len(self.positions)}")

        # Get positions from SDK for accurate P&L
        positions = self.client.get_positions()
        for p in positions:
            print(f"  {p.question[:40]}... P&L: ${p.pnl:+.2f}")


def main():
    """Example integration loop."""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", default=os.getenv("SIMMER_API_KEY"))
    parser.add_argument("--base-url", default="https://api.simmer.markets")
    parser.add_argument("--asset", default="BTC")
    parser.add_argument("--size", type=float, default=10.0)
    args = parser.parse_args()

    if not args.api_key:
        print("Error: Set SIMMER_API_KEY or use --api-key")
        sys.exit(1)

    executor = SimmerExecutor(args.api_key, args.base_url)

    print(f"Simmer Executor initialized")
    print(f"Looking for {args.asset} markets...")

    markets = executor.get_active_markets(args.asset)
    print(f"Found {len(markets)} markets")

    if not markets:
        print("No markets found. Import some Polymarket markets first.")
        return

    for m in markets[:5]:
        print(f"  - {m.question[:60]}...")
        print(f"    Price: {m.current_probability:.1%}, External: {m.external_price_yes or 'N/A'}")

    # Example: Execute a single trade
    if markets:
        market = markets[0]
        print(f"\nExecuting test trade on: {market.question[:50]}...")

        result = executor.execute_action(
            market_id=market.id,
            action=Action.BUY,
            current_price=market.current_probability,
            trade_size=args.size
        )

        if result and result.success:
            print(f"Trade successful!")
            print(f"  Shares: {result.shares_bought:.2f}")
            print(f"  Cost: ${result.cost:.2f}")
            print(f"  New price: {result.new_price:.3f}")

    executor.print_summary()


if __name__ == "__main__":
    main()
