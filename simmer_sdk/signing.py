"""
Polymarket Order Signing Utilities

Signs orders locally for external wallet trading.

- V1 (pre-2026-04-28): uses `py_order_utils` — the legacy path.
- V2 (default starting `simmer-sdk 0.10.0`): uses `py_clob_client_v2`
  which produces the V2 order shape (drops taker/nonce/feeRateBps from
  the signed struct; adds timestamp/metadata/builder).

Selected automatically via `SIMMER_POLYMARKET_EXCHANGE_VERSION` env or
the 0.10.0-default-V2 behavior.

SECURITY NOTE: The private key should NEVER be logged, transmitted, or stored
outside of memory. It is only used for signing operations.
"""

from typing import Dict, Any, Optional
from dataclasses import dataclass, field

from simmer_sdk.polymarket_contracts import (
    POLYGON_CHAIN_ID,
    is_v2_enabled,
)

# Polymarket token/USDC decimals (1 share = 1e6 raw units, 1 USDC/pUSD = 1e6 raw units)
POLYMARKET_DECIMAL_FACTOR = 1e6

# Minimum order size (Polymarket requires >= 5 shares)
MIN_ORDER_SIZE_SHARES = 5

# Zero address for open orders (anyone can fill)
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Zero bytes32 — V2 default for metadata / builder when unset
ZERO_BYTES32 = "0x" + "00" * 32


@dataclass
class SignedOrder:
    """A signed Polymarket order ready for submission.

    Supports both V1 and V2 order shapes via optional fields. V1 uses
    `taker` / `nonce` / `feeRateBps` in the signed struct; V2 drops those
    and adds `timestamp` / `metadata` / `builder` instead. `expiration`
    is retained in the HTTP body on both versions (V2 keeps it at "0"
    out of the signed hash).
    """
    # Common to both V1 and V2
    salt: str
    maker: str
    signer: str
    tokenId: str
    makerAmount: str
    takerAmount: str
    side: str  # "BUY" or "SELL"
    signatureType: int
    signature: str

    # V1-only (absent on V2)
    taker: Optional[str] = None
    nonce: Optional[str] = None
    feeRateBps: Optional[str] = None

    # V2-only (absent on V1)
    timestamp: Optional[str] = None
    metadata: Optional[str] = None
    builder: Optional[str] = None

    # Shared: V1 in signed hash; V2 in HTTP body only, at "0"
    expiration: Optional[str] = None

    # Meta: which exchange version this order targets
    exchange_version: str = "v1"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API submission. Omits None fields so
        V1 orders don't carry V2 fields and vice versa."""
        out: Dict[str, Any] = {
            "salt": self.salt,
            "maker": self.maker,
            "signer": self.signer,
            "tokenId": self.tokenId,
            "makerAmount": self.makerAmount,
            "takerAmount": self.takerAmount,
            "side": self.side,
            "signatureType": self.signatureType,
            "signature": self.signature,
        }
        # Include V1 or V2 specific fields based on what's populated
        if self.taker is not None:
            out["taker"] = self.taker
        if self.nonce is not None:
            out["nonce"] = self.nonce
        if self.feeRateBps is not None:
            out["feeRateBps"] = self.feeRateBps
        if self.timestamp is not None:
            out["timestamp"] = self.timestamp
        if self.metadata is not None:
            out["metadata"] = self.metadata
        if self.builder is not None:
            out["builder"] = self.builder
        # expiration lives in HTTP body on both versions; default "0" on V2
        if self.expiration is not None:
            out["expiration"] = self.expiration
        return out


def build_and_sign_order(
    private_key: str,
    wallet_address: str,
    token_id: str,
    side: str,  # "BUY" or "SELL"
    price: float,
    size: float,
    neg_risk: bool = False,
    signature_type: int = 0,  # 0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE
    tick_size: float = 0.01,
    fee_rate_bps: int = 0,
    order_type: str = "FAK",  # "FAK", "FOK", "GTC", "GTD"
    builder_code: Optional[str] = None,
    metadata: Optional[str] = None,
) -> SignedOrder:
    """
    Build and sign a Polymarket order.

    Args:
        private_key: Wallet private key (0x prefixed hex string)
        wallet_address: Wallet address that will sign the order
        token_id: Token ID for the outcome (YES or NO token)
        side: "BUY" or "SELL"
        price: Order price (0-1)
        size: Number of shares to trade
        neg_risk: Whether this is a neg-risk market
        signature_type: Signature type (0=EOA default)
        tick_size: Market tick size (e.g., 0.01 or 0.001)
        fee_rate_bps: V1 only. Ignored on V2 (fees are match-time, not signed).
        builder_code: V2 only. bytes32 hex for builder attribution. Reads env
            `POLY_BUILDER_CODE` if None; defaults to zero bytes32 if unset.
            Mint yours at polymarket.com/settings?tab=builder.
        metadata: V2 only. bytes32 hex, default zero bytes32.

    Returns:
        SignedOrder ready for API submission. V1 or V2 shape based on
        `SIMMER_POLYMARKET_EXCHANGE_VERSION` (default V2 on 0.10.0+).

    Raises:
        ImportError: If required signing deps aren't installed
        ValueError: If order parameters are invalid
    """
    if is_v2_enabled():
        return _build_and_sign_order_v2(
            private_key=private_key,
            wallet_address=wallet_address,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            neg_risk=neg_risk,
            signature_type=signature_type,
            tick_size=tick_size,
            order_type=order_type,
            builder_code=builder_code,
            metadata=metadata,
        )
    return _build_and_sign_order_v1(
        private_key=private_key,
        wallet_address=wallet_address,
        token_id=token_id,
        side=side,
        price=price,
        size=size,
        neg_risk=neg_risk,
        signature_type=signature_type,
        tick_size=tick_size,
        fee_rate_bps=fee_rate_bps,
        order_type=order_type,
    )


def _build_and_sign_order_v1(
    private_key: str,
    wallet_address: str,
    token_id: str,
    side: str,
    price: float,
    size: float,
    neg_risk: bool,
    signature_type: int,
    tick_size: float,
    fee_rate_bps: int,
    order_type: str,
) -> SignedOrder:
    """V1 signing path (legacy). Unchanged from pre-0.10.0 behavior."""
    try:
        from py_order_utils.builders import OrderBuilder
        from py_order_utils.signer import Signer
        from py_order_utils.model import OrderData, EOA, POLY_PROXY, POLY_GNOSIS_SAFE as GNOSIS_SAFE, BUY, SELL
        from py_clob_client.config import get_contract_config
        from py_clob_client.order_builder.builder import OrderBuilder as ClobOrderBuilder, ROUNDING_CONFIG
    except ImportError:
        raise ImportError(
            "py_order_utils and py_clob_client are required for V1 local signing. "
            "Install with: pip install py-order-utils py-clob-client"
        )

    # Validate inputs
    if side not in ("BUY", "SELL"):
        raise ValueError(f"Invalid side '{side}'. Must be 'BUY' or 'SELL'")
    if price <= 0 or price >= 1:
        raise ValueError(f"Invalid price {price}. Must be between 0 and 1")
    if size <= 0:
        raise ValueError(f"Invalid size {size}. Must be positive")
    if signature_type not in (0, 1, 2):
        raise ValueError(f"Invalid signature_type {signature_type}. Must be 0, 1, or 2")

    # Use py-clob-client's OrderBuilder for tick_size-aware precision
    # This handles rounding correctly (avoids float truncation bugs like
    # int(0.99 * 5.05 * 1e6) = 4999499 instead of 4999500)
    tick_size_str = str(tick_size)
    if tick_size_str not in ROUNDING_CONFIG:
        tick_size_str = "0.01"  # Safe fallback (most common)
    round_config = ROUNDING_CONFIG[tick_size_str]

    dummy_builder = ClobOrderBuilder.__new__(ClobOrderBuilder)
    side_enum, maker_raw, taker_raw = dummy_builder.get_order_amounts(
        side, size, price, round_config
    )

    # CLOB enforces maker max 2 decimals for FAK/FOK (market orders).
    # GTC/GTD (limit orders) need full precision from get_order_amounts().
    # See _dev/active/_polymarket-rounding-precision/ for full history.
    # Uses py-clob-client's own helpers to avoid float truncation (int() on 2069999.9999 → 2069999).
    from py_clob_client.order_builder.helpers import round_normal as _round_normal, to_token_decimals as _to_token_decimals
    if order_type in ("FAK", "FOK"):
        maker_raw = _to_token_decimals(_round_normal(maker_raw / 1e6, 2))

    # Check minimum order size
    shares_raw = taker_raw if side == "BUY" else maker_raw
    effective_shares = shares_raw / POLYMARKET_DECIMAL_FACTOR
    if effective_shares < MIN_ORDER_SIZE_SHARES:
        raise ValueError(
            f"Order too small: {effective_shares:.2f} shares after rounding "
            f"is below minimum ({MIN_ORDER_SIZE_SHARES})"
        )

    # Map signature type
    sig_type_map = {0: EOA, 1: POLY_PROXY, 2: GNOSIS_SAFE}
    sig_type = sig_type_map.get(signature_type, EOA)

    # Build OrderData
    data = OrderData(
        maker=wallet_address,
        taker=ZERO_ADDRESS,
        tokenId=token_id,
        makerAmount=str(maker_raw),
        takerAmount=str(taker_raw),
        side=side_enum,
        feeRateBps=str(fee_rate_bps),
        nonce="0",
        signer=wallet_address,
        expiration="0",
        signatureType=sig_type,
    )

    # Get contract config and build signer
    contract_config = get_contract_config(POLYGON_CHAIN_ID, neg_risk)
    order_builder = OrderBuilder(
        contract_config.exchange,
        POLYGON_CHAIN_ID,
        Signer(key=private_key),
    )

    # Sign the order
    signed = order_builder.build_signed_order(data)
    order_dict = signed.dict()

    return SignedOrder(
        salt=str(order_dict["salt"]),
        maker=order_dict["maker"],
        signer=order_dict["signer"],
        taker=order_dict["taker"],
        tokenId=order_dict["tokenId"],
        makerAmount=order_dict["makerAmount"],
        takerAmount=order_dict["takerAmount"],
        expiration=order_dict["expiration"],
        nonce=order_dict["nonce"],
        feeRateBps=order_dict["feeRateBps"],
        side=side,
        signatureType=signature_type,
        signature=order_dict["signature"],
        exchange_version="v1",
    )


def _build_and_sign_order_v2(
    private_key: str,
    wallet_address: str,
    token_id: str,
    side: str,
    price: float,
    size: float,
    neg_risk: bool,
    signature_type: int,
    tick_size: float,
    order_type: str,
    builder_code: Optional[str],
    metadata: Optional[str],
) -> SignedOrder:
    """V2 signing path. Uses `py_clob_client_v2`'s ClobClient.create_order().

    V2 drops taker/nonce/feeRateBps from the signed struct and adds
    timestamp/metadata/builder. The HTTP POST body keeps `expiration`
    at "0" (not part of signed hash). See docs.simmer.markets/v2-migration.
    """
    import os
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import OrderArgs, PartialCreateOrderOptions
    except ImportError:
        raise ImportError(
            "py_clob_client_v2 >= 1.0.0 is required for V2 local signing. "
            "Install with: pip install 'py-clob-client-v2>=1.0.0'. "
            "Or pin simmer-sdk<0.10.0 to stay on V1 (V1 CLOB retired 2026-04-28)."
        )

    # Validate inputs
    if side not in ("BUY", "SELL"):
        raise ValueError(f"Invalid side '{side}'. Must be 'BUY' or 'SELL'")
    if price <= 0 or price >= 1:
        raise ValueError(f"Invalid price {price}. Must be between 0 and 1")
    if size <= 0:
        raise ValueError(f"Invalid size {size}. Must be positive")
    if signature_type != 0:
        raise ValueError(
            f"V2 signing only supports signature_type=0 (EOA). "
            f"Got {signature_type}. For Safe/Proxy wallets, use the "
            f"polynode SDK's relayer path or the Simmer dashboard Migrate flow."
        )

    # Resolve builder_code: explicit arg > env > zero bytes32
    if builder_code is None:
        builder_code = os.getenv("POLY_BUILDER_CODE", "").strip() or ZERO_BYTES32
    if not builder_code.startswith("0x"):
        builder_code = "0x" + builder_code
    if metadata is None:
        metadata = ZERO_BYTES32

    # GTC/GTD expiration is a unix-seconds int; FAK/FOK use 0
    expiration_seconds = 0

    clob_host = os.getenv("POLYMARKET_CLOB_HOST", "https://clob.polymarket.com")
    client = ClobClient(
        host=clob_host,
        chain_id=POLYGON_CHAIN_ID,
        key=private_key,
        signature_type=0,
        funder=wallet_address,
    )

    tick_size_str = str(tick_size)
    order_args = OrderArgs(
        token_id=token_id,
        price=price,
        size=size,
        side=side.upper(),
        expiration=expiration_seconds,
        builder_code=builder_code,
        metadata=metadata,
    )
    options = PartialCreateOrderOptions(
        tick_size=tick_size_str,
        neg_risk=neg_risk,
    )
    signed = client.create_order(order_args, options)
    if hasattr(signed, "dict"):
        order_dict = signed.dict()
    elif hasattr(signed, "model_dump"):
        order_dict = signed.model_dump()
    else:
        order_dict = {k: getattr(signed, k) for k in signed.__dataclass_fields__}

    # Minimum order size check (after SDK-computed amounts)
    maker_raw = int(order_dict["makerAmount"])
    taker_raw = int(order_dict["takerAmount"])
    shares_raw = taker_raw if side == "BUY" else maker_raw
    effective_shares = shares_raw / POLYMARKET_DECIMAL_FACTOR
    if effective_shares < MIN_ORDER_SIZE_SHARES:
        raise ValueError(
            f"Order too small: {effective_shares:.2f} shares after rounding "
            f"is below minimum ({MIN_ORDER_SIZE_SHARES})"
        )

    return SignedOrder(
        salt=str(order_dict["salt"]),
        maker=order_dict["maker"],
        signer=order_dict["signer"],
        tokenId=order_dict["tokenId"],
        makerAmount=order_dict["makerAmount"],
        takerAmount=order_dict["takerAmount"],
        side=side,
        signatureType=int(order_dict.get("signatureType", 0)),
        signature=order_dict["signature"],
        # V2 fields
        timestamp=str(order_dict.get("timestamp", "")),
        metadata=str(order_dict.get("metadata", metadata)),
        builder=str(order_dict.get("builder", builder_code)),
        # Expiration stays in HTTP body at "0"
        expiration="0",
        exchange_version="v2",
    )


def build_and_sign_order_ows(
    ows_wallet: str,
    token_id: str,
    side: str,  # "BUY" or "SELL"
    price: float,
    size: float,
    neg_risk: bool = False,
    signature_type: int = 0,  # 0=EOA
    tick_size: float = 0.01,
    fee_rate_bps: int = 0,
    order_type: str = "FAK",
) -> SignedOrder:
    """
    Build and sign a Polymarket order using an OWS wallet.

    Same as build_and_sign_order() but signs via OWS instead of a raw
    private key. The private key never leaves the OWS vault.

    Args:
        ows_wallet: Name of the OWS wallet to sign with.
        token_id: Token ID for the outcome (YES or NO token).
        side: "BUY" or "SELL".
        price: Order price (0-1).
        size: Number of shares to trade.
        neg_risk: Whether this is a neg-risk market.
        signature_type: Signature type (0=EOA default).
        tick_size: Market tick size.
        fee_rate_bps: Fee rate in basis points.
        order_type: "FAK", "FOK", "GTC", or "GTD".

    Returns:
        SignedOrder ready for API submission.
    """
    try:
        from py_order_utils.builders import OrderBuilder
        from py_order_utils.signer import Signer
        from py_order_utils.model import OrderData, EOA, POLY_PROXY, POLY_GNOSIS_SAFE as GNOSIS_SAFE, BUY, SELL
        from py_clob_client.config import get_contract_config
        from py_clob_client.order_builder.builder import OrderBuilder as ClobOrderBuilder, ROUNDING_CONFIG
        from poly_eip712_structs import make_domain
    except ImportError:
        raise ImportError(
            "py_order_utils, py_clob_client, and poly_eip712_structs are required. "
            "Install with: pip install py-order-utils py-clob-client"
        )

    from simmer_sdk.ows_utils import get_ows_wallet_address, ows_sign_typed_data

    # Validate inputs
    if side not in ("BUY", "SELL"):
        raise ValueError(f"Invalid side '{side}'. Must be 'BUY' or 'SELL'")
    if price <= 0 or price >= 1:
        raise ValueError(f"Invalid price {price}. Must be between 0 and 1")
    if size <= 0:
        raise ValueError(f"Invalid size {size}. Must be positive")
    if signature_type not in (0, 1, 2):
        raise ValueError(f"Invalid signature_type {signature_type}. Must be 0, 1, or 2")

    # Get wallet address from OWS
    wallet_address = get_ows_wallet_address(ows_wallet)

    # Calculate order amounts (same logic as build_and_sign_order)
    tick_size_str = str(tick_size)
    if tick_size_str not in ROUNDING_CONFIG:
        tick_size_str = "0.01"
    round_config = ROUNDING_CONFIG[tick_size_str]

    dummy_builder = ClobOrderBuilder.__new__(ClobOrderBuilder)
    side_enum, maker_raw, taker_raw = dummy_builder.get_order_amounts(
        side, size, price, round_config
    )

    from py_clob_client.order_builder.helpers import round_normal as _round_normal, to_token_decimals as _to_token_decimals
    if order_type in ("FAK", "FOK"):
        maker_raw = _to_token_decimals(_round_normal(maker_raw / 1e6, 2))

    # Check minimum order size
    shares_raw = taker_raw if side == "BUY" else maker_raw
    effective_shares = shares_raw / POLYMARKET_DECIMAL_FACTOR
    if effective_shares < MIN_ORDER_SIZE_SHARES:
        raise ValueError(
            f"Order too small: {effective_shares:.2f} shares after rounding "
            f"is below minimum ({MIN_ORDER_SIZE_SHARES})"
        )

    # Map signature type
    sig_type_map = {0: EOA, 1: POLY_PROXY, 2: GNOSIS_SAFE}
    sig_type = sig_type_map.get(signature_type, EOA)

    # Build unsigned order — need a dummy signer for py_order_utils
    # (it requires a Signer to construct the order struct)
    from eth_account import Account
    dummy_account = Account.create()
    contract_config = get_contract_config(POLYGON_CHAIN_ID, neg_risk)
    order_builder = OrderBuilder(
        contract_config.exchange,
        POLYGON_CHAIN_ID,
        Signer(key=dummy_account.key.hex()),
    )

    data_for_build = OrderData(
        maker=dummy_account.address,
        taker=ZERO_ADDRESS,
        tokenId=token_id,
        makerAmount=str(maker_raw),
        takerAmount=str(taker_raw),
        side=side_enum,
        feeRateBps=str(fee_rate_bps),
        nonce="0",
        signer=dummy_account.address,
        expiration="0",
        signatureType=sig_type,
    )
    order = order_builder.build_order(data_for_build)

    # Replace addresses with real OWS wallet address
    order.values["maker"] = wallet_address
    order.values["signer"] = wallet_address

    # Generate EIP-712 typed data JSON
    domain = make_domain(
        name="Polymarket CTF Exchange",
        version="1",
        chainId=str(POLYGON_CHAIN_ID),
        verifyingContract=contract_config.exchange,
    )
    typed_data_json = order.to_message_json(domain=domain)

    # Sign with OWS — key never leaves the vault
    signature = ows_sign_typed_data(ows_wallet, typed_data_json)

    return SignedOrder(
        salt=str(order.values["salt"]),
        maker=wallet_address,
        signer=wallet_address,
        taker=ZERO_ADDRESS,
        tokenId=token_id,
        makerAmount=str(maker_raw),
        takerAmount=str(taker_raw),
        expiration="0",
        nonce="0",
        feeRateBps=str(fee_rate_bps),
        side=side,
        signatureType=signature_type,
        signature=signature,
    )


def sign_message(private_key: str, message: str) -> str:
    """
    Sign a message with the wallet's private key.

    Used for wallet linking challenge-response.

    Args:
        private_key: Wallet private key (0x prefixed hex string)
        message: Message to sign

    Returns:
        Hex-encoded signature

    Raises:
        ImportError: If eth_account is not installed
    """
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct
    except ImportError:
        raise ImportError(
            "eth_account is required for message signing. "
            "Install with: pip install eth-account"
        )

    message_hash = encode_defunct(text=message)
    signed = Account.sign_message(message_hash, private_key=private_key)
    return signed.signature.hex()


def get_wallet_address(private_key: str) -> str:
    """
    Get the wallet address for a private key.

    Args:
        private_key: Wallet private key (0x prefixed hex string)

    Returns:
        Wallet address (0x prefixed, checksummed)

    Raises:
        ImportError: If eth_account is not installed
    """
    try:
        from eth_account import Account
    except ImportError:
        raise ImportError(
            "eth_account is required for address derivation. "
            "Install with: pip install eth-account"
        )

    account = Account.from_key(private_key)
    return account.address
