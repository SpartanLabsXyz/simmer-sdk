"""
Polymarket Approval Utilities

Helps users set up the required token approvals for Polymarket trading.
External wallets need to approve several spender contracts before trading.

Required approvals:
1. USDC.e (bridged) → CTF Exchange, Neg Risk CTF Exchange, Neg Risk Adapter
2. CTF Token (ERC1155) → Same 3 spenders

Usage:
    from simmer_sdk.approvals import get_approval_transactions, get_missing_approval_transactions

    # Check what's missing
    approvals = client.check_approvals()
    if not approvals["all_set"]:
        # Get transaction data for missing approvals only
        txs = get_missing_approval_transactions(approvals)
        for tx in txs:
            print(f"Approve {tx['description']}")
            # Sign and send tx using your wallet/clawbot
"""

from typing import List, Dict, Any

# Polygon Mainnet Chain ID
POLYGON_CHAIN_ID = 137

# Token addresses on Polygon
USDC_BRIDGED = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"  # USDC.e
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"  # Native USDC (Circle-issued)
CTF_TOKEN = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"  # Polymarket CTF

# Spender contracts that need approval
SPENDERS = {
    "ctf_exchange": {
        "address": "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E",
        "name": "CTF Exchange",
        "description": "Main Polymarket exchange for standard markets",
    },
    "neg_risk_ctf_exchange": {
        "address": "0xC5d563A36AE78145C45a50134d48A1215220f80a",
        "name": "Neg Risk CTF Exchange",
        "description": "Exchange for negative risk markets",
    },
    "neg_risk_adapter": {
        "address": "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296",
        "name": "Neg Risk Adapter",
        "description": "Adapter for negative risk market positions",
    },
}

# Max uint256 for unlimited approval
MAX_UINT256 = "0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

# ERC20 approve(spender, amount) function selector
ERC20_APPROVE_SELECTOR = "0x095ea7b3"

# ERC1155 setApprovalForAll(operator, approved) function selector
ERC1155_SET_APPROVAL_SELECTOR = "0xa22cb465"

# Boolean true encoded as 32-byte hex (for setApprovalForAll)
BOOL_TRUE_ENCODED = "0000000000000000000000000000000000000000000000000000000000000001"

# Length of address prefix used in allowance keys (0x + 6 hex chars, matching backend)
ADDRESS_PREFIX_LENGTH = 8

# EVM word size in hex characters (32 bytes = 64 hex chars)
EVM_WORD_SIZE_HEX = 64


def _build_approval_info(spender_info: Dict[str, str], token: str) -> Dict[str, Any]:
    """Build approval info dict for a token/spender pair."""
    if token == "USDC.e":
        return {
            "token": "USDC.e",
            "token_address": USDC_BRIDGED,
            "spender": spender_info["name"],
            "spender_address": spender_info["address"],
            "type": "ERC20",
            "description": f"Allow {spender_info['name']} to spend USDC.e",
        }
    elif token == "USDC":
        return {
            "token": "USDC",
            "token_address": USDC_NATIVE,
            "spender": spender_info["name"],
            "spender_address": spender_info["address"],
            "type": "ERC20",
            "description": f"Allow {spender_info['name']} to spend USDC",
        }
    else:
        return {
            "token": "CTF",
            "token_address": CTF_TOKEN,
            "spender": spender_info["name"],
            "spender_address": spender_info["address"],
            "type": "ERC1155",
            "description": f"Allow {spender_info['name']} to transfer CTF tokens",
        }


def get_required_approvals() -> List[Dict[str, Any]]:
    """
    Get list of all required approvals for Polymarket trading.

    Returns:
        List of approval requirements with token, spender, and type info
    """
    approvals = []
    for spender_info in SPENDERS.values():
        approvals.append(_build_approval_info(spender_info, "USDC"))
        approvals.append(_build_approval_info(spender_info, "USDC.e"))
        approvals.append(_build_approval_info(spender_info, "CTF"))
    return approvals


def get_approval_transactions() -> List[Dict[str, Any]]:
    """
    Get transaction data for all required Polymarket approvals.

    These transactions can be executed by a wallet or clawbot to set up
    all necessary approvals for trading. The transactions are the same
    regardless of wallet address (approval is granted by the signer).

    Returns:
        List of transaction objects ready for signing/sending, each containing:
        - to: Contract address to call
        - data: Encoded function call
        - value: "0x0" (no ETH needed)
        - chainId: Polygon chain ID
        - description: Human-readable description
        - token: Token being approved
        - spender: Spender being approved

    Example:
        txs = get_approval_transactions()
        for tx in txs:
            # Using web3.py
            signed = web3.eth.account.sign_transaction(tx, private_key)
            web3.eth.send_raw_transaction(signed.rawTransaction)
    """
    transactions = []

    for spender_info in SPENDERS.values():
        spender_addr = spender_info["address"]
        spender_name = spender_info["name"]

        # ERC20 approve(spender, amount) calldata (same for both USDC tokens)
        # Encode: selector + spender (32 bytes) + amount (32 bytes)
        erc20_approve_data = (
            ERC20_APPROVE_SELECTOR +
            spender_addr[2:].lower().zfill(EVM_WORD_SIZE_HEX) +
            MAX_UINT256[2:]  # Remove 0x prefix
        )

        # USDC native (Circle-issued)
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

        # USDC.e (bridged)
        transactions.append({
            "to": USDC_BRIDGED,
            "data": erc20_approve_data,
            "value": "0x0",
            "chainId": POLYGON_CHAIN_ID,
            "description": f"Approve {spender_name} to spend USDC.e",
            "token": "USDC.e",
            "spender": spender_name,
            "spender_address": spender_addr,
        })

        # ERC1155 setApprovalForAll(operator, approved)
        # Encode: selector + operator (32 bytes) + approved (32 bytes, 1 = true)
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
    """
    Get transaction data only for missing approvals.

    Args:
        approval_status: Result from client.check_approvals()

    Returns:
        List of transaction objects for missing approvals only

    Example:
        approvals = client.check_approvals()
        if not approvals["all_set"]:
            missing_txs = get_missing_approval_transactions(approvals)
            print(f"Need to send {len(missing_txs)} approval transactions")
    """
    if approval_status.get("all_set", False):
        return []

    all_txs = get_approval_transactions()
    allowances = approval_status.get("allowances", {})

    missing_txs = []
    for tx in all_txs:
        # Build the key used in allowances dict
        # Format: "usdc_native_{spender[:8]}", "usdc_bridged_{spender[:8]}", or "ctf_{spender[:8]}"
        spender_prefix = tx["spender_address"][:ADDRESS_PREFIX_LENGTH]

        if tx["token"] == "USDC":
            key = f"usdc_native_{spender_prefix}"
        elif tx["token"] == "USDC.e":
            key = f"usdc_bridged_{spender_prefix}"
        else:
            key = f"ctf_{spender_prefix}"

        # If not in allowances or False, it's missing
        if not allowances.get(key, False):
            missing_txs.append(tx)

    return missing_txs


def format_approval_guide(approval_status: Dict[str, Any]) -> str:
    """
    Format a human-readable approval status guide.

    Args:
        approval_status: Result from client.check_approvals()

    Returns:
        Formatted string showing approval status
    """
    if approval_status.get("all_set", False):
        return "✅ All Polymarket approvals are set. You're ready to trade!"

    lines = ["⚠️ Missing Polymarket approvals:\n"]
    allowances = approval_status.get("allowances", {})

    # Group by spender for readability
    for spender_info in SPENDERS.values():
        spender_prefix = spender_info["address"][:ADDRESS_PREFIX_LENGTH]
        spender_name = spender_info["name"]

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

    return "\n".join(lines)
