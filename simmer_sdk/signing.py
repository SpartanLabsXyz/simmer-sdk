"""
Polymarket Order Signing Utilities

Signs orders locally for external wallet trading.
Uses py_order_utils for Polymarket CLOB order construction.

SECURITY NOTE: The private key should NEVER be logged, transmitted, or stored
outside of memory. It is only used for signing operations.
"""

import math
from typing import Dict, Any
from dataclasses import dataclass

# Polymarket token/USDC decimals (1 share = 1e6 raw units, 1 USDC = 1e6 raw units)
POLYMARKET_DECIMAL_FACTOR = 1e6

# Minimum order size (Polymarket requires >= 5 shares)
MIN_ORDER_SIZE_SHARES = 5

# Polygon mainnet chain ID
POLYGON_CHAIN_ID = 137

# Precision factors for Polymarket CLOB
# Shares: max 4 decimal places in 1e6 factor = round to multiple of 100
SHARES_PRECISION_FACTOR = 100

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

    # Fix decimal precision for Polymarket CLOB orders
    rounded_price = round(price, 4)
    rounded_size = math.floor(size * SHARES_PRECISION_FACTOR) / SHARES_PRECISION_FACTOR  # Floor to 2 decimals

    # Calculate raw amounts with proper precision
    shares_raw = int(rounded_size * POLYMARKET_DECIMAL_FACTOR)
    shares_raw = (shares_raw // SHARES_PRECISION_FACTOR) * SHARES_PRECISION_FACTOR  # 4 decimal precision

    # USDC raw = shares × price in raw units, floored to tick precision
    # Dome validates exact consistency: makerAmount = floor(size * price * 1e6 / 10) * 10
    # Previous 2-decimal rounding (//10000) caused rejection on fractional prices (e.g., 0.2636)
    usdc_raw = int(rounded_price * rounded_size * POLYMARKET_DECIMAL_FACTOR)
    usdc_raw = (usdc_raw // 10) * 10  # Floor to 5 decimal precision (tick-aligned)

    # Check minimum order size
    effective_shares = shares_raw / POLYMARKET_DECIMAL_FACTOR
    if effective_shares < MIN_ORDER_SIZE_SHARES:
        raise ValueError(
            f"Order too small: {effective_shares:.2f} shares after rounding "
            f"is below minimum ({MIN_ORDER_SIZE_SHARES})"
        )

    # Assign maker/taker amounts based on side
    # BUY: maker gives USDC, receives shares (taker)
    # SELL: maker gives shares, receives USDC (taker)
    if side == "BUY":
        maker_raw = usdc_raw
        taker_raw = shares_raw
    else:
        maker_raw = shares_raw
        taker_raw = usdc_raw

    # Map signature type
    sig_type_map = {0: EOA, 1: POLY_PROXY, 2: GNOSIS_SAFE}
    sig_type = sig_type_map.get(signature_type, EOA)

    side_enum = BUY if side == "BUY" else SELL

    # Build OrderData
    data = OrderData(
        maker=wallet_address,
        taker=ZERO_ADDRESS,
        tokenId=token_id,
        makerAmount=str(maker_raw),
        takerAmount=str(taker_raw),
        side=side_enum,
        feeRateBps="0",
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
