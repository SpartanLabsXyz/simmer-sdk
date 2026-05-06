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

import os
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
    signature_type: int = 0,  # 0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE, 3=POLY_1271 (V2 only)
    tick_size: float = 0.01,
    fee_rate_bps: int = 0,
    order_type: str = "FAK",  # "FAK", "FOK", "GTC", "GTD"
    builder_code: Optional[str] = None,
    metadata: Optional[str] = None,
    amount_usdc: Optional[float] = None,
    deposit_wallet_address: Optional[str] = None,
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
        amount_usdc: V2 FAK/FOK BUY only. Original USDC dollar amount. If
            provided, the V2 path routes through `build_market_order` with
            this amount as maker (CLOB requires maker max 2 dec on FAK/FOK).
            If None, the V2 path derives it from `size * price`. Ignored
            for SELL (uses `size` as shares) and for GTC/GTD (uses `size`).
            **OWS path scope:** `build_and_sign_order_ows` is still V1-only
            and does not accept this kwarg. OWS BYOW users on V2-default
            configs hit a different rejection (V1-shape order at V2 CLOB)
            that this fix does not address; tracked separately in
            `_dev/active/_wallet-custody-migration/`.

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
            amount_usdc=amount_usdc,
            deposit_wallet_address=deposit_wallet_address,
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
    amount_usdc: Optional[float] = None,
    deposit_wallet_address: Optional[str] = None,
) -> SignedOrder:
    """V2 signing path. Uses `py_clob_client_v2.OrderBuilder` directly.

    For FAK/FOK orders we route through ``build_market_order`` with
    ``MarketOrderArgsV2`` (canonical pattern per Polymarket V2 docs):
    BUY ``amount`` is USDC, SELL ``amount`` is shares. The library's
    ``get_market_order_amounts`` rounds maker (USDC for BUY, shares for
    SELL) down to ``round_config.size=2`` decimals, satisfying the
    CLOB's "FAK/FOK maker max 2 decimals" rule for all tick sizes.

    For GTC/GTD orders we use ``build_order`` with ``OrderArgsV2``,
    where the library's ``get_order_amounts`` preserves full precision —
    GTC/GTD must satisfy ``price × size = amount`` exactly, so we never
    re-round.

    Bypassing ``ClobClient.create_order/create_market_order`` avoids
    network calls (`get_tick_size`, `get_version`, `get_clob_market_info`)
    that the high-level helpers make on every order. We sign locally and
    let `local_dev_server` route the signed order to CLOB.

    V2 drops taker/nonce/feeRateBps from the signed struct and adds
    timestamp/metadata/builder. The HTTP POST body keeps `expiration`
    at "0" (not part of signed hash). See docs.simmer.markets/v2-migration.

    See ``_dev/active/_polymarket-rounding-precision/HISTORY.md`` for
    the full rationale: V1 used post-hoc maker rounding, but V2's
    ``OrderArgsV2`` path produces sub-cent maker on tick=0.01 BUYs at
    most prices and on tick=0.001 BUYs at virtually all prices — the
    canonical fix is the market-order builder, not post-hoc rounding.
    """
    import os
    try:
        from py_clob_client_v2.order_builder.builder import OrderBuilder, ROUNDING_CONFIG
        from py_clob_client_v2.clob_types import (
            OrderArgsV2,
            MarketOrderArgsV2,
            CreateOrderOptions,
            OrderType,
        )
        from py_clob_client_v2.signer import Signer
        from py_clob_client_v2.order_utils import SignatureTypeV2
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
    if signature_type not in (0, 3):
        raise ValueError(
            f"V2 signing supports signature_type=0 (EOA) or 3 (POLY_1271 / "
            f"deposit-wallet). Got {signature_type}. For Safe/Proxy wallets, "
            f"use the Simmer dashboard Migrate flow to upgrade to a deposit "
            f"wallet, then this SDK signs sig type 3 automatically."
        )

    # POLY_1271 path — deposit-wallet user. Delegates to polynode for the
    # ERC-7739 TypedDataSign wrapping; we don't hand-roll it here.
    if signature_type == 3:
        if not deposit_wallet_address:
            raise ValueError(
                "signature_type=3 (POLY_1271) requires deposit_wallet_address. "
                "The deposit wallet is the maker/funder; the EOA stays the "
                "signer. Pass the address from your /api/sdk/settings response "
                "(`deposit_wallet_address` field)."
            )
        return _build_and_sign_order_v2_dw(
            private_key=private_key,
            eoa_address=wallet_address,
            deposit_wallet_address=deposit_wallet_address,
            token_id=token_id,
            side=side,
            price=price,
            size=size,
            neg_risk=neg_risk,
            tick_size=tick_size,
            order_type=order_type,
            builder_code=builder_code,
            metadata=metadata,
            amount_usdc=amount_usdc,
        )
    if order_type not in ("FAK", "FOK", "GTC", "GTD"):
        raise ValueError(
            f"Invalid order_type '{order_type}'. Must be FAK, FOK, GTC, or GTD."
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

    tick_size_str = str(tick_size)
    if tick_size_str not in ROUNDING_CONFIG:
        # Safe fallback to most common tick. Mirrors V1 behavior.
        tick_size_str = "0.01"
    options = CreateOrderOptions(tick_size=tick_size_str, neg_risk=neg_risk)

    signer = Signer(private_key=private_key, chain_id=POLYGON_CHAIN_ID)
    order_builder = OrderBuilder(
        signer=signer,
        signature_type=SignatureTypeV2.EOA,
        funder=wallet_address,
    )

    is_market = order_type in ("FAK", "FOK")
    if is_market:
        # FAK/FOK: amount in USDC for BUY, shares for SELL.
        # For BUY, prefer caller-provided USDC amount; fall back to size*price
        # (lossy: round_down inside helper may shave a cent due to float drift).
        if side == "BUY":
            market_amount = (
                float(amount_usdc) if amount_usdc is not None else float(size) * float(price)
            )
        else:
            market_amount = float(size)
        market_args = MarketOrderArgsV2(
            token_id=token_id,
            amount=market_amount,
            side=side.upper(),
            price=price,
            order_type=getattr(OrderType, order_type),
            builder_code=builder_code,
            metadata=metadata,
        )
        signed = order_builder.build_market_order(market_args, options, version=2)
    else:
        # GTC/GTD: size in shares; library preserves price × size = amount.
        order_args = OrderArgsV2(
            token_id=token_id,
            price=price,
            size=size,
            side=side.upper(),
            expiration=expiration_seconds,
            builder_code=builder_code,
            metadata=metadata,
        )
        signed = order_builder.build_order(order_args, options, version=2)

    if hasattr(signed, "dict"):
        order_dict = signed.dict()
    elif hasattr(signed, "model_dump"):
        order_dict = signed.model_dump()
    else:
        import dataclasses
        order_dict = dataclasses.asdict(signed)

    # Minimum order size check (after library-computed amounts)
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


# Solady ERC-7739 TypedDataSign wrap constants — used by deposit-wallet
# (POLY_1271) signing. Mirrors the canonical implementation in
# `simmer/simmer_v3/polymarket_v2_signing.py`. Verified against the working
# on-chain trade
# 0x05bd47c5248ee082d77e99288d95b1ed416c2dc8aca7ac6b11ec45e05cfe6d47
# (decoded 2026-05-05): all deterministic parts match byte-for-byte.
_ORDER_TYPE_STRING = (
    b"Order(uint256 salt,address maker,address signer,uint256 tokenId,"
    b"uint256 makerAmount,uint256 takerAmount,uint8 side,uint8 signatureType,"
    b"uint256 timestamp,bytes32 metadata,bytes32 builder)"
)
_EIP712_DOMAIN_TYPE = (
    b"EIP712Domain(string name,string version,uint256 chainId,"
    b"address verifyingContract)"
)


def _build_and_sign_order_v2_dw(
    private_key: str,
    eoa_address: str,
    deposit_wallet_address: str,
    token_id: str,
    side: str,
    price: float,
    size: float,
    neg_risk: bool,
    tick_size: float,
    order_type: str,
    builder_code: Optional[str],
    metadata: Optional[str],
    amount_usdc: Optional[float] = None,
) -> SignedOrder:
    """V2 sig-type-3 (POLY_1271) order signing for deposit-wallet users.

    Hand-builds the 317-byte ERC-7739 wrapped signature that Polymarket's
    deposit-wallet contract expects. Mirrors the canonical server-side
    implementation in `simmer/simmer_v3/polymarket_v2_signing.py` (which is
    proven to produce CLOB-accepted orders — canary fill
    `0x67b00feb328a42cb5a98dc471429f2170eba64bc29b1e7131ad712345cdf6e05`).

    Why hand-rolled — three workarounds, all empirically verified:

    1. ``maker == signer == deposit_wallet`` (NOT signer=EOA). The on-chain
       working trade has calldata word[8] (maker) == word[9] (signer) ==
       deposit wallet. The CLOB's "the order signer address has to be the
       address of the API KEY" error is misleading — the actual constraint
       is that maker and signer match the funder for ERC-1271 paths.

    2. ``v ∈ {27, 28}`` un-normalized. Solady's ECDSA ecrecover in the
       deposit-wallet contract returns 0x0 for v ∈ {0, 1}, failing
       isValidSignature. polynode 0.10.3's `create_signed_order_v2`
       normalizes v down to 0/1, so we bypass it. eth_account's
       `sign_typed_data` returns v=27/28 by default — we keep it.

    3. FAK/FOK market-order maker rounding. CLOB enforces max 2 decimals
       on the USD-side amount; for BUY that's maker (USDC). compute_amounts
       gives full 6dp precision, so we Decimal-quantize amount_usd to cents
       before computing maker, and floor the resulting taker (shares
       received) at tick-derived precision so effective bid (maker/taker)
       ≥ requested price. Mirrors py_clob_client_v2's get_market_order_amounts.

    Args:
        private_key: User's external EOA private key (hex). Stays local.
        eoa_address: The EOA address derived from `private_key`. Used to
            verify-derive the signing account; not put on the order itself.
        deposit_wallet_address: User's Polymarket deposit wallet. Goes on
            the order as both `maker` and `signer`.

    Returns:
        SignedOrder with `signatureType=3`, `maker == signer ==
        deposit_wallet`, and a 317-byte ERC-7739-wrapped signature
        (636 hex chars including `0x`).
    """
    try:
        from polynode.trading.eip712 import (  # noqa: WPS433
            compute_amounts,
            build_order_payload_v2,
        )
    except ImportError:
        raise ImportError(
            "polynode>=0.10.3 is required for POLY_1271 (sig type 3) order "
            "signing (uses compute_amounts + build_order_payload_v2; we "
            "hand-roll the ERC-7739 wrap to work around polynode's v-norm "
            "and FAK/FOK rounding gaps). Install with: "
            "pip install 'polynode>=0.10.3'."
        )
    try:
        from eth_account import Account  # noqa: WPS433
        from eth_abi import encode as abi_encode  # noqa: WPS433
        from eth_utils import keccak  # noqa: WPS433
    except ImportError:
        raise ImportError(
            "eth-account, eth-abi, eth-utils are required for POLY_1271 "
            "signing. They ship with simmer-sdk's existing deps; if you're "
            "seeing this error your install is incomplete — reinstall with "
            "`pip install --upgrade simmer-sdk`."
        )

    side_upper = side.upper()
    if side_upper not in ("BUY", "SELL"):
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
    if not (0 < price < 1):
        raise ValueError(
            f"price {price!r} must be strictly in (0, 1) for prediction "
            f"markets. Got {price}."
        )
    if size <= 0:
        raise ValueError(f"size {size} must be positive")

    account = Account.from_key(private_key)
    if account.address.lower() != eoa_address.lower():
        raise ValueError(
            f"private_key address {account.address} does not match "
            f"wallet_address {eoa_address}. Refusing to sign."
        )

    # Web3 import is heavyweight; only do address checksumming via eth_utils
    # (already a dep) to avoid pulling web3 just for `to_checksum_address`.
    from eth_utils import to_checksum_address  # noqa: WPS433

    funder_checksum = to_checksum_address(deposit_wallet_address)
    tick_str = str(tick_size)
    is_market = order_type in ("FAK", "FOK")

    if is_market and side_upper == "BUY":
        # FAK/FOK BUY: cent-align maker (USDC), floor taker (shares) at tick
        # precision so effective bid (maker/taker) ≥ requested price.
        # See server's polymarket_v2_signing.py for full reasoning chain.
        from decimal import Decimal, ROUND_DOWN  # noqa: WPS433

        _MARKET_AMOUNT_DECIMALS = {
            "0.1": 3, "0.01": 4, "0.001": 5, "0.0001": 6,
        }
        amount_decimals = _MARKET_AMOUNT_DECIMALS.get(tick_str)
        if amount_decimals is None:
            raise ValueError(
                f"Unsupported tick_size for market order: {tick_str!r}. "
                f"Expected one of {list(_MARKET_AMOUNT_DECIMALS)}."
            )
        if price < float(tick_str):
            raise ValueError(
                f"market order price {price} is below tick_size {tick_str}. "
                f"After flooring to tick the effective price would be 0 "
                f"(division-by-zero). Submit at price >= tick."
            )
        # Prefer caller-supplied amount_usdc when present (cleanest path).
        # Otherwise derive from size × price (lossy under floats, fixed up
        # by Decimal quantize below).
        size_dec = Decimal(str(size))
        tick_dec = Decimal(tick_str)
        price_dec = Decimal(str(price)).quantize(tick_dec, rounding=ROUND_DOWN)
        if amount_usdc is not None:
            amount_usd_dec = Decimal(str(amount_usdc)).quantize(Decimal("0.01"))
        else:
            amount_usd_dec = (size_dec * price_dec).quantize(Decimal("0.01"))
        maker_amount = int(amount_usd_dec * 1_000_000)
        # Floor taker at tick-derived precision so we don't undershoot the
        # ask (round-to-nearest can land effective bid below requested).
        taker_quant = Decimal(10) ** -amount_decimals
        size_floored = (amount_usd_dec / price_dec).quantize(
            taker_quant, rounding=ROUND_DOWN
        )
        taker_amount = int(size_floored * 1_000_000)
    elif is_market and side_upper == "SELL":
        # FAK/FOK SELL: floor size to 2dp first (matches V2 SDK's
        # round_down(amount, 2)), then compute_amounts. Decimal-via-str
        # because `2.30 * 100` evaluates to 229.99...7 in IEEE-754.
        from decimal import Decimal, ROUND_DOWN  # noqa: WPS433

        size_floored_f = float(
            Decimal(str(size)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        )
        tick_dec = Decimal(tick_str)
        price_floored = float(
            Decimal(str(price)).quantize(tick_dec, rounding=ROUND_DOWN)
        )
        maker_amount, taker_amount = compute_amounts(
            price=price_floored,
            size=size_floored_f,
            side=side_upper,
            tick_size=tick_str,
        )
    else:
        # GTC / GTD limit — full tick-size precision; compute_amounts
        # rounds price to tick and size to 6dp.
        maker_amount, taker_amount = compute_amounts(
            price=float(price), size=float(size), side=side_upper, tick_size=tick_str
        )

    # POLY_1271 = 3. polynode's SignatureType enum exposes it; build the
    # payload via build_order_payload_v2 with that enum value.
    from polynode.trading import SignatureType  # noqa: WPS433

    payload = build_order_payload_v2(
        maker=funder_checksum,
        signer=funder_checksum,  # CRITICAL: signer == maker == DW for POLY_1271
        token_id=token_id,
        maker_amount=maker_amount,
        taker_amount=taker_amount,
        side=side_upper,
        signature_type=SignatureType.POLY_1271,
        neg_risk=neg_risk,
        metadata=metadata or ZERO_BYTES32,
        builder=builder_code or os.getenv("POLY_BUILDER_CODE", "").strip() or ZERO_BYTES32,
    )

    # ── Step 1: sign the TypedDataSign envelope (NOT the raw Order). ──
    # Solady ERC-7739 nests the user's typed data inside a TypedDataSign
    # struct whose domain is the deposit wallet's contract domain.
    zero_bytes32 = "0x" + "00" * 32
    tds_typed_data = {
        "domain": payload.domain,  # V2 exchange domain (Polymarket CTF Exchange v2)
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "TypedDataSign": [
                {"name": "contents", "type": "Order"},
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
                {"name": "salt", "type": "bytes32"},
            ],
            "Order": payload.types["Order"],
        },
        "primaryType": "TypedDataSign",
        "message": {
            "contents": payload.message,
            "name": "DepositWallet",
            "version": "1",
            "chainId": 137,
            "verifyingContract": funder_checksum,
            "salt": zero_bytes32,
        },
    }
    signed_msg = Account.sign_typed_data(account.key, full_message=tds_typed_data)
    inner_sig_bytes = bytearray(signed_msg.signature)
    if len(inner_sig_bytes) != 65:
        raise RuntimeError(
            f"Expected 65-byte ECDSA signature, got {len(inner_sig_bytes)}"
        )
    # Do NOT normalize v: Solady's ecrecover in the deposit-wallet contract
    # returns 0x0 for v ∈ {0, 1}. Working on-chain trades have v=27/28.
    # eth_account returns v=27/28 by default — keep it.

    # ── Step 2: appDomainSeparator = keccak(EIP712Domain hash || ...) ──
    domain_addr = to_checksum_address(payload.domain["verifyingContract"])
    app_dom_sep = keccak(
        abi_encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [
                keccak(_EIP712_DOMAIN_TYPE),
                keccak(payload.domain["name"].encode()),
                keccak(payload.domain["version"].encode()),
                int(payload.domain["chainId"]),
                domain_addr,
            ],
        )
    )

    # ── Step 3: contentsHash = keccak(Order type hash || encoded fields) ──
    msg = payload.message
    contents_hash = keccak(
        abi_encode(
            [
                "bytes32", "uint256", "address", "address", "uint256",
                "uint256", "uint256", "uint8", "uint8", "uint256",
                "bytes32", "bytes32",
            ],
            [
                keccak(_ORDER_TYPE_STRING),
                int(msg["salt"]),
                to_checksum_address(msg["maker"]),
                to_checksum_address(msg["signer"]),
                int(msg["tokenId"]),
                int(msg["makerAmount"]),
                int(msg["takerAmount"]),
                int(msg["side"]),
                int(msg["signatureType"]),
                int(msg["timestamp"]),
                bytes.fromhex(msg["metadata"][2:]),
                bytes.fromhex(msg["builder"][2:]),
            ],
        )
    )

    # ── Step 4: assemble the 317-byte wrap. ──
    # innerSig(65) || appDomSep(32) || contentsHash(32) || typeStr(186) || lenBytes(2)
    type_len = len(_ORDER_TYPE_STRING)
    wrapped = bytearray()
    wrapped.extend(inner_sig_bytes)
    wrapped.extend(app_dom_sep)
    wrapped.extend(contents_hash)
    wrapped.extend(_ORDER_TYPE_STRING)
    wrapped.append((type_len >> 8) & 0xFF)
    wrapped.append(type_len & 0xFF)
    expected_len = 65 + 32 + 32 + type_len + 2
    if len(wrapped) != expected_len:
        raise RuntimeError(
            f"ERC-7739 wrap length {len(wrapped)} != expected {expected_len}"
        )
    sig_hex = "0x" + bytes(wrapped).hex()

    return SignedOrder(
        salt=str(msg["salt"]),
        maker=funder_checksum,
        signer=funder_checksum,  # = maker for POLY_1271
        tokenId=str(msg["tokenId"]),
        makerAmount=str(maker_amount),
        takerAmount=str(taker_amount),
        side=side_upper,
        signatureType=3,
        signature=sig_hex,
        timestamp=str(msg["timestamp"]),
        metadata=msg["metadata"],
        builder=msg["builder"],
        expiration="0",
        exchange_version="v2",
    )
