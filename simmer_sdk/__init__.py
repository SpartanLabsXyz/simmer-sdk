"""
Simmer SDK - Python client for Simmer prediction markets

Usage:
    from simmer_sdk import SimmerClient

    client = SimmerClient(api_key="sk_live_...")

    # List markets
    markets = client.get_markets(import_source="polymarket")

    # Execute trade
    result = client.trade(market_id="...", side="yes", amount=10.0)

    # Get positions
    positions = client.get_positions()
"""

from .client import SimmerClient

__version__ = "0.2.3"
__all__ = ["SimmerClient"]
