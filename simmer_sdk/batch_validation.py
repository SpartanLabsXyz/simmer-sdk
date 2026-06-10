"""Client-side validation of server-supplied DepositWallet (DW) batches.

Why this exists
---------------
The DW activation / redemption / wrap flows fetch an EIP-712 ``typed_data``
blob (plus a ``calls`` list of ``{target, value, data}``) from a Simmer server
``/prepare`` endpoint, and the SDK signs it locally with the user's wallet key.
The server runs its own ``validate_*`` guards at SUBMIT time — but those protect
the *server* (its builder-HMAC relayer) from a malicious *user*. They do NOT
protect the *user* from a malicious / compromised / MITM'd *server*: a hostile
server simply wouldn't run them.

So the SDK signs whatever the server hands it. A server that returned an
``approve(attacker, MAX_UINT256)`` batch could have the user's own key authorize
draining their deposit wallet, while the SDK printed only "Signing batch
locally…". The "your key never leaves the process" guarantee is real but
insufficient — *what* the key signs was fully server-dictated.

These functions are the symmetric client-side copy of the server guards
(``simmer_v3/dw_approvals.py::validate_dw_approval_calls``,
``dw_redeem_external.py::validate_redeem_calls``,
``dw_wrap_on_dw.py::validate_wrap_on_dw_calls``). They are deliberately a faithful
mirror: any batch an *honest* server produces already passes the server's own
identical check, so mirroring it here cannot false-reject a legitimate batch —
it only rejects batches an honest server would never build.

All spender / target / token addresses are sourced from the SDK's own pinned
``polymarket_contracts`` constants, never from the server response.
"""

from __future__ import annotations

from typing import Any

# ERC-20 / ERC-1155 / CTF selectors (4-byte function signatures).
_APPROVE_SELECTOR = "0x095ea7b3"            # approve(address,uint256)
_SET_APPROVAL_FOR_ALL_SELECTOR = "0xa22cb465"  # setApprovalForAll(address,bool)
_REDEEM_SELECTOR = "0x01b7037c"             # redeemPositions(address,bytes32,bytes32,uint256[])
_WRAP_SELECTOR = "0x62355638"               # CollateralOnramp.wrap(address,address,uint256)

_MAX_UINT256 = (1 << 256) - 1


class BatchValidationError(ValueError):
    """Raised when a server-supplied DW batch fails client-side validation.

    A subclass of ``ValueError`` so existing ``except ValueError`` handlers
    still catch it, but distinct enough to special-case ("the server tried to
    get you to sign something it shouldn't have").
    """


def _get(call: Any, key: str) -> Any:
    """Read a field from a call entry that may be a dict (JSON) or an object."""
    if isinstance(call, dict):
        return call.get(key)
    return getattr(call, key, None)


def _require_eth_abi():
    try:
        from eth_abi import decode as abi_decode  # noqa: WPS433 (local import)
        return abi_decode
    except ImportError as exc:  # pragma: no cover - eth-account pulls eth-abi in
        raise BatchValidationError(
            "eth-abi is required to validate DW batches before signing. "
            "It ships with eth-account; install with: pip install eth-account"
        ) from exc


def _check_common(call: Any, i: int) -> tuple[str, str]:
    """Validate the target / value / data envelope shared by every call shape.

    Returns ``(target_lower, data_lower)``. Raises on any structural problem.
    """
    target = (_get(call, "target") or "")
    target_l = target.lower()
    data = (_get(call, "data") or "")
    data_l = data.lower()
    value = _get(call, "value")

    if not target_l.startswith("0x") or len(target_l) != 42:
        raise BatchValidationError(
            f"call[{i}]: target must be a 0x-prefixed 20-byte address; got {target!r}"
        )
    # value must be zero — none of these flows move native POL/MATIC.
    if str(value) not in ("0", "0x0", "0x00"):
        raise BatchValidationError(
            f"call[{i}]: value must be '0' (DW batches don't move native currency); "
            f"got {value!r}"
        )
    if not data_l.startswith("0x") or len(data_l) < 10:
        raise BatchValidationError(
            f"call[{i}]: data must be 0x-prefixed calldata of >= 4 bytes; "
            f"got {data!r}"
        )
    return target_l, data_l


def validate_dw_approval_calls(calls: list) -> None:
    """Validate a DW *activation* (approvals) batch before signing.

    Mirror of ``simmer_v3/dw_approvals.py::validate_dw_approval_calls``. Accepts
    only ``approve`` / ``setApprovalForAll`` calls to pinned (token, spender)
    pairs, each granting MAX (full activation), with no duplicates.
    """
    from .polymarket_contracts import (
        CONDITIONAL_TOKENS,
        PUSD,
        V2_FEE_ESCROW,
        CTF_COLLATERAL_ADAPTER,
        active_spenders,
        redemption_spenders,
    )

    abi_decode = _require_eth_abi()

    pusd_l = PUSD.lower()
    ctf_l = CONDITIONAL_TOKENS.lower()
    allowed_pairs: set[tuple[str, str]] = set()
    for spender in active_spenders():
        s = spender.lower()
        allowed_pairs.add((pusd_l, s))   # pUSD → spender (ERC20 approve)
        allowed_pairs.add((ctf_l, s))    # CTF  → spender (ERC1155 setApprovalForAll)
    allowed_pairs.add((pusd_l, V2_FEE_ESCROW.lower()))
    for adapter in redemption_spenders():
        allowed_pairs.add((ctf_l, adapter.lower()))
    allowed_pairs.add((pusd_l, CTF_COLLATERAL_ADAPTER.lower()))

    if not calls:
        raise BatchValidationError("Approval batch must contain at least 1 call; got 0.")

    seen: set[tuple[str, str]] = set()
    for i, call in enumerate(calls):
        target_l, data_l = _check_common(call, i)
        selector = data_l[:10]
        if selector not in (_APPROVE_SELECTOR, _SET_APPROVAL_FOR_ALL_SELECTOR):
            raise BatchValidationError(
                f"call[{i}]: selector {selector} not allowed — DW activation only "
                f"accepts ERC20.approve and ERC1155.setApprovalForAll."
            )
        try:
            args = bytes.fromhex(data_l[10:])
            if selector == _APPROVE_SELECTOR:
                spender, amount = abi_decode(["address", "uint256"], args)
            else:
                spender, approved = abi_decode(["address", "bool"], args)
                amount = _MAX_UINT256 if approved else 0
        except BatchValidationError:
            raise
        except Exception as exc:
            raise BatchValidationError(
                f"call[{i}]: could not decode approval calldata: {type(exc).__name__}: {exc}"
            )

        if amount != _MAX_UINT256:
            raise BatchValidationError(
                f"call[{i}]: approval must be MAX (full activation); got {amount}. "
                f"Revokes/partials are not part of a legitimate activation batch."
            )
        pair = (target_l, spender.lower())
        if pair not in allowed_pairs:
            raise BatchValidationError(
                f"call[{i}]: (token={pair[0][:10]}…, spender={pair[1][:10]}…) is not a "
                f"known approval pair. The server tried to get you to approve an "
                f"unexpected token/spender — refusing to sign."
            )
        if pair in seen:
            raise BatchValidationError(
                f"call[{i}]: duplicate (token, spender) pair — refusing to sign."
            )
        seen.add(pair)


def validate_redeem_calls(calls: list) -> None:
    """Validate a DW *redeem* batch before signing.

    Mirror of ``dw_redeem_external.py::validate_redeem_calls``: 1 or 2 calls
    (optional JIT setApprovalForAll then redeemPositions); the redeem targets a
    pinned adapter; the approval targets CTF and names a pinned redemption
    adapter as operator; index_sets ⊆ {1, 2}.
    """
    from .polymarket_contracts import (
        CONDITIONAL_TOKENS,
        CTF_COLLATERAL_ADAPTER,
        NEG_RISK_CTF_COLLATERAL_ADAPTER,
    )

    abi_decode = _require_eth_abi()

    ctf_l = CONDITIONAL_TOKENS.lower()
    allowed_targets = {
        ctf_l: {_SET_APPROVAL_FOR_ALL_SELECTOR},
        CTF_COLLATERAL_ADAPTER.lower(): {_REDEEM_SELECTOR},
        NEG_RISK_CTF_COLLATERAL_ADAPTER.lower(): {_REDEEM_SELECTOR},
    }
    allowed_ops = {CTF_COLLATERAL_ADAPTER.lower(), NEG_RISK_CTF_COLLATERAL_ADAPTER.lower()}

    if not (1 <= len(calls) <= 2):
        raise BatchValidationError(
            f"Redeem batch must be 1 or 2 calls (optional JIT approval + redeem); "
            f"got {len(calls)}."
        )

    saw_redeem = False
    saw_approval = False
    for i, call in enumerate(calls):
        target_l, data_l = _check_common(call, i)
        selector = data_l[:10]
        if target_l not in allowed_targets:
            raise BatchValidationError(
                f"call[{i}]: target {target_l[:10]}… is not CTF or a redemption "
                f"adapter — refusing to sign."
            )
        if selector not in allowed_targets[target_l]:
            raise BatchValidationError(
                f"call[{i}]: selector {selector} not allowed on target "
                f"{target_l[:10]}… — refusing to sign."
            )
        if selector == _SET_APPROVAL_FOR_ALL_SELECTOR:
            if i != 0:
                raise BatchValidationError(
                    f"call[{i}]: setApprovalForAll must be the first call (precedes redeem)."
                )
            try:
                operator, approved = abi_decode(["address", "bool"], bytes.fromhex(data_l[10:]))
            except Exception as exc:
                raise BatchValidationError(
                    f"call[{i}]: could not decode setApprovalForAll args: {exc}"
                )
            if not approved:
                raise BatchValidationError(
                    f"call[{i}]: setApprovalForAll must grant (approved=true)."
                )
            if operator.lower() not in allowed_ops:
                raise BatchValidationError(
                    f"call[{i}]: setApprovalForAll operator {operator.lower()[:10]}… is "
                    f"not a redemption adapter — refusing to sign."
                )
            saw_approval = True
        elif selector == _REDEEM_SELECTOR:
            try:
                _coll, _parent, _cond, index_sets = abi_decode(
                    ["address", "bytes32", "bytes32", "uint256[]"], bytes.fromhex(data_l[10:])
                )
            except Exception as exc:
                raise BatchValidationError(
                    f"call[{i}]: could not decode redeemPositions args: {exc}"
                )
            if not index_sets or any(s not in (1, 2) for s in index_sets):
                raise BatchValidationError(
                    f"call[{i}]: redeemPositions index_sets must be a subset of {{1, 2}}; "
                    f"got {list(index_sets)}."
                )
            saw_redeem = True

    if not saw_redeem:
        raise BatchValidationError("Redeem batch must contain exactly one redeemPositions call.")
    if len(calls) == 2 and not saw_approval:
        raise BatchValidationError(
            "Two-call redeem batch must be (setApprovalForAll, redeemPositions)."
        )


def validate_wrap_on_dw_calls(calls: list, deposit_wallet_address: str) -> None:
    """Validate a DW *wrap* (USDC.e → pUSD) batch before signing.

    Mirror of ``dw_wrap_on_dw.py::validate_wrap_on_dw_calls``: 1 or 2 calls
    (optional approve(USDC.e → Onramp) then Onramp.wrap(USDC.e, DW, amount)).
    The wrap recipient MUST be the user's own DW — never a third party.
    """
    from .polymarket_contracts import COLLATERAL_ONRAMP, USDC_E

    abi_decode = _require_eth_abi()

    onramp_l = COLLATERAL_ONRAMP.lower()
    usdce_l = USDC_E.lower()
    dw_l = (deposit_wallet_address or "").lower()
    if not dw_l.startswith("0x") or len(dw_l) != 42:
        raise BatchValidationError(
            f"deposit_wallet_address must be a 0x 20-byte address; got "
            f"{deposit_wallet_address!r}"
        )

    if not calls:
        raise BatchValidationError("Wrap batch must contain at least 1 call; got 0.")
    if len(calls) > 2:
        raise BatchValidationError(
            f"Wrap batch must contain at most 2 calls (optional approve + wrap); "
            f"got {len(calls)}."
        )

    for i, call in enumerate(calls):
        _check_common(call, i)  # target/value/data envelope

    if len(calls) == 2:
        a_target, a_data = _check_common(calls[0], 0)
        if a_target != usdce_l:
            raise BatchValidationError("call[0] (approve): target must be USDC.e.")
        if not a_data.startswith(_APPROVE_SELECTOR):
            raise BatchValidationError("call[0] (approve): selector must be ERC20.approve.")
        try:
            spender, _amt = abi_decode(["address", "uint256"], bytes.fromhex(a_data[10:]))
        except Exception as exc:
            raise BatchValidationError(f"call[0] (approve): decode failed: {exc}")
        if spender.lower() != onramp_l:
            raise BatchValidationError(
                f"call[0] (approve): spender must be the Onramp ({onramp_l[:10]}…)."
            )

    w_target, w_data = _check_common(calls[-1], len(calls) - 1)
    if w_target != onramp_l:
        raise BatchValidationError(
            f"call[{len(calls)-1}] (wrap): target must be the Onramp ({onramp_l[:10]}…)."
        )
    if not w_data.startswith(_WRAP_SELECTOR):
        raise BatchValidationError(
            f"call[{len(calls)-1}] (wrap): selector must be CollateralOnramp.wrap."
        )
    try:
        token, recipient, _amt = abi_decode(
            ["address", "address", "uint256"], bytes.fromhex(w_data[10:])
        )
    except Exception as exc:
        raise BatchValidationError(f"call[{len(calls)-1}] (wrap): decode failed: {exc}")
    if token.lower() != usdce_l:
        raise BatchValidationError(f"call[{len(calls)-1}] (wrap): token must be USDC.e.")
    if recipient.lower() != dw_l:
        raise BatchValidationError(
            f"call[{len(calls)-1}] (wrap): recipient must be your own deposit wallet "
            f"({dw_l[:10]}…), not {recipient.lower()[:10]}… — refusing to sign a wrap "
            f"to a third-party recipient."
        )
