"""
Polymarket contract addresses — V1 and V2.

Mirrors the server-side `simmer_v3/polymarket_contracts.py` so SDK
users that build orders / approvals locally use the same addresses
the Simmer API expects. Callers should use `active_spenders()`,
`collateral_token()`, and `get_active_addresses()` instead of picking
V1 or V2 constants directly.

Exchange version:
- Default starting `simmer-sdk 0.12.2`: **time-gated**. Signs V1
  before the Polymarket V2 cutover at 2026-04-28 11:00 UTC, and
  V2 from that timestamp onward. Same installed binary auto-flips
  at cutover — no upgrade needed.
- Override via `SIMMER_POLYMARKET_EXCHANGE_VERSION=v1` or `=v2`
  env var to force a shape (rare — use only for testing).
- Note: 0.10.0 through 0.12.1 defaulted to V2 unconditionally,
  which signed V2-shaped orders against the V1 CLOB pre-cutover
  and got `order_version_mismatch`. 0.12.2 fixes that.

Sources:
- Polymarket docs: docs.polymarket.com/v2-migration
- PolyNode V2 guide: docs.polynode.dev/guides/v2-migration
- Simmer migration guide: docs.simmer.markets/v2-migration
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

# Polymarket V2 cutover timestamp. Before this instant the SDK signs V1;
# at and after this instant it signs V2. The same installed binary auto-
# flips — no upgrade required at cutover.
POLYMARKET_V2_CUTOVER_UTC = datetime(2026, 4, 28, 11, 0, 0, tzinfo=timezone.utc)

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

# V2 fee + redemption infrastructure (2026-05-01 upgrade)
V2_FEE_ESCROW = "0x3A43D88ef8Aae4dF5a50B3abf67122CAAeEF7c9F"                     # pUSD fee collection (PolyNode)
CTF_COLLATERAL_ADAPTER = "0xAdA100Db00Ca00073811820692005400218FcE1f"             # redemption adapter
NEG_RISK_CTF_COLLATERAL_ADAPTER = "0xadA2005600Dec949baf300f4C6120000bDB6eAab"   # neg-risk redemption adapter


# ============================================================
# Combo (parlay) infrastructure
# ============================================================
# Combos settle on their own exchange + position-token contract, distinct
# from the V2 CLOB spenders. A deposit wallet must approve COMBO_EXCHANGE to
# spend its pUSD (ERC20) AND its combo position tokens (ERC1155 on
# COMBO_POSITION_MANAGER — NOT the CTF; combo position tokens live there,
# Polymarket-confirmed 2026-06-18). Mirrors the server
# `simmer_v3/polymarket_contracts.py` COMBO_* constants.
COMBO_EXCHANGE = "0xe3333700cA9d93003F00f0F71f8515005F6c00Aa"
COMBO_POSITION_MANAGER = "0x006F54F7f9A22e0000CC2AB60031000000ae9fEF"


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

    Default behavior (since `simmer-sdk 0.12.2`): time-gated on the
    Polymarket V2 cutover at 2026-04-28 11:00 UTC. Signs V1 before that
    instant, V2 from that instant onward. Same installed binary auto-
    flips — no upgrade or env-var change needed at cutover.

    Override via `SIMMER_POLYMARKET_EXCHANGE_VERSION=v1` or `=v2` env
    var to force a shape (rare — use only for testing).
    """
    override = os.getenv("SIMMER_POLYMARKET_EXCHANGE_VERSION", "").strip().lower()
    if override == "v1":
        return False
    if override == "v2":
        return True
    return datetime.now(timezone.utc) >= POLYMARKET_V2_CUTOVER_UTC


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


def redemption_spenders() -> list[str]:
    """Adapter contracts that need ERC1155 setApprovalForAll on the CTF token.

    These are not trading-path spenders; they are redemption-path adapters
    added in the 2026-05-01 upgrade. Required for `redeemPositions` calls
    routed through the adapter contracts. Both V1 and V2 wallets need these.
    """
    return [CTF_COLLATERAL_ADAPTER, NEG_RISK_CTF_COLLATERAL_ADAPTER]


def combo_spenders() -> list[str]:
    """Spenders a deposit wallet must approve to trade combos (parlays).

    Just the combo exchange today. Kept separate from active_spenders() so
    the standard DW activation cascade is unchanged — combo approval is an
    opt-in extra (run `client.activate_combo_dw()`) for users who actually
    build combos. Mirrors the server `combo_spenders()`.
    """
    return [COMBO_EXCHANGE]
