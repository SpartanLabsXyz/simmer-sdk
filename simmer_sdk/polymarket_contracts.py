"""
Polymarket contract addresses — V1 and V2.

Mirrors the server-side `simmer_v3/polymarket_contracts.py` so SDK
users that build orders / approvals locally use the same addresses
the Simmer API expects. Callers should use `active_spenders()`,
`collateral_token()`, and `get_active_addresses()` instead of picking
V1 or V2 constants directly.

Exchange version:
- Default starting `simmer-sdk 0.10.0`: **V2** (Polymarket cut over
  on 2026-04-28 ~11:00 UTC). Pre-cutover, pin `simmer-sdk <0.10.0`
  for V1. Post-cutover, upgrade to 0.10.0+ for V2.
- Override via `SIMMER_POLYMARKET_EXCHANGE_VERSION=v1` env var (use
  with care — only applicable if you're testing against the old V1
  CLOB, which stopped accepting orders at cutover).

Sources:
- Polymarket docs: docs.polymarket.com/v2-migration
- PolyNode V2 guide: docs.polynode.dev/guides/v2-migration
- Simmer migration guide: docs.simmer.markets/v2-migration
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# ============================================================
# Chain + shared constants (unchanged V1 → V2)
# ============================================================
POLYGON_CHAIN_ID = 137
CONDITIONAL_TOKENS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # CTF, unchanged
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"   # unchanged


# ============================================================
# V1 (pre-2026-04-28) — retained for back-compat if user pins v1
# ============================================================
V1_CTF_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
V1_NEG_RISK_CTF_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
V1_COLLATERAL_TOKEN = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e
V1_EIP712_ORDER_DOMAIN_VERSION = "1"


# ============================================================
# V2 (primary after 2026-04-28 cutover)
# ============================================================
V2_CTF_EXCHANGE = "0xE111180000d2663C0091e4f400237545B87B996B"
V2_NEG_RISK_EXCHANGE_A = "0xe2222d279d744050d28e00520010520000310F59"
V2_NEG_RISK_EXCHANGE_B = "0xe2222d002000Ba0053CEF3375333610F64600036"  # secondary
V2_COLLATERAL_TOKEN = "0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB"  # pUSD
V2_EIP712_ORDER_DOMAIN_VERSION = "2"


# ============================================================
# V2 collateral infrastructure
# ============================================================
COLLATERAL_ONRAMP = "0x93070a847efEf7F70739046A929D47a521F5B8ee"       # USDC.e → pUSD
COLLATERAL_OFFRAMP = "0x2957922Eb93258b93368531d39fAcCA3B4dC5854"      # pUSD → USDC.e


# ============================================================
# Convenience aliases
# ============================================================
USDC_E = V1_COLLATERAL_TOKEN    # same address, named by role
PUSD = V2_COLLATERAL_TOKEN
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"


@dataclass(frozen=True)
class ActiveAddresses:
    """Addresses the SDK should use for the current exchange version."""
    ctf_exchange: str
    neg_risk_exchange_primary: str
    neg_risk_exchange_secondary: str | None
    neg_risk_adapter: str
    collateral_token: str  # pUSD (V2) or USDC.e (V1)
    eip712_order_domain_version: str
    version: str  # "v1" or "v2"


def is_v2_enabled() -> bool:
    """True when the SDK should build V2-shaped orders / approvals.

    Default: **V2** starting `simmer-sdk 0.10.0`. Override with env
    `SIMMER_POLYMARKET_EXCHANGE_VERSION=v1` to force V1 shape (e.g., for
    testing against the retired V1 CLOB — rare).
    """
    override = os.getenv("SIMMER_POLYMARKET_EXCHANGE_VERSION", "").strip().lower()
    if override == "v1":
        return False
    if override == "v2":
        return True
    # Unset / unrecognized → v2 default on 0.10.0+
    return True


def get_active_addresses() -> ActiveAddresses:
    """Return the active address set based on the exchange version flag."""
    if is_v2_enabled():
        return ActiveAddresses(
            ctf_exchange=V2_CTF_EXCHANGE,
            neg_risk_exchange_primary=V2_NEG_RISK_EXCHANGE_A,
            neg_risk_exchange_secondary=V2_NEG_RISK_EXCHANGE_B,
            neg_risk_adapter=NEG_RISK_ADAPTER,
            collateral_token=V2_COLLATERAL_TOKEN,
            eip712_order_domain_version=V2_EIP712_ORDER_DOMAIN_VERSION,
            version="v2",
        )
    return ActiveAddresses(
        ctf_exchange=V1_CTF_EXCHANGE,
        neg_risk_exchange_primary=V1_NEG_RISK_CTF_EXCHANGE,
        neg_risk_exchange_secondary=None,
        neg_risk_adapter=NEG_RISK_ADAPTER,
        collateral_token=V1_COLLATERAL_TOKEN,
        eip712_order_domain_version=V1_EIP712_ORDER_DOMAIN_VERSION,
        version="v1",
    )


def active_spenders() -> list[str]:
    """Contract addresses that need token allowances for active trading.

    V1: 3 spenders (CTF Exchange, Neg Risk CTF Exchange, Neg Risk Adapter).
    V2: 4 spenders (adds a second Neg Risk Exchange for multi-outcome capacity).
    """
    addrs = get_active_addresses()
    spenders = [
        addrs.ctf_exchange,
        addrs.neg_risk_exchange_primary,
        addrs.neg_risk_adapter,
    ]
    if addrs.neg_risk_exchange_secondary:
        spenders.append(addrs.neg_risk_exchange_secondary)
    return spenders


def collateral_token() -> str:
    """Active collateral token address (pUSD on V2, USDC.e on V1)."""
    return get_active_addresses().collateral_token


def exchange_version_str() -> str:
    """'v1' or 'v2' — matches server-side `real_trades.exchange_version`."""
    return get_active_addresses().version
