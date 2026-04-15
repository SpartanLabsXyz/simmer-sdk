"""
OWS (Open Wallet Standard) utilities for Simmer SDK.

Handles wallet detection, address resolution, and signing delegation.
OWS is optional — the SDK works without it using raw private keys.
"""


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
