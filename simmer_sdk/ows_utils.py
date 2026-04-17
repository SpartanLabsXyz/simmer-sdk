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


def _coerce_typed_data_uints(typed_data_json: str) -> str:
    """
    Coerce uint values in EIP-712 typed data to strings.

    OWS's Rust EIP-712 parser requires uint values as strings when they
    exceed JavaScript's Number.MAX_SAFE_INTEGER (e.g., Polymarket token IDs).
    This converts all uint fields in the message to string representation.
    """
    data = json.loads(typed_data_json)
    types = data.get("types", {})
    primary_type = data.get("primaryType", "")
    message = data.get("message", {})

    # Find which fields are uint types
    uint_fields = set()
    for field in types.get(primary_type, []):
        if field["type"].startswith("uint"):
            uint_fields.add(field["name"])

    # Convert int values for uint fields:
    # - Values > 2^128: hex encoding (OWS requirement for large uint256)
    # - Other values: string encoding
    U128_MAX = (1 << 128) - 1
    for field_name in uint_fields:
        if field_name in message and isinstance(message[field_name], int):
            val = message[field_name]
            if val > U128_MAX:
                message[field_name] = hex(val)
            else:
                message[field_name] = str(val)

    return json.dumps(data)


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

    # Coerce large ints to strings for OWS compatibility
    typed_data_json = _coerce_typed_data_uints(typed_data_json)

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


def ows_sign_transaction(wallet_name: str, tx_hex: str) -> dict:
    """
    Sign a raw EVM transaction envelope using an OWS wallet.

    OWS computes keccak256 of the unsigned tx bytes and signs the hash;
    callers assemble the final signed transaction (signature + tx envelope)
    and broadcast separately. Use ows_send_transaction() if you want OWS to
    broadcast for you.

    Args:
        wallet_name: Name of the OWS wallet.
        tx_hex: Hex-encoded unsigned EVM transaction bytes (RLP envelope,
            with or without 0x prefix). Supports legacy and EIP-1559 typed txs.

    Returns:
        dict with `signature` (hex string, 65 bytes: r || s || v) and
        `recovery_id` (int).
    """
    import ows

    result = ows.sign_transaction(
        wallet=wallet_name,
        chain="polygon",
        tx_hex=tx_hex,
    )
    return {
        "signature": result["signature"],
        "recovery_id": result["recovery_id"],
    }


def ows_send_transaction(wallet_name: str, tx_hex: str, rpc_url: Optional[str] = None) -> dict:
    """
    Sign and broadcast a raw EVM transaction in one call via OWS.

    Convenience wrapper for the simple case (no custom relay routing).
    For Polymarket flows that require the PolyNode relay, sign with
    ows_sign_transaction() and broadcast through your own path instead.

    Args:
        wallet_name: Name of the OWS wallet.
        tx_hex: Hex-encoded unsigned EVM transaction bytes.
        rpc_url: Optional RPC URL override. Defaults to OWS-configured Polygon RPC.

    Returns:
        dict with `tx_hash` (hex string).
    """
    import ows

    result = ows.sign_and_send(
        wallet=wallet_name,
        chain="polygon",
        tx_hex=tx_hex,
        rpc_url=rpc_url,
    )
    return {"tx_hash": result["tx_hash"]}


def ows_sign_typed_tx(wallet_name: str, tx_fields: dict) -> str:
    """
    Sign an EIP-1559 (type 2) transaction via OWS, returning the broadcast-ready hex.

    The SDK already builds tx_fields dicts (to/data/value/chainId/nonce/gas/maxFeePerGas/
    maxPriorityFeePerGas/type=2) for both approvals and redeem paths. This helper takes
    that same dict and returns a fully signed RLP-encoded envelope, ready to broadcast
    via Simmer's relay (`/api/sdk/wallet/broadcast-tx`) — same shape that
    `eth_account.Account.sign_transaction(...).raw_transaction` produces, but signed
    by OWS instead of a raw private key.

    Implementation: signs with a throwaway eth_account key to get the RLP structure,
    extracts the unsigned envelope bytes, asks OWS to sign them, then re-assembles
    the signed envelope with OWS's signature. Avoids depending on eth_account internals.

    Args:
        wallet_name: OWS wallet name.
        tx_fields: Standard eth_account transaction dict with type=2.

    Returns:
        Hex-encoded signed transaction envelope (0x-prefixed), ready to broadcast.

    Raises:
        ValueError: If the tx is not EIP-1559 or OWS returns an unexpected signature shape.
    """
    import rlp
    from eth_account import Account

    # Step 1: produce the canonical RLP envelope structure via a throwaway sign.
    dummy_acc = Account.create()
    signed_dummy = Account.sign_transaction(tx_fields, dummy_acc.key)
    raw_dummy = bytes(signed_dummy.raw_transaction)

    if not raw_dummy or raw_dummy[0] != 0x02:
        raise ValueError(
            f"ows_sign_typed_tx only supports EIP-1559 (type 2) transactions; "
            f"got envelope prefix {hex(raw_dummy[0]) if raw_dummy else 'empty'}"
        )

    decoded = rlp.decode(raw_dummy[1:])
    if len(decoded) != 12:
        raise ValueError(
            f"Unexpected RLP field count {len(decoded)} for signed EIP-1559 (expected 12)"
        )

    # Step 2: build the unsigned envelope (first 9 fields, 0x02 prefix).
    unsigned_fields = list(decoded[:9])
    unsigned_envelope = b"\x02" + rlp.encode(unsigned_fields)

    # Step 3: ask OWS to sign.
    sig_result = ows_sign_transaction(wallet_name, "0x" + unsigned_envelope.hex())
    sig_hex = sig_result["signature"]
    if sig_hex.startswith("0x"):
        sig_hex = sig_hex[2:]
    sig_bytes = bytes.fromhex(sig_hex)
    if len(sig_bytes) != 65:
        raise ValueError(
            f"OWS returned {len(sig_bytes)}-byte signature, expected 65 (r || s || v)"
        )

    # Step 4: re-assemble with OWS signature.
    # RLP canonical encoding requires stripping leading zero bytes from integers
    # (v, r, s). Polygon RPC rejects non-canonical txs with:
    #   "rlp: non-canonical integer (leading zero bytes) for *big.Int"
    # OWS returns 32-byte fixed-width r/s; ~1-in-256-per-byte chance the high
    # byte is zero, which in practice means several percent of broadcasts fail
    # without this lstrip (caught by live test 2026-04-17).
    v_byte = sig_bytes[64]
    v_field = bytes([v_byte]) if v_byte != 0 else b""
    r_field = sig_bytes[:32].lstrip(b"\x00")
    s_field = sig_bytes[32:64].lstrip(b"\x00")

    signed_envelope = b"\x02" + rlp.encode(unsigned_fields + [v_field, r_field, s_field])
    return "0x" + signed_envelope.hex()


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


def _clob_l2_headers(creds: ClobApiCreds, address: str, method: str, path: str, body: str = "") -> dict:
    """Build Polymarket CLOB Level 2 (HMAC) auth headers."""
    from datetime import datetime
    from py_clob_client.signing.hmac import build_hmac_signature

    timestamp = int(datetime.now().timestamp())
    hmac_sig = build_hmac_signature(creds.api_secret, timestamp, method, path, body)

    return {
        "POLY_ADDRESS": address,
        "POLY_SIGNATURE": hmac_sig,
        "POLY_TIMESTAMP": str(timestamp),
        "POLY_API_KEY": creds.api_key,
        "POLY_PASSPHRASE": creds.api_passphrase,
    }


def ows_cancel_order(wallet_name: str, order_id: str) -> dict:
    """Cancel a single CLOB order using OWS-derived credentials."""
    import requests as req

    address = get_ows_wallet_address(wallet_name)
    creds = ows_derive_clob_creds(wallet_name)
    headers = _clob_l2_headers(creds, address, "DELETE", f"/order/{order_id}")

    resp = req.delete(f"{CLOB_HOST}/order/{order_id}", headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()


def ows_cancel_all_orders(wallet_name: str) -> dict:
    """Cancel all CLOB orders using OWS-derived credentials."""
    import requests as req

    address = get_ows_wallet_address(wallet_name)
    creds = ows_derive_clob_creds(wallet_name)
    body = "{}"
    headers = _clob_l2_headers(creds, address, "DELETE", "/cancel-all", body)
    headers["Content-Type"] = "application/json"

    resp = req.delete(f"{CLOB_HOST}/cancel-all", headers=headers, data=body, timeout=10)
    resp.raise_for_status()
    return resp.json()
