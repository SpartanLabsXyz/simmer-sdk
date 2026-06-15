"""
Polymarket Combo (RFQ) order signing.

Combos settle through a **separate** on-chain exchange (Exchange v3,
``0xe3333700cA9d93003F00f0F71f8515005F6c00Aa``, EIP-712 domain version "3")
from the normal CLOB V2 exchange (version "2"). The taker order struct is the
same 11-field ``Order``, but signed under the v3 domain.

Two signer variants, by wallet cohort:

- **EOA** (``signature_type=0``): a standard EIP-712 signature over the v3
  domain. ``maker == signer == EOA``.
- **Deposit wallet** (``signature_type=3`` / POLY_1271): the 317-byte
  ERC-7739-wrapped ERC-1271 signature, identical in mechanics to the CLOB-V2
  deposit-wallet path in ``signing._build_and_sign_order_v2_dw`` — the ONLY
  difference is the inner Order domain is the combo Exchange v3 domain instead
  of the V2 CLOB exchange domain. ``maker == signer == deposit_wallet``.

The DW identity rule (``signer == maker == DW``) was confirmed by Polymarket
(Shantikiran, 2026-06-15) and empirically reproven against the live requester
gateway 2026-06-16 (RFQ_CREATE ACK + RFQ_QUOTE_READY on test DW 0x4fBd). The
earlier "deposit-wallet combos are rejected" finding was an auth-shape bug on
our side (we had sent signer=EOA, maker=DW), NOT a Polymarket gap.

Unlike the CLOB path, combo maker/taker amounts come straight from the RFQ
quote (``maker_amount_e6`` / ``taker_amount_e6``), so there is no tick-size
rounding here.

SECURITY: the private key is only used locally for signing and is never logged
or transmitted.
"""

import os
import time
import secrets
from typing import Optional

from simmer_sdk.signing import (
    SignedOrder,
    ZERO_BYTES32,
    _ORDER_TYPE_STRING,
    _EIP712_DOMAIN_TYPE,
)

# ── Combo Exchange v3 (verified on-chain via COMBO_EXCHANGE.eip712Domain()
#    and by recovering a captured order's signer — gist §1, 2026-06-13). ──
COMBO_EXCHANGE_V3 = "0xe3333700cA9d93003F00f0F71f8515005F6c00Aa"
COMBO_DOMAIN_NAME = "Polymarket CTF Exchange"
COMBO_DOMAIN_VERSION = "3"

# EIP-712 Order type for combos (identical 11-field struct to CLOB v2).
COMBO_ORDER_TYPES = {
    "Order": [
        {"name": "salt", "type": "uint256"},
        {"name": "maker", "type": "address"},
        {"name": "signer", "type": "address"},
        {"name": "tokenId", "type": "uint256"},
        {"name": "makerAmount", "type": "uint256"},
        {"name": "takerAmount", "type": "uint256"},
        {"name": "side", "type": "uint8"},
        {"name": "signatureType", "type": "uint8"},
        {"name": "timestamp", "type": "uint256"},
        {"name": "metadata", "type": "bytes32"},
        {"name": "builder", "type": "bytes32"},
    ],
}

_SIDE_INT = {"BUY": 0, "SELL": 1}

# Combo order `timestamp` units — see _combo_timestamp(). The gist's verified
# browser impl used ms (Date.now()); Polymarket's maker docs say Unix seconds.
# Default to seconds (docs-authoritative). Override with env if a live fill
# rejects on a timestamp/order error (the morning runbook flags this as the
# first knob to flip).
_TIMESTAMP_UNIT = os.getenv("SIMMER_COMBO_TIMESTAMP_UNIT", "seconds").strip().lower()


def combo_domain() -> dict:
    """The EIP-712 domain for combo taker orders (Exchange v3)."""
    return {
        "name": COMBO_DOMAIN_NAME,
        "version": COMBO_DOMAIN_VERSION,
        "chainId": 137,
        "verifyingContract": COMBO_EXCHANGE_V3,
    }


def _combo_timestamp() -> int:
    """Order timestamp. Seconds by default; ms if SIMMER_COMBO_TIMESTAMP_UNIT=ms."""
    if _TIMESTAMP_UNIT in ("ms", "millis", "milliseconds"):
        return int(time.time() * 1000)
    return int(time.time())


def _gen_salt() -> int:
    """Random 80-bit salt (matches the gist's 10-byte crypto.getRandomValues)."""
    return secrets.randbits(80)


def _normalize_bytes32(value: Optional[str]) -> str:
    v = value or ZERO_BYTES32
    if not v.startswith("0x"):
        v = "0x" + v
    return v


def _normalize_builder(builder_code: Optional[str]) -> str:
    v = builder_code or os.getenv("POLY_BUILDER_CODE", "").strip() or ZERO_BYTES32
    if not v.startswith("0x"):
        v = "0x" + v
    return v


def _build_order_message(
    *,
    maker: str,
    signer: str,
    token_id: str,
    maker_amount: int,
    taker_amount: int,
    side: str,
    signature_type: int,
    metadata: str,
    builder: str,
    salt: Optional[int] = None,
    timestamp: Optional[int] = None,
) -> dict:
    """Assemble the 11-field combo Order message (EIP-712 `Order` struct)."""
    side_upper = side.upper()
    if side_upper not in _SIDE_INT:
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
    if int(maker_amount) <= 0 or int(taker_amount) <= 0:
        raise ValueError(
            f"maker_amount ({maker_amount}) and taker_amount ({taker_amount}) "
            f"must be positive base-e6 integers from the RFQ quote"
        )
    return {
        "salt": salt if salt is not None else _gen_salt(),
        "maker": maker,
        "signer": signer,
        "tokenId": str(token_id),
        "makerAmount": int(maker_amount),
        "takerAmount": int(taker_amount),
        "side": _SIDE_INT[side_upper],
        "signatureType": int(signature_type),
        "timestamp": timestamp if timestamp is not None else _combo_timestamp(),
        "metadata": _normalize_bytes32(metadata),
        "builder": _normalize_builder(builder),
    }


def _signed_order_from_message(msg: dict, *, side: str, signature: str) -> SignedOrder:
    """Wire shape for RFQ_ACCEPT.signed_order — big numbers as strings, side as
    the BUY/SELL label (the WS client maps it back to int at send time), with
    signatureType kept as int and expiration='0'."""
    return SignedOrder(
        salt=str(msg["salt"]),
        maker=msg["maker"],
        signer=msg["signer"],
        tokenId=str(msg["tokenId"]),
        makerAmount=str(msg["makerAmount"]),
        takerAmount=str(msg["takerAmount"]),
        side=side.upper(),
        signatureType=int(msg["signatureType"]),
        signature=signature,
        timestamp=str(msg["timestamp"]),
        metadata=msg["metadata"],
        builder=msg["builder"],
        expiration="0",
        exchange_version="v3",
    )


def build_and_sign_combo_order_eoa(
    private_key: str,
    eoa_address: str,
    token_id: str,
    side: str,
    maker_amount: int,
    taker_amount: int,
    builder_code: Optional[str] = None,
    metadata: Optional[str] = None,
    salt: Optional[int] = None,
    timestamp: Optional[int] = None,
) -> SignedOrder:
    """Sign a combo taker order for an EOA (signature_type=0).

    Standard EIP-712 signature over the combo Exchange v3 domain;
    ``maker == signer == EOA``. Amounts come from the RFQ quote.
    """
    from eth_account import Account
    from eth_utils import to_checksum_address

    account = Account.from_key(private_key)
    if account.address.lower() != eoa_address.lower():
        raise ValueError(
            f"private_key address {account.address} != eoa_address {eoa_address}. "
            f"Refusing to sign."
        )
    eoa = to_checksum_address(eoa_address)
    msg = _build_order_message(
        maker=eoa, signer=eoa, token_id=token_id,
        maker_amount=maker_amount, taker_amount=taker_amount, side=side,
        signature_type=0, metadata=metadata, builder=builder_code,
        salt=salt, timestamp=timestamp,
    )
    typed_data = {
        "domain": combo_domain(),
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            **COMBO_ORDER_TYPES,
        },
        "primaryType": "Order",
        "message": msg,
    }
    signed = Account.sign_typed_data(account.key, full_message=typed_data)
    sig_hex = signed.signature.hex()
    if not sig_hex.startswith("0x"):
        sig_hex = "0x" + sig_hex
    return _signed_order_from_message(msg, side=side, signature=sig_hex)


def build_and_sign_combo_order_dw(
    private_key: str,
    eoa_address: str,
    deposit_wallet_address: str,
    token_id: str,
    side: str,
    maker_amount: int,
    taker_amount: int,
    builder_code: Optional[str] = None,
    metadata: Optional[str] = None,
    salt: Optional[int] = None,
    timestamp: Optional[int] = None,
) -> SignedOrder:
    """Sign a combo taker order for a deposit wallet (signature_type=3 / POLY_1271).

    Produces the 317-byte ERC-7739-wrapped ERC-1271 signature — identical wrap
    mechanics to ``signing._build_and_sign_order_v2_dw`` but with the inner
    Order domain set to the combo Exchange v3 domain. ``maker == signer == DW``.
    The inner ECDSA signature is produced by the owner EOA's key; the deposit
    wallet's ``isValidSignature`` ecrecovers the EOA and matches it to the
    wallet owner.
    """
    from eth_account import Account
    from eth_abi import encode as abi_encode
    from eth_utils import keccak, to_checksum_address

    account = Account.from_key(private_key)
    if account.address.lower() != eoa_address.lower():
        raise ValueError(
            f"private_key address {account.address} != eoa_address {eoa_address}. "
            f"Refusing to sign."
        )
    dw = to_checksum_address(deposit_wallet_address)
    domain = combo_domain()
    msg = _build_order_message(
        maker=dw, signer=dw, token_id=token_id,
        maker_amount=maker_amount, taker_amount=taker_amount, side=side,
        signature_type=3, metadata=metadata, builder=builder_code,
        salt=salt, timestamp=timestamp,
    )

    # ── Step 1: sign the TypedDataSign envelope (NOT the raw Order). ──
    # Solady ERC-7739 nests the Order inside a TypedDataSign struct whose
    # domain is the combo exchange (v3); the outer wrapper names the deposit
    # wallet contract domain (name "DepositWallet" v1, verifyingContract=DW).
    zero_bytes32 = "0x" + "00" * 32
    tds_typed_data = {
        "domain": domain,  # combo Exchange v3 domain
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
            **COMBO_ORDER_TYPES,
        },
        "primaryType": "TypedDataSign",
        "message": {
            "contents": msg,
            "name": "DepositWallet",
            "version": "1",
            "chainId": 137,
            "verifyingContract": dw,
            "salt": zero_bytes32,
        },
    }
    signed_msg = Account.sign_typed_data(account.key, full_message=tds_typed_data)
    inner_sig_bytes = bytearray(signed_msg.signature)
    if len(inner_sig_bytes) != 65:
        raise RuntimeError(f"Expected 65-byte ECDSA signature, got {len(inner_sig_bytes)}")
    # Do NOT normalize v: Solady's ecrecover returns 0x0 for v ∈ {0,1}.

    # ── Step 2: appDomainSeparator over the combo v3 domain. ──
    app_dom_sep = keccak(
        abi_encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [
                keccak(_EIP712_DOMAIN_TYPE),
                keccak(domain["name"].encode()),
                keccak(domain["version"].encode()),
                int(domain["chainId"]),
                to_checksum_address(domain["verifyingContract"]),
            ],
        )
    )

    # ── Step 3: contentsHash over the Order struct. ──
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
        raise RuntimeError(f"ERC-7739 wrap length {len(wrapped)} != expected {expected_len}")
    sig_hex = "0x" + bytes(wrapped).hex()

    return _signed_order_from_message(msg, side=side, signature=sig_hex)
