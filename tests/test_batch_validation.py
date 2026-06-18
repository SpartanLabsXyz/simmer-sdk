"""Tests for simmer_sdk.batch_validation — client-side guards that refuse to
sign malicious server-supplied DepositWallet batches (0.17.29 security fix).

The security-critical assertions are the REJECT cases: a compromised/MITM'd
server must not be able to get the SDK to sign an approve-to-attacker,
setApprovalForAll-to-attacker, wrap-to-third-party, or value-bearing batch.
"""
from __future__ import annotations

import pytest
from eth_abi import encode as abi_encode

from simmer_sdk.batch_validation import (
    BatchValidationError,
    validate_dw_approval_calls,
    validate_redeem_calls,
    validate_wrap_on_dw_calls,
)
from simmer_sdk.polymarket_contracts import (
    CONDITIONAL_TOKENS,
    COLLATERAL_ONRAMP,
    CTF_COLLATERAL_ADAPTER,
    PUSD,
    USDC_E,
    active_spenders,
)

MAX = (1 << 256) - 1
ATTACKER = "0x" + "de" * 20
DW = "0x" + "11" * 20
SPENDER = active_spenders()[0]


def _approve(token, spender, amount=MAX):
    return {
        "target": token,
        "value": "0",
        "data": "0x095ea7b3" + abi_encode(["address", "uint256"], [spender, amount]).hex(),
    }


def _set_approval(token, operator, approved=True):
    return {
        "target": token,
        "value": "0",
        "data": "0xa22cb465" + abi_encode(["address", "bool"], [operator, approved]).hex(),
    }


def _redeem(adapter, index_sets=(1, 2)):
    data = "0x01b7037c" + abi_encode(
        ["address", "bytes32", "bytes32", "uint256[]"],
        [PUSD, b"\x00" * 32, b"\x11" * 32, list(index_sets)],
    ).hex()
    return {"target": adapter, "value": "0", "data": data}


def _wrap(recipient, amount=5_000_000):
    data = "0x62355638" + abi_encode(
        ["address", "address", "uint256"], [USDC_E, recipient, amount]
    ).hex()
    return {"target": COLLATERAL_ONRAMP, "value": "0", "data": data}


# --- approvals -------------------------------------------------------------

def test_approval_valid_passes():
    validate_dw_approval_calls([_approve(PUSD, SPENDER)])
    validate_dw_approval_calls([_set_approval(CONDITIONAL_TOKENS, SPENDER)])


def test_combo_approval_pairs_pass():
    """activate_combo_dw()'s batch — pUSD.approve(COMBO_EXCHANGE) +
    COMBO_POSITION_MANAGER.setApprovalForAll(COMBO_EXCHANGE) — must pass the
    client-side validator, else the SDK would refuse to sign the (honest)
    combo approval batch the server returns. Mirror of the server's
    get_allowed_dw_approval_targets() combo additions."""
    from simmer_sdk.polymarket_contracts import COMBO_EXCHANGE, COMBO_POSITION_MANAGER
    validate_dw_approval_calls([
        _approve(PUSD, COMBO_EXCHANGE),
        _set_approval(COMBO_POSITION_MANAGER, COMBO_EXCHANGE),
    ])


def test_combo_ctf_to_exchange_rejected():
    """The ERC1155 combo leg is on COMBO_POSITION_MANAGER, NOT the CTF — combo
    position tokens live on the Position Manager. CTF→COMBO_EXCHANGE is not a
    whitelisted pair (approving it is a no-op that would strand a fill), so the
    validator must refuse it."""
    from simmer_sdk.polymarket_contracts import COMBO_EXCHANGE
    with pytest.raises(BatchValidationError, match="not a known approval pair"):
        validate_dw_approval_calls([_set_approval(CONDITIONAL_TOKENS, COMBO_EXCHANGE)])


def test_approval_to_attacker_spender_rejected():
    with pytest.raises(BatchValidationError, match="not a known approval pair"):
        validate_dw_approval_calls([_approve(PUSD, ATTACKER)])


def test_setapproval_to_attacker_operator_rejected():
    with pytest.raises(BatchValidationError, match="not a known approval pair"):
        validate_dw_approval_calls([_set_approval(CONDITIONAL_TOKENS, ATTACKER)])


def test_approval_non_max_amount_rejected():
    with pytest.raises(BatchValidationError, match="MAX"):
        validate_dw_approval_calls([_approve(PUSD, SPENDER, amount=1)])


def test_approval_transfer_selector_rejected():
    # ERC20.transfer(attacker, amt) — selector 0xa9059cbb, not in the allowlist.
    transfer = {
        "target": PUSD,
        "value": "0",
        "data": "0xa9059cbb" + abi_encode(["address", "uint256"], [ATTACKER, MAX]).hex(),
    }
    with pytest.raises(BatchValidationError, match="not allowed"):
        validate_dw_approval_calls([transfer])


def test_approval_nonzero_value_rejected():
    call = _approve(PUSD, SPENDER)
    call["value"] = "1"
    with pytest.raises(BatchValidationError, match="value must be '0'"):
        validate_dw_approval_calls([call])


def test_approval_duplicate_rejected():
    with pytest.raises(BatchValidationError, match="duplicate"):
        validate_dw_approval_calls([_approve(PUSD, SPENDER), _approve(PUSD, SPENDER)])


# --- redeem ----------------------------------------------------------------

def test_redeem_valid_single_passes():
    validate_redeem_calls([_redeem(CTF_COLLATERAL_ADAPTER)])


def test_redeem_valid_with_jit_approval_passes():
    validate_redeem_calls([
        _set_approval(CONDITIONAL_TOKENS, CTF_COLLATERAL_ADAPTER),
        _redeem(CTF_COLLATERAL_ADAPTER),
    ])


def test_redeem_to_attacker_target_rejected():
    bad = _redeem(CTF_COLLATERAL_ADAPTER)
    bad["target"] = ATTACKER
    with pytest.raises(BatchValidationError, match="not CTF or a redemption adapter"):
        validate_redeem_calls([bad])


def test_redeem_jit_approval_to_attacker_operator_rejected():
    with pytest.raises(BatchValidationError, match="not a redemption adapter|not allowed"):
        validate_redeem_calls([
            _set_approval(CONDITIONAL_TOKENS, ATTACKER),
            _redeem(CTF_COLLATERAL_ADAPTER),
        ])


def test_redeem_bad_index_sets_rejected():
    with pytest.raises(BatchValidationError, match="index_sets"):
        validate_redeem_calls([_redeem(CTF_COLLATERAL_ADAPTER, index_sets=(7,))])


def test_redeem_too_many_calls_rejected():
    with pytest.raises(BatchValidationError, match="1 or 2 calls"):
        validate_redeem_calls([_redeem(CTF_COLLATERAL_ADAPTER)] * 3)


# --- wrap ------------------------------------------------------------------

def test_wrap_valid_to_own_dw_passes():
    validate_wrap_on_dw_calls([_wrap(DW)], DW)


def test_wrap_valid_with_approve_passes():
    validate_wrap_on_dw_calls([_approve(USDC_E, COLLATERAL_ONRAMP), _wrap(DW)], DW)


def test_wrap_to_attacker_recipient_rejected():
    with pytest.raises(BatchValidationError, match="must be your own deposit wallet"):
        validate_wrap_on_dw_calls([_wrap(ATTACKER)], DW)


def test_wrap_approve_to_attacker_spender_rejected():
    bad_approve = _approve(USDC_E, ATTACKER)
    with pytest.raises(BatchValidationError, match="spender must be the Onramp"):
        validate_wrap_on_dw_calls([bad_approve, _wrap(DW)], DW)


def test_wrap_missing_dw_address_rejected():
    with pytest.raises(BatchValidationError, match="deposit_wallet_address"):
        validate_wrap_on_dw_calls([_wrap(DW)], "0xDW")
