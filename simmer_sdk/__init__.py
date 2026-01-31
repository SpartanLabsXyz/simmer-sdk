"""
Simmer SDK - Python client for Simmer prediction markets

Usage:
    from simmer_sdk import SimmerClient

    client = SimmerClient(api_key="sk_live_...")

    # List markets
    markets = client.get_markets(import_source="polymarket")

    # Execute trade (server-side signing with Simmer-managed wallet)
    result = client.trade(market_id="...", side="yes", amount=10.0)

    # Get positions
    positions = client.get_positions()

External Wallet Trading (BYOW):
    The SDK supports trading with your own Polymarket wallet (Bring Your Own Wallet).
    There are two ways to configure this:

    Option 1: Environment variable (recommended for clawbots/skills)
        # Set in your environment or config.yaml:
        # SIMMER_PRIVATE_KEY=0x...

        # SDK auto-detects and uses your wallet
        client = SimmerClient(api_key="sk_live_...", venue="polymarket")
        result = client.trade(...)  # Signs locally, auto-links wallet

    Option 2: Explicit parameter
        client = SimmerClient(
            api_key="sk_live_...",
            venue="polymarket",
            private_key="0x..."  # Your wallet's private key
        )
        result = client.trade(...)  # Signs locally, auto-links wallet

    The SDK will:
    - Auto-detect SIMMER_PRIVATE_KEY env var if private_key not provided
    - Auto-link your wallet on first trade (if not already linked)
    - Warn about missing Polymarket approvals

    For manual control:
        client.link_wallet()  # Explicitly link wallet
        client.check_approvals()  # Check approval status
        client.ensure_approvals()  # Get missing approval tx data

    SECURITY WARNING:
    - Never log or print your private key
    - Never commit it to version control
    - Use environment variables or secure secret management
"""

from .client import SimmerClient
from .approvals import (
    get_required_approvals,
    get_approval_transactions,
    get_missing_approval_transactions,
    format_approval_guide,
)

# Single source of truth: read version from package metadata (set in pyproject.toml)
try:
    from importlib.metadata import version as _get_version, PackageNotFoundError
    __version__ = _get_version("simmer-sdk")
except PackageNotFoundError:
    # Package not installed (editable/dev install)
    __version__ = "dev"
except ImportError:
    # Python < 3.8 (shouldn't happen, but fallback gracefully)
    __version__ = "dev"
__all__ = [
    "SimmerClient",
    "get_required_approvals",
    "get_approval_transactions",
    "get_missing_approval_transactions",
    "format_approval_guide",
]
