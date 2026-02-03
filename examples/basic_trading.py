"""
Basic Simmer SDK Example

A minimal example showing how to:
1. Connect to Simmer
2. List available markets
3. Execute a trade
4. Check your positions

Usage:
    export SIMMER_API_KEY="sk_live_..."
    python basic_trading.py
"""

import os
from simmer_sdk import SimmerClient

# Initialize client (uses simmer by default - virtual $SIM currency)
client = SimmerClient(api_key=os.environ["SIMMER_API_KEY"])

# List active markets from Polymarket
print("Available markets:")
markets = client.get_markets(import_source="polymarket", limit=5)
for m in markets:
    print(f"  [{m.id[:8]}] {m.question[:60]}... @ {m.current_probability:.0%}")

if not markets:
    print("No markets found. Import some first via the dashboard.")
    exit()

# Execute a trade on the first market
market = markets[0]
print(f"\nTrading on: {market.question[:50]}...")

result = client.trade(
    market_id=market.id,
    side="yes",
    amount=10.0  # $10 of virtual $SIM
)

if result.success:
    print(f"  Bought {result.shares_bought:.2f} YES shares for ${result.cost:.2f}")
    print(f"  New market price: {result.new_price:.1%}")
    print(f"  Remaining balance: ${result.balance:.2f}")
else:
    print(f"  Trade failed: {result.error}")

# Check positions
print("\nYour positions:")
positions = client.get_positions()
for p in positions:
    print(f"  {p.question[:40]}... P&L: ${p.pnl:+.2f}")

print(f"\nTotal P&L: ${client.get_total_pnl():.2f}")
