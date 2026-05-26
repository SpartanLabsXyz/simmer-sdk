"""External-wallet deposit-wallet redemption — SDK-side prepare/sign/submit.

Mirror of `website/src/lib/polymarket/dwRedeemExternal.js` in Python. Used
when the SDK detects the caller is an external-wallet user with a Polymarket
deposit-wallet (DW) deployed — the position lives on the DW contract, so
`msg.sender` of the redeem call must be the DW. The DW only acts on signed
WALLET batches relayed through Polymarket. The legacy unsigned-tx EOA path
(returned by `/api/sdk/redeem` for non-DW external wallets) doesn't apply.

Flow
----

  1. `prepare_dw_redeem(...)` → POST /api/sdk/dw-redeem/prepare
     Server validates market resolution + builds the EIP-712 WALLET batch.

  2. Sign the typed data locally with `WALLET_PRIVATE_KEY` or the OWS vault.
     Single signature covers the whole batch (1-2 calls inside).

  3. `submit_dw_redeem(...)` → POST /api/sdk/dw-redeem/submit
     Server validates the call shape against the security gate, relays via
     Polymarket with our builder HMAC, waits for receipt, extracts the pUSD
     payout, writes the real_trades row.

Why the SDK helper exists separately from the existing `client.redeem()`
unsigned-tx flow:

  - The existing flow returns `unsigned_tx` for external users; the SDK signs
    a raw EIP-1559 tx and broadcasts via `/api/sdk/wallet/broadcast-tx`. That
    works for V1 EOA-direct positions (msg.sender = user EOA = position holder).
  - For DW positions, msg.sender of the redeem MUST be the DW. A user EOA
    redeem call would revert (no shares) or no-op silently. Hence the
    separate prepare/sign/submit shape that submits via the relayer.

Both helpers are pure HTTP + signing — no shared state with `SimmerClient`.
The client passes its API base, auth headers, and signing material in.

This closes the auto-redeem-gap row in the simmer monorepo's
`_dev/active/_polymarket-dw-phase-2/NEXT.md`. Server-side counterparts:
`/api/sdk/dw-redeem/{prepare,submit}` (added in the same release).

Requires server >= simmer-sdk 0.17.0 (which adds the SDK-auth wrappers
around the G6 dashboard endpoints). Older servers respond 404 to the
prepare endpoint; callers should fall back to the legacy `client.redeem()`
flow on 404 (handled in `client.py`).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class DwRedeemError(Exception):
    """Base error for the SDK-side ext+DW redemption flow."""


class DwRedeemPrepareError(DwRedeemError):
    """Raised when /api/sdk/dw-redeem/prepare returns a non-2xx response or
    a special routing signal.

    Carries:
      - status_code: HTTP status from prepare (200 for routing signals).
      - eoa_fallback: True when server signalled DW=0 + EOA>0 — caller
        should fall back to the unsigned-tx EOA path.
      - already_redeemed: True when both DW + EOA balances are 0 — the
        position was already redeemed (or never held); nothing to do.
      - not_redeemable: True when market payout is not yet ready on-chain.
      - reason: machine-readable reason string (e.g. "market_not_settled").
      - detail: fine-grained detail (e.g. "neg_risk_not_determined").
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        eoa_fallback: bool = False,
        already_redeemed: bool = False,
        not_redeemable: bool = False,
        reason: str = "unknown",
        detail: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.eoa_fallback = eoa_fallback
        self.already_redeemed = already_redeemed
        self.not_redeemable = not_redeemable
        self.reason = reason
        self.detail = detail


class DwRedeemSubmitError(DwRedeemError):
    """Raised when /api/sdk/dw-redeem/submit fails (relayer rejection, signature
    mismatch, etc.). Caller may want to re-prepare a fresh batch (the nonce
    is opaque + has a TTL).
    """

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def prepare_dw_redeem(
    *,
    api_url: str,
    headers: Dict[str, str],
    market_id: str,
    side: str,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """POST /api/sdk/dw-redeem/prepare → typed-data + nonce + deadline + calls.

    Args:
        api_url: API base, e.g. "https://api.simmer.markets/api"
        headers: Already-built auth headers (Authorization: Bearer ...).
        market_id: Simmer market UUID (NOT the Polymarket condition_id —
            server resolves it).
        side: "yes" or "no" — the user's outcome side that won.
        timeout: HTTP timeout, seconds.

    Returns:
        Dict with keys (verbatim — pass `nonce`, `deadline`, `calls` back to
        submit unmodified):

          - typed_data:  EIP-712 typed data dict (domain, types, primaryType,
                         message). Pass straight to `Account.sign_typed_data`
                         or `ows_sign_typed_data`.
          - nonce:       opaque str (relayer nonce; expires)
          - deadline:    opaque str (unix-seconds; expires)
          - calls:       list of {target,value,data} dicts
          - deposit_wallet_address: str
          - condition_id, outcome, negative_risk, is_cancelled

        Special case — if the server detects DW balance is 0 but EOA balance
        is > 0 (SIM-1645: SDK signs trades EOA-style with sig-type-0 so
        positions accumulate on the EOA), it returns:

            {"eoa_fallback": True, "condition_id": ..., "outcome": ...}

        The caller should fall back to the unsigned-tx EOA path
        (`client.redeem()` legacy flow). This helper raises
        `DwRedeemPrepareError(eoa_fallback=True)` to signal it.

    Raises:
        DwRedeemPrepareError: on non-2xx response or eoa_fallback signal.
    """
    res = requests.post(
        f"{api_url}/sdk/dw-redeem/prepare",
        headers={**headers, "Content-Type": "application/json"},
        data=json.dumps({"market_id": market_id, "side": side}),
        timeout=timeout,
    )

    if not res.ok:
        try:
            data = res.json()
            if data.get("not_redeemable"):
                _reason = data.get("reason", "market_not_settled")
                _detail = data.get("detail")
                _msg = (
                    "Payouts are not finalized on-chain yet. Try again later."
                    if _detail == "neg_risk_not_determined"
                    else f"Market not yet redeemable ({_detail or _reason})."
                )
                raise DwRedeemPrepareError(
                    _msg,
                    status_code=res.status_code,
                    not_redeemable=True,
                    reason=_reason,
                    detail=_detail,
                )
            detail = data.get("detail")
            if isinstance(detail, list) and detail:
                detail = detail[0].get("msg") or str(detail[0])
        except DwRedeemPrepareError:
            raise
        except Exception:
            detail = None
        raise DwRedeemPrepareError(
            detail or f"HTTP {res.status_code}",
            status_code=res.status_code,
        )

    data = res.json()
    if data.get("already_redeemed"):
        # Both DW and EOA hold zero of the winning position — the redemption
        # already happened (or the position was never held). The server
        # short-circuits here to avoid relayer-sponsored zero-payout calls.
        # Caller should treat as success-no-op.
        raise DwRedeemPrepareError(
            "Position already redeemed — nothing to do.",
            status_code=200,
            already_redeemed=True,
        )
    if data.get("eoa_fallback"):
        # Not an error per se — the server is telling us to use the legacy
        # path. Surface as a typed exception so the caller can handle it
        # explicitly without parsing response shape.
        raise DwRedeemPrepareError(
            "Position tokens are held in your EOA — routing to direct EOA path.",
            status_code=200,
            eoa_fallback=True,
        )
    if data.get("not_redeemable"):
        # SIM-2511: payout not yet finalized on-chain (e.g. NegRisk adapter
        # getDetermined=0). Server blocks before returning signable typed data.
        _reason = data.get("reason", "market_not_settled")
        _detail = data.get("detail")
        _msg = (
            "Payouts are not finalized on-chain yet. Try again later."
            if _detail == "neg_risk_not_determined"
            else f"Market not yet redeemable ({_detail or _reason})."
        )
        raise DwRedeemPrepareError(
            _msg,
            status_code=200,
            not_redeemable=True,
            reason=_reason,
            detail=_detail,
        )
    return data


def sign_dw_redeem_typed_data(
    typed_data: Dict[str, Any],
    *,
    private_key: Optional[str] = None,
    ows_wallet: Optional[str] = None,
) -> str:
    """Sign the EIP-712 batch typed data and return a 0x-prefixed hex signature.

    Exactly one of `private_key` / `ows_wallet` must be set.

    Args:
        typed_data: The `typed_data` field from `prepare_dw_redeem`.
        private_key: Hex-encoded EVM private key (with or without 0x prefix).
        ows_wallet: OWS wallet name (vault-managed key).

    Returns:
        Hex-encoded 65-byte signature string (0x-prefixed).

    Raises:
        DwRedeemError: if neither / both signing materials are provided, or
            if the underlying signer raises.
    """
    if bool(private_key) == bool(ows_wallet):
        raise DwRedeemError(
            "Provide exactly one of `private_key` or `ows_wallet` to sign "
            "the redemption batch."
        )

    if private_key:
        try:
            from eth_account import Account
        except ImportError as exc:
            raise DwRedeemError(
                "eth-account is required for ext+DW redemption signing. "
                "Install with: pip install eth-account"
            ) from exc

        # eth_account expects an `HexBytes` key OR a 0x-prefixed hex str
        # (it accepts both). Account.sign_typed_data has two call shapes —
        # `from_key(...).sign_typed_data(...)` (instance method, expects
        # domain/types/message kwargs) OR module-level
        # `Account.sign_typed_data(key, full_message=<dict>)` which accepts
        # a fully-assembled typed-data dict (including `primaryType`). The
        # G6 dashboard wagmi flow signs the WALLET batch's TypedDataSign
        # (primaryType wraps Batch) — pass the full dict via `full_message`
        # so `primaryType` is honored.
        try:
            signed = Account.sign_typed_data(
                private_key,
                full_message=typed_data,
            )
        except Exception as exc:
            raise DwRedeemError(
                f"sign_typed_data failed: {type(exc).__name__}: {exc}"
            ) from exc
        sig = signed.signature.hex()
        return sig if sig.startswith("0x") else "0x" + sig

    # OWS path
    try:
        from simmer_sdk.ows_utils import ows_sign_typed_data
    except ImportError as exc:
        raise DwRedeemError(
            "OWS support is required to sign with an OWS-managed wallet."
        ) from exc
    try:
        sig = ows_sign_typed_data(ows_wallet, json.dumps(typed_data))
    except Exception as exc:
        raise DwRedeemError(
            f"OWS sign_typed_data failed: {type(exc).__name__}: {exc}"
        ) from exc
    return sig if sig.startswith("0x") else "0x" + sig


def submit_dw_redeem(
    *,
    api_url: str,
    headers: Dict[str, str],
    market_id: str,
    side: str,
    signature: str,
    prepared: Dict[str, Any],
    timeout: float = 90.0,
) -> Dict[str, Any]:
    """POST /api/sdk/dw-redeem/submit → {success, tx_hash, payout_pusd, ...}.

    Args:
        api_url, headers: same as prepare.
        market_id, side: same as prepare (echoed back so the server can write
            the real_trades row).
        signature: hex-encoded 65-byte ECDSA signature from
            `sign_dw_redeem_typed_data`.
        prepared: verbatim dict returned by `prepare_dw_redeem` — used to pull
            `nonce`, `deadline`, `calls`. Passed by reference, not modified.
        timeout: HTTP timeout — wider than prepare because the server waits
            for the relayer + on-chain receipt synchronously (~15-30s common,
            up to 60s under load).

    Returns:
        Dict with keys: success (bool), tx_hash (str), tx_id (str|None),
        payout_pusd (float), calls_executed (int).

    Raises:
        DwRedeemSubmitError: on non-2xx response.
    """
    body = {
        "market_id": market_id,
        "side": side,
        "signature": signature,
        "nonce": prepared["nonce"],
        "deadline": prepared["deadline"],
        "calls": prepared["calls"],
    }
    res = requests.post(
        f"{api_url}/sdk/dw-redeem/submit",
        headers={**headers, "Content-Type": "application/json"},
        data=json.dumps(body),
        timeout=timeout,
    )

    if not res.ok:
        try:
            data = res.json()
            detail = data.get("detail")
            if isinstance(detail, list) and detail:
                detail = detail[0].get("msg") or str(detail[0])
        except Exception:
            detail = None
        # 503 from the server typically means stale nonce after a prior
        # failure — caller may want to re-prepare for a fresh signature.
        msg = detail or (
            "Relayer rejected the redemption — please try again."
            if res.status_code == 503
            else f"HTTP {res.status_code}"
        )
        raise DwRedeemSubmitError(msg, status_code=res.status_code)

    return res.json()


def redeem_dw_external(
    *,
    api_url: str,
    headers: Dict[str, str],
    market_id: str,
    side: str,
    private_key: Optional[str] = None,
    ows_wallet: Optional[str] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """End-to-end ext+DW redemption — convenience wrapper.

    Calls prepare → sign → submit. Caller passes the same auth + signing
    material it would use for trade signing.

    `on_progress` (optional) is called with one of {"preparing", "signing",
    "submitting"} so callers can surface stage transitions to the user
    (mirrors `moveToTradingExternal`'s onProgress hook).

    Returns the submit-endpoint response dict on success.

    Raises:
        DwRedeemPrepareError(eoa_fallback=True) when the server signals the
            user should use the legacy unsigned-tx EOA path. The caller is
            expected to catch this and fall through to `client.redeem()`'s
            existing external-wallet flow.
        DwRedeemPrepareError(eoa_fallback=False) on other prepare failures
            (market not closed, position lost, etc.).
        DwRedeemSubmitError on submit failures.
        DwRedeemError on signing failures.
    """
    if on_progress:
        on_progress("preparing")
    prepared = prepare_dw_redeem(
        api_url=api_url,
        headers=headers,
        market_id=market_id,
        side=side,
    )
    if on_progress:
        on_progress("signing")
    signature = sign_dw_redeem_typed_data(
        prepared["typed_data"],
        private_key=private_key,
        ows_wallet=ows_wallet,
    )
    if on_progress:
        on_progress("submitting")
    return submit_dw_redeem(
        api_url=api_url,
        headers=headers,
        market_id=market_id,
        side=side,
        signature=signature,
        prepared=prepared,
    )
