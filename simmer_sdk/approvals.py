"""
Polymarket Approval Utilities

Helps users set up the required token approvals for Polymarket trading.
External wallets need to approve several spender contracts before trading.

**V1 required approvals (pre-2026-04-28 cutover):**
1. USDC.e (bridged) + USDC (native) → 3 V1 spenders (CTF, NegRisk CTF, NegRisk Adapter)
2. CTF Token (ERC1155) → same 3 spenders

**V2 required approvals (starting 2026-04-28):**
1. pUSD → 4 V2 spenders (CTF V2, NegRisk A, NegRisk B, NegRisk Adapter)
2. CTF Token (ERC1155) → same 4 spenders
3. pUSD → CollateralOfframp (for withdrawals that unwrap back to USDC.e)

Flag: `SIMMER_POLYMARKET_EXCHANGE_VERSION` env (default `v2` on 0.10.0+).

Usage:
    from simmer_sdk.approvals import get_approval_transactions, get_missing_approval_transactions

    # Check what's missing (server returns V1 or V2 allowance shape per its flag)
    approvals = client.check_approvals()
    if not approvals["all_set"]:
        txs = get_missing_approval_transactions(approvals)
        for tx in txs:
            print(f"Approve {tx['description']}")
            # Sign and send tx using your wallet/clawbot
"""

from typing import List, Dict, Any

from simmer_sdk.polymarket_contracts import (
    POLYGON_CHAIN_ID,
    USDC_E,
    USDC_NATIVE,
    PUSD,
    CONDITIONAL_TOKENS as CTF_TOKEN,
    V1_CTF_EXCHANGE,
    V1_NEG_RISK_CTF_EXCHANGE,
    NEG_RISK_ADAPTER,
    V2_CTF_EXCHANGE,
    V2_NEG_RISK_EXCHANGE_A,
    V2_NEG_RISK_EXCHANGE_B,
    is_v2_enabled,
    active_spenders,
)

# Back-compat re-exports for existing 0.9.x integrators
USDC_BRIDGED = USDC_E  # legacy alias

# ============================================================
# Flag-aware spender tables
# ============================================================
_V1_SPENDERS = {
    "ctf_exchange": {
        "address": V1_CTF_EXCHANGE,
        "name": "CTF Exchange",
        "description": "Main Polymarket exchange for standard markets",
    },
    "neg_risk_ctf_exchange": {
        "address": V1_NEG_RISK_CTF_EXCHANGE,
        "name": "Neg Risk CTF Exchange",
        "description": "Exchange for negative risk markets",
    },
    "neg_risk_adapter": {
        "address": NEG_RISK_ADAPTER,
        "name": "Neg Risk Adapter",
        "description": "Adapter for negative risk market positions",
    },
}

_V2_SPENDERS = {
    "ctf_exchange_v2": {
        "address": V2_CTF_EXCHANGE,
        "name": "CTF Exchange V2",
        "description": "Main V2 Polymarket exchange for standard markets (pUSD collateral)",
    },
    "neg_risk_exchange_a": {
        "address": V2_NEG_RISK_EXCHANGE_A,
        "name": "Neg Risk Exchange A",
        "description": "Primary V2 neg-risk exchange",
    },
    "neg_risk_exchange_b": {
        "address": V2_NEG_RISK_EXCHANGE_B,
        "name": "Neg Risk Exchange B",
        "description": "Secondary V2 neg-risk exchange (additional multi-outcome capacity)",
    },
    "neg_risk_adapter": {
        "address": NEG_RISK_ADAPTER,
        "name": "Neg Risk Adapter",
        "description": "Adapter for negative risk market positions (unchanged in V2)",
    },
}


def _spenders() -> Dict[str, Dict[str, str]]:
    """Active spender table based on exchange version flag."""
    return _V2_SPENDERS if is_v2_enabled() else _V1_SPENDERS


# Legacy export — existing code that iterates SPENDERS directly stays working.
# Reads the flag lazily via a module-level access pattern. New code should call
# `_spenders()` or read `active_spenders()` from polymarket_contracts instead.
SPENDERS = _V1_SPENDERS  # default binding, overridden below if V2
if is_v2_enabled():
    SPENDERS = _V2_SPENDERS

# Max uint256 for unlimited approval
MAX_UINT256 = "0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

# ERC20 approve(spender, amount) function selector
ERC20_APPROVE_SELECTOR = "0x095ea7b3"

# ERC1155 setApprovalForAll(operator, approved) function selector
ERC1155_SET_APPROVAL_SELECTOR = "0xa22cb465"

# Boolean true encoded as 32-byte hex (for setApprovalForAll)
BOOL_TRUE_ENCODED = "0000000000000000000000000000000000000000000000000000000000000001"

# Length of address prefix used in allowance keys (0x + 6 hex chars, matches backend)
ADDRESS_PREFIX_LENGTH = 8

# EVM word size in hex characters (32 bytes = 64 hex chars)
EVM_WORD_SIZE_HEX = 64


def _build_approval_info(spender_info: Dict[str, str], token: str) -> Dict[str, Any]:
    """Build approval info dict for a token/spender pair.

    `token` values:
    - "USDC.e" — V1 collateral
    - "USDC" — native USDC (V1 only)
    - "pUSD" — V2 collateral
    - "CTF" — ERC1155 outcome tokens (both versions)
    """
    token_map = {
        "USDC.e": (USDC_E, "Allow {name} to spend USDC.e"),
        "USDC": (USDC_NATIVE, "Allow {name} to spend USDC"),
        "pUSD": (PUSD, "Allow {name} to spend pUSD"),
    }
    if token in token_map:
        addr, desc_tpl = token_map[token]
        return {
            "token": token,
            "token_address": addr,
            "spender": spender_info["name"],
            "spender_address": spender_info["address"],
            "type": "ERC20",
            "description": desc_tpl.format(name=spender_info["name"]),
        }
    # CTF ERC1155
    return {
        "token": "CTF",
        "token_address": CTF_TOKEN,
        "spender": spender_info["name"],
        "spender_address": spender_info["address"],
        "type": "ERC1155",
        "description": f"Allow {spender_info['name']} to transfer CTF tokens",
    }


def get_required_approvals() -> List[Dict[str, Any]]:
    """Get list of all required approvals for the active exchange version.

    V1 (flag off): USDC + USDC.e + CTF per 3 spenders = 9 approvals.
    V2 (flag on, default on 0.10.0+): pUSD + CTF per 4 spenders = 8 approvals.
    """
    approvals: List[Dict[str, Any]] = []
    v2 = is_v2_enabled()
    for spender_info in _spenders().values():
        if v2:
            approvals.append(_build_approval_info(spender_info, "pUSD"))
        else:
            approvals.append(_build_approval_info(spender_info, "USDC"))
            approvals.append(_build_approval_info(spender_info, "USDC.e"))
        approvals.append(_build_approval_info(spender_info, "CTF"))
    return approvals


def get_approval_transactions() -> List[Dict[str, Any]]:
    """Get transaction data for all required Polymarket approvals.

    These transactions can be executed by a wallet or clawbot to set up
    all necessary approvals for trading. Selected token set + spender
    set depend on exchange version (V1 or V2).

    Returns:
        List of transaction objects ready for signing/sending, each containing:
        - to: Contract address to call
        - data: Encoded function call
        - value: "0x0" (no ETH needed)
        - chainId: Polygon chain ID
        - description: Human-readable description
        - token: Token being approved (USDC, USDC.e, pUSD, or CTF)
        - spender: Spender being approved

    Example:
        txs = get_approval_transactions()
        for tx in txs:
            signed = web3.eth.account.sign_transaction(tx, private_key)
            web3.eth.send_raw_transaction(signed.rawTransaction)
    """
    transactions: List[Dict[str, Any]] = []
    v2 = is_v2_enabled()

    for spender_info in _spenders().values():
        spender_addr = spender_info["address"]
        spender_name = spender_info["name"]

        # Same calldata shape across all ERC20s — approve(spender, MAX_UINT256)
        erc20_approve_data = (
            ERC20_APPROVE_SELECTOR +
            spender_addr[2:].lower().zfill(EVM_WORD_SIZE_HEX) +
            MAX_UINT256[2:]
        )

        if v2:
            # V2: pUSD only. Native USDC + USDC.e are not part of V2 trading.
            transactions.append({
                "to": PUSD,
                "data": erc20_approve_data,
                "value": "0x0",
                "chainId": POLYGON_CHAIN_ID,
                "description": f"Approve {spender_name} to spend pUSD",
                "token": "pUSD",
                "spender": spender_name,
                "spender_address": spender_addr,
            })
        else:
            # V1: both USDC variants (user may fund with either)
            transactions.append({
                "to": USDC_NATIVE,
                "data": erc20_approve_data,
                "value": "0x0",
                "chainId": POLYGON_CHAIN_ID,
                "description": f"Approve {spender_name} to spend USDC",
                "token": "USDC",
                "spender": spender_name,
                "spender_address": spender_addr,
            })
            transactions.append({
                "to": USDC_E,
                "data": erc20_approve_data,
                "value": "0x0",
                "chainId": POLYGON_CHAIN_ID,
                "description": f"Approve {spender_name} to spend USDC.e",
                "token": "USDC.e",
                "spender": spender_name,
                "spender_address": spender_addr,
            })

        # ERC1155 setApprovalForAll(operator, approved) — same for V1 and V2
        ctf_data = (
            ERC1155_SET_APPROVAL_SELECTOR +
            spender_addr[2:].lower().zfill(EVM_WORD_SIZE_HEX) +
            BOOL_TRUE_ENCODED
        )
        transactions.append({
            "to": CTF_TOKEN,
            "data": ctf_data,
            "value": "0x0",
            "chainId": POLYGON_CHAIN_ID,
            "description": f"Approve {spender_name} to transfer CTF tokens",
            "token": "CTF",
            "spender": spender_name,
            "spender_address": spender_addr,
        })

    return transactions


def get_missing_approval_transactions(
    approval_status: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Get transaction data only for missing approvals.

    Args:
        approval_status: Result from client.check_approvals() — server returns
            V1 or V2 allowance shape based on the server's flag state.

    Returns:
        List of transaction objects for missing approvals only.
    """
    if approval_status.get("all_set", False):
        return []

    all_txs = get_approval_transactions()
    allowances = approval_status.get("allowances", {})

    missing_txs = []
    for tx in all_txs:
        spender_prefix = tx["spender_address"][:ADDRESS_PREFIX_LENGTH]

        token = tx["token"]
        if token == "USDC":
            key = f"usdc_native_{spender_prefix}"
        elif token == "USDC.e":
            key = f"usdc_bridged_{spender_prefix}"
        elif token == "pUSD":
            key = f"pusd_{spender_prefix}"
        else:
            key = f"ctf_{spender_prefix}"

        if not allowances.get(key, False):
            missing_txs.append(tx)

    return missing_txs


def format_approval_guide(approval_status: Dict[str, Any]) -> str:
    """Format a human-readable approval status guide.

    Returns:
        Formatted string showing what approvals are missing.
    """
    if approval_status.get("all_set", False):
        return "✅ All Polymarket approvals are set. You're ready to trade!"

    lines = ["⚠️ Missing Polymarket approvals:\n"]
    allowances = approval_status.get("allowances", {})
    v2 = is_v2_enabled()

    for spender_info in _spenders().values():
        spender_prefix = spender_info["address"][:ADDRESS_PREFIX_LENGTH]
        spender_name = spender_info["name"]

        if v2:
            pusd_key = f"pusd_{spender_prefix}"
            ctf_key = f"ctf_{spender_prefix}"
            pusd_ok = allowances.get(pusd_key, False)
            ctf_ok = allowances.get(ctf_key, False)
            if not pusd_ok or not ctf_ok:
                lines.append(f"  {spender_name}:")
                if not pusd_ok:
                    lines.append(f"    ❌ pUSD approval missing")
                if not ctf_ok:
                    lines.append(f"    ❌ CTF approval missing")
        else:
            usdc_native_key = f"usdc_native_{spender_prefix}"
            usdc_bridged_key = f"usdc_bridged_{spender_prefix}"
            ctf_key = f"ctf_{spender_prefix}"

            usdc_native_ok = allowances.get(usdc_native_key, False)
            usdc_bridged_ok = allowances.get(usdc_bridged_key, False)
            ctf_ok = allowances.get(ctf_key, False)

            if not usdc_native_ok or not usdc_bridged_ok or not ctf_ok:
                lines.append(f"  {spender_name}:")
                if not usdc_native_ok:
                    lines.append(f"    ❌ USDC approval missing")
                if not usdc_bridged_ok:
                    lines.append(f"    ❌ USDC.e approval missing")
                if not ctf_ok:
                    lines.append(f"    ❌ CTF approval missing")

    lines.append("\nTo set approvals:")
    lines.append("  1. Use get_approval_transactions() to get tx data")
    lines.append("  2. Sign and send each transaction from your wallet")
    lines.append("  3. Wait for confirmations, then retry trading")
    if v2:
        lines.append(
            "\nNote: V2 uses pUSD instead of USDC.e. If you still hold USDC.e, "
            "migrate via simmer.markets/dashboard (one click) or manually via "
            "the Collateral Onramp. See docs.simmer.markets/v2-migration."
        )

    return "\n".join(lines)
