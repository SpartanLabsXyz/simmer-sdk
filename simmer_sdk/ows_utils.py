"""
OWS (Open Wallet Standard) utilities for Simmer SDK.

Handles wallet detection, address resolution, signing delegation,
and Polymarket CLOB credential derivation — all without exposing
the private key outside the OWS vault.
"""

import json
from typing import Optional, Tuple
from dataclasses import dataclass


def _check_ows() -> bool:
    """Check if OWS Python SDK is importable."""
    try:
        import ows  # noqa: F401
        return True
    except (ImportError, ModuleNotFoundError):
        return False


def is_ows_available() -> bool:
    """Check if OWS is installed and usable."""
    return _check_ows()


def get_ows_wallet_address(wallet_name: str) -> str:
    """
    Get the EVM address for an OWS wallet.

    Args:
        wallet_name: Name or ID of the OWS wallet.

    Returns:
        EVM address (0x-prefixed, checksummed).

    Raises:
        ImportError: If OWS is not installed.
        ValueError: If wallet not found or has no EVM account.
    """
    try:
        import ows
    except ImportError:
        raise ImportError(
            "open-wallet-standard is required for OWS wallet mode. "
            "Install with: pip install open-wallet-standard"
        )

    try:
        wallet_info = ows.get_wallet(wallet_name)
    except Exception as e:
        raise ValueError(f"OWS wallet '{wallet_name}' not found: {e}")

    evm_accounts = [
        a for a in wallet_info.get("accounts", [])
        if a.get("chain_id", "").startswith("eip155")
    ]
    if not evm_accounts:
        raise ValueError(
            f"No EVM account found in OWS wallet '{wallet_name}'. "
            f"Available chains: {[a['chain_id'] for a in wallet_info.get('accounts', [])]}"
        )

    return evm_accounts[0]["address"]


def ows_sign_typed_data(wallet_name: str, typed_data_json: str) -> str:
    """
    Sign EIP-712 typed data using an OWS wallet.

    Args:
        wallet_name: Name of the OWS wallet.
        typed_data_json: JSON string of EIP-712 typed data.

    Returns:
        Hex-encoded signature string.
    """
    import ows

    result = ows.sign_typed_data(
        wallet=wallet_name,
        chain="polygon",
        typed_data_json=typed_data_json,
    )
    return result["signature"]


def ows_sign_message(wallet_name: str, message: str) -> str:
    """
    Sign a personal message using an OWS wallet.

    Used for wallet linking challenge-response.

    Args:
        wallet_name: Name of the OWS wallet.
        message: Message to sign.

    Returns:
        Hex-encoded signature string.
    """
    import ows

    result = ows.sign_message(
        wallet=wallet_name,
        chain="polygon",
        message=message,
    )
    return result["signature"]


# --- Polymarket CLOB credential derivation via OWS ---

CLOB_HOST = "https://clob.polymarket.com"
CLOB_AUTH_DOMAIN_NAME = "ClobAuthDomain"
CLOB_AUTH_VERSION = "1"
CLOB_AUTH_CHAIN_ID = 137
CLOB_AUTH_MESSAGE = "This message attests that I control the given wallet"


@dataclass
class ClobApiCreds:
    """Polymarket CLOB API credentials."""
    api_key: str
    api_secret: str
    api_passphrase: str


def _build_clob_auth_typed_data(address: str, timestamp: int, nonce: int = 0) -> str:
    """Build the EIP-712 typed data JSON for Polymarket CLOB Level 1 auth."""
    typed_data = {
        "primaryType": "ClobAuth",
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "ClobAuth": [
                {"name": "address", "type": "address"},
                {"name": "timestamp", "type": "string"},
                {"name": "nonce", "type": "uint256"},
                {"name": "message", "type": "string"},
            ],
        },
        "domain": {
            "name": CLOB_AUTH_DOMAIN_NAME,
            "version": CLOB_AUTH_VERSION,
            "chainId": CLOB_AUTH_CHAIN_ID,
        },
        "message": {
            "address": address,
            "timestamp": str(timestamp),
            "nonce": nonce,
            "message": CLOB_AUTH_MESSAGE,
        },
    }
    return json.dumps(typed_data)


def _clob_level_1_headers(wallet_name: str, address: str, nonce: int = 0) -> dict:
    """Build Polymarket CLOB Level 1 auth headers using OWS signing."""
    from datetime import datetime

    timestamp = int(datetime.now().timestamp())
    typed_data_json = _build_clob_auth_typed_data(address, timestamp, nonce)
    signature = ows_sign_typed_data(wallet_name, typed_data_json)

    # Polymarket expects 0x-prefixed signature
    if not signature.startswith("0x"):
        signature = "0x" + signature

    return {
        "POLY_ADDRESS": address,
        "POLY_SIGNATURE": signature,
        "POLY_TIMESTAMP": str(timestamp),
        "POLY_NONCE": str(nonce),
    }


def ows_derive_clob_creds(wallet_name: str, nonce: int = 0) -> ClobApiCreds:
    """
    Derive Polymarket CLOB API credentials using an OWS wallet.

    Creates or derives CLOB API keys by signing the auth challenge
    with OWS — the private key never leaves the vault.

    Args:
        wallet_name: Name of the OWS wallet.
        nonce: Nonce for credential derivation (default 0).

    Returns:
        ClobApiCreds with api_key, api_secret, api_passphrase.

    Raises:
        ValueError: If credential derivation fails.
    """
    import requests

    address = get_ows_wallet_address(wallet_name)
    headers = _clob_level_1_headers(wallet_name, address, nonce)

    # Try create first, fall back to derive (same pattern as py_clob_client)
    for endpoint in ["/auth/api-key", "/auth/derive-api-key"]:
        method = requests.post if endpoint == "/auth/api-key" else requests.get
        try:
            resp = method(
                f"{CLOB_HOST}{endpoint}",
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return ClobApiCreds(
                api_key=data["apiKey"],
                api_secret=data["secret"],
                api_passphrase=data["passphrase"],
            )
        except requests.exceptions.HTTPError:
            if endpoint == "/auth/api-key":
                # Create failed — try derive
                headers = _clob_level_1_headers(wallet_name, address, nonce)
                continue
            raise
        except (KeyError, TypeError) as e:
            raise ValueError(f"Failed to parse CLOB credentials: {e}")

    raise ValueError("Failed to create or derive CLOB API credentials")
