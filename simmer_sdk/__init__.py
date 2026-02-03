"""
Simmer SDK - Python client for Simmer prediction markets

Usage:
    from simmer_sdk import SimmerClient

    client = SimmerClient(api_key="sk_live_...")

    # List markets
    markets = client.get_markets(import_source="polymarket")

    # Execute trade (simmer with $SIM virtual currency)
    result = client.trade(market_id="...", side="yes", amount=10.0)

    # Get positions
    positions = client.get_positions()

External Wallet Trading (BYOW):
    The SDK supports trading with your own wallet (Bring Your Own Wallet).

    POLYMARKET (EVM wallet):
        # Set SIMMER_PRIVATE_KEY env var to your EVM private key (0x...)
        client = SimmerClient(api_key="sk_live_...", venue="polymarket")
        result = client.trade(...)  # Signs locally with EVM key

        # Or pass explicitly
        client = SimmerClient(
            api_key="sk_live_...",
            venue="polymarket",
            private_key="0x..."
        )

    KALSHI (Solana wallet):
        # Set SIMMER_SOLANA_KEY env var to your base58 Solana secret key
        client = SimmerClient(api_key="sk_live_...", venue="kalshi")
        result = client.trade(...)  # Signs locally with Solana key

        # Note: Kalshi BYOW requires Node.js for signing.
        # Run `npm install` in the SDK directory to install dependencies.

    The SDK will:
    - Auto-detect env vars (SIMMER_PRIVATE_KEY for EVM, SIMMER_SOLANA_KEY for Solana)
    - Auto-link EVM wallet on first Polymarket trade
    - Warn about missing Polymarket approvals

    For manual control (Polymarket):
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
from .solana_signing import (
    sign_solana_transaction,
    has_solana_key,
    get_solana_public_key,
    validate_solana_key,
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
    # Polymarket approvals
    "get_required_approvals",
    "get_approval_transactions",
    "get_missing_approval_transactions",
    "format_approval_guide",
    # Solana signing (Kalshi BYOW)
    "sign_solana_transaction",
    "has_solana_key",
    "get_solana_public_key",
    "validate_solana_key",
]
