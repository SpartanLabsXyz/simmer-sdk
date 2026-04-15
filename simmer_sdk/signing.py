"""
Polymarket Order Signing Utilities

Signs orders locally for external wallet trading.
Uses py_order_utils for Polymarket CLOB order construction.

SECURITY NOTE: The private key should NEVER be logged, transmitted, or stored
outside of memory. It is only used for signing operations.
"""

from typing import Dict, Any
from dataclasses import dataclass

# Polymarket token/USDC decimals (1 share = 1e6 raw units, 1 USDC = 1e6 raw units)
POLYMARKET_DECIMAL_FACTOR = 1e6

# Minimum order size (Polymarket requires >= 5 shares)
MIN_ORDER_SIZE_SHARES = 5

# Polygon mainnet chain ID
POLYGON_CHAIN_ID = 137

# Zero address for open orders (anyone can fill)
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@dataclass
class SignedOrder:
    """A signed Polymarket order ready for submission."""
    salt: str
    maker: str
    signer: str
    taker: str
    tokenId: str
    makerAmount: str
    takerAmount: str
    expiration: str
    nonce: str
    feeRateBps: str
    side: str  # "BUY" or "SELL"
    signatureType: int
    signature: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API submission."""
        return {
            "salt": self.salt,
            "maker": self.maker,
            "signer": self.signer,
            "taker": self.taker,
            "tokenId": self.tokenId,
            "makerAmount": self.makerAmount,
            "takerAmount": self.takerAmount,
            "expiration": self.expiration,
            "nonce": self.nonce,
            "feeRateBps": self.feeRateBps,
            "side": self.side,
            "signatureType": self.signatureType,
            "signature": self.signature,
        }


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

    Returns:
        SignedOrder ready for API submission

    Raises:
        ImportError: If py_order_utils is not installed
        ValueError: If order parameters are invalid
    """
    try:
        from py_order_utils.builders import OrderBuilder
        from py_order_utils.signer import Signer
        from py_order_utils.model import OrderData, EOA, POLY_PROXY, POLY_GNOSIS_SAFE as GNOSIS_SAFE, BUY, SELL
        from py_clob_client.config import get_contract_config
        from py_clob_client.order_builder.builder import OrderBuilder as ClobOrderBuilder, ROUNDING_CONFIG
    except ImportError:
        raise ImportError(
            "py_order_utils and py_clob_client are required for local signing. "
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
