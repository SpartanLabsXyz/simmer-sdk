"""
Polymarket combo (parlay) RFQ — discovery, pricing, and taker placement.

A combo bundles 2+ binary market legs into one YES/NO position settled by a
single RFQ order on the combo Exchange v3. This module is the **taker /
requester** side (place a combo); the maker/quoter side lives in the
combo-quoter service, not here.

Lifecycle (gist §6, reverse-engineered + verified live; auth + RFQ_CREATE
empirically ACK'd on a deposit wallet 2026-06-16):

    open -> auth -> RFQ_CREATE -> RFQ_QUOTE_READY -> (sign taker order)
         -> RFQ_ACCEPT -> RFQ_EXECUTION_UPDATE{MATCHED->MINED->CONFIRMED, tx_hash}

The ~5s quote window after RFQ_QUOTE_READY is the one rule that matters: if
signing outlasts it the gateway returns EXPIRED_RFQ, so we re-quote on the same
authed socket (bounded). Signing gets a generous timeout so a slow-but-valid
signature is never killed as "request timed out".

Hosts/paths are env-overridable (Polymarket has moved gateway hostnames before).
"""

import asyncio
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

import requests

from simmer_sdk.combo_signing import (
    build_and_sign_combo_order_eoa,
    build_and_sign_combo_order_dw,
)

# ── Endpoints (gist §1, verified live 2026-06-13; all env-overridable) ──
COMBOS_RFQ_BASE = os.getenv("SIMMER_COMBOS_RFQ_BASE", "https://combos-rfq-api.polymarket.com")
COMBO_MARKETS_PATH = "/v1/rfq/combo-markets"
COMBO_PRICE_PATH = "/v1/rfq/combo-price"
RFQ_WS_URL = os.getenv(
    "SIMMER_COMBOS_RFQ_WS_URL",
    "wss://combos-rfq-gateway-requester.polymarket.sh/ws",
)
# urllib/requests default UA gets 403'd by the combo REST host.
_UA = os.getenv(
    "SIMMER_COMBOS_UA",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) simmer-sdk-combo",
)
_ORIGIN = os.getenv("SIMMER_COMBOS_ORIGIN", "https://polymarket.com")

_SIDE_INT = {"BUY": 0, "SELL": 1}


# ───────────────────────── discovery ─────────────────────────

def fetch_combo_legs(limit: int = 100, max_legs: int = 300) -> List[Dict[str, Any]]:
    """List combo-eligible binary markets (public, no auth, cursor-paginated).

    Each leg: ``{id, condition_id, position_ids:[YES,NO], slug, title,
    outcomes, outcome_prices, volume, tags}``. Both sides are pickable; store
    the chosen side's ``position_ids[i]`` for downstream calls.
    """
    legs: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    pages = 0
    while pages < 10 and len(legs) < max_legs:
        url = f"{COMBOS_RFQ_BASE}{COMBO_MARKETS_PATH}?limit={limit}"
        if cursor:
            url += f"&cursor={cursor}"
        r = requests.get(url, headers={"User-Agent": _UA, "accept": "application/json"}, timeout=20)
        r.raise_for_status()
        data = r.json()
        page = data.get("markets") or (data if isinstance(data, list) else [])
        legs.extend(page)
        cursor = data.get("next_cursor")
        pages += 1
        if not cursor:
            break
    return legs[:max_legs]


def estimate_combo_price(leg_prices: List[float], stake: float = 1.0) -> Optional[Dict[str, float]]:
    """Offline product-of-legs estimate (gist §4). No network.

    Combined implied probability = product of each chosen leg's price.
    Returns ``{combined_price, multiplier, potential_payout}`` or None if
    fewer than 2 valid legs.
    """
    valid = [float(p) for p in leg_prices if p and 0 < float(p) <= 1]
    if len(valid) < 2:
        return None
    combined = 1.0
    for p in valid:
        combined *= p
    mult = (1.0 / combined) if combined > 0 else 0.0
    return {"combined_price": combined, "multiplier": mult, "potential_payout": stake * mult}


# ───────────────────────── placement ─────────────────────────

class ComboPlacementError(Exception):
    """Raised when a combo placement fails (auth, expiry exhaustion, gateway error)."""


def _signed_order_to_wire(order) -> Dict[str, Any]:
    """RFQ_ACCEPT.signed_order wire shape: side + signatureType as INTS, big
    numbers as strings, expiration:'0' + signature (gist §6.4 + §4 type gotcha)."""
    return {
        "salt": str(order.salt),
        "maker": order.maker,
        "signer": order.signer,
        "tokenId": str(order.tokenId),
        "makerAmount": str(order.makerAmount),
        "takerAmount": str(order.takerAmount),
        "side": _SIDE_INT[order.side.upper()],
        "signatureType": int(order.signatureType),
        "timestamp": str(order.timestamp),
        "metadata": order.metadata,
        "builder": order.builder,
        "expiration": "0",
        "signature": order.signature,
    }


async def _place_combo_ws(
    *,
    creds: Dict[str, str],
    private_key: str,
    eoa_address: str,
    deposit_wallet_address: Optional[str],
    signature_type: int,
    leg_position_ids: List[str],
    direction: str,
    side: str,
    size_usdc_e6: int,
    builder_code: Optional[str],
    metadata: Optional[str],
    max_retries: int,
    on_status: Optional[Callable[[str], None]],
) -> Dict[str, Any]:
    try:
        import websockets  # noqa: WPS433
    except ImportError:
        raise ImportError(
            "websockets is required for combo placement. Install with "
            "`pip install 'simmer-sdk[combo]'` or `pip install websockets`."
        )

    def status(s: str) -> None:
        if on_status:
            try:
                on_status(s)
            except Exception:
                pass

    maker_address = deposit_wallet_address if signature_type == 3 else eoa_address

    def sign(token_id: str, maker_amount: int, taker_amount: int):
        if signature_type == 3:
            if not deposit_wallet_address:
                raise ComboPlacementError("deposit_wallet_address required for signature_type=3")
            return build_and_sign_combo_order_dw(
                private_key=private_key, eoa_address=eoa_address,
                deposit_wallet_address=deposit_wallet_address, token_id=token_id,
                side=direction, maker_amount=maker_amount, taker_amount=taker_amount,
                builder_code=builder_code, metadata=metadata,
            )
        return build_and_sign_combo_order_eoa(
            private_key=private_key, eoa_address=eoa_address, token_id=token_id,
            side=direction, maker_amount=maker_amount, taker_amount=taker_amount,
            builder_code=builder_code, metadata=metadata,
        )

    # Node ws clients must set Origin explicitly (browsers do it automatically).
    async with websockets.connect(RFQ_WS_URL, open_timeout=20, max_size=2 ** 22,
                                  additional_headers={"Origin": _ORIGIN}) as ws:
        # auth
        await ws.send(json.dumps({
            "type": "auth",
            "auth": {"apiKey": creds["apiKey"], "secret": creds["secret"],
                     "passphrase": creds["passphrase"]},
            "identity": {"signer_address": maker_address, "maker_address": maker_address,
                         "signature_type": signature_type},
        }))
        authed = False
        deadline = time.time() + 10
        while time.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
            msg = json.loads(raw)
            if msg.get("type") == "auth":
                authed = bool(msg.get("success"))
                break
        if not authed:
            raise ComboPlacementError("combo RFQ auth failed (check CLOB creds / identity)")

        async def request_quote():
            status("requesting")
            await ws.send(json.dumps({
                "type": "RFQ_CREATE",
                "leg_position_ids": [str(x) for x in leg_position_ids],
                "direction": direction.upper(),
                "side": side.upper(),
                "requested_size": {"unit": "notional", "value_e6": str(int(size_usdc_e6))},
            }))

        await request_quote()
        attempt = 1
        # exec/quote read loop
        overall_deadline = time.time() + 60
        while time.time() < overall_deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, overall_deadline - time.time()))
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "RFQ_QUOTE_READY":
                req = msg.get("request", {})
                quote = msg.get("quote", {})
                token_id = req.get("no_position_id") if side.upper() == "NO" else req.get("yes_position_id")
                maker_amt = int(quote["maker_amount_e6"])
                taker_amt = int(quote["taker_amount_e6"])
                status("signing")
                order = sign(str(token_id), maker_amt, taker_amt)  # local, fast
                await ws.send(json.dumps({
                    "type": "RFQ_ACCEPT",
                    "rfq_id": req.get("rfq_id"),
                    "quote_id": quote.get("quote_id"),
                    "signed_order": _signed_order_to_wire(order),
                }))
                status("submitting")
                overall_deadline = time.time() + 60  # generous post-accept exec window

            elif mtype == "RFQ_EXECUTION_UPDATE" and msg.get("status") in ("CONFIRMED", "MINED"):
                status(msg.get("status").lower())
                return {"status": msg.get("status"), "tx_hash": msg.get("tx_hash"),
                        "rfq_id": msg.get("rfq_id")}

            elif (mtype in ("RFQ_STATUS_UPDATE", "RFQ_EXECUTION_UPDATE")
                  and (msg.get("status") in ("EXPIRED", "FAILED")
                       or "EXPIRED" in str(msg.get("code", "")).upper())):
                is_expired = msg.get("status") == "EXPIRED" or "EXPIRED" in str(msg.get("code", "")).upper()
                if is_expired and attempt <= max_retries:
                    attempt += 1
                    status("requoting")
                    await request_quote()
                    overall_deadline = time.time() + 60
                else:
                    raise ComboPlacementError(
                        f"combo placement failed: {msg.get('code') or msg.get('status')}"
                    )

        raise ComboPlacementError("combo placement timed out waiting for execution")


def place_combo(
    *,
    creds: Dict[str, str],
    private_key: str,
    eoa_address: str,
    leg_position_ids: List[str],
    size_usdc: float,
    deposit_wallet_address: Optional[str] = None,
    signature_type: int = 0,
    direction: str = "BUY",
    side: str = "YES",
    builder_code: Optional[str] = None,
    metadata: Optional[str] = None,
    max_retries: int = 2,
    on_status: Optional[Callable[[str], None]] = None,
    dry_run: bool = True,
    allow_deposit_wallet: bool = False,
) -> Dict[str, Any]:
    """Place a combo via the requester RFQ WebSocket.

    Args:
        creds: CLOB L2 creds ``{apiKey, secret, passphrase}``.
        private_key: owner EOA key (signs locally; never transmitted).
        eoa_address: the EOA address for ``private_key``.
        leg_position_ids: chosen-side CTF token id per leg (>= 2).
        size_usdc: stake in USD (>= $1). Sent as notional ``value_e6``.
        deposit_wallet_address / signature_type: pass the DW + ``3`` for the
            POLY_1271 deposit-wallet cohort; default EOA (``0``).
        dry_run: when True (default) does NOT connect/place — returns the
            resolved request for inspection. Set False to actually place
            (money-path; gated by the caller).

    Returns the execution result ``{status, tx_hash, rfq_id}`` on a real fill,
    or the dry-run plan when ``dry_run=True``.
    """
    if len(leg_position_ids) < 2:
        raise ValueError("a combo needs at least 2 legs")
    if size_usdc < 1:
        raise ValueError("combo stake must be at least $1 (Polymarket order minimum)")
    if signature_type == 3 and not deposit_wallet_address:
        raise ValueError("signature_type=3 (deposit wallet) requires deposit_wallet_address")

    size_usdc_e6 = int(round(size_usdc * 1_000_000))
    maker_address = deposit_wallet_address if signature_type == 3 else eoa_address
    plan = {
        "dry_run": True,
        "ws_url": RFQ_WS_URL,
        "identity": {"signer_address": maker_address, "maker_address": maker_address,
                     "signature_type": signature_type},
        "leg_position_ids": [str(x) for x in leg_position_ids],
        "direction": direction.upper(), "side": side.upper(),
        "requested_size": {"unit": "notional", "value_e6": str(size_usdc_e6)},
        "note": "dry_run=True — no socket opened, no order signed, no money moved.",
    }
    if dry_run:
        return plan

    # Deposit-wallet gate. DW combo *identity + signing* work, but a DW cannot
    # APPROVE the combo exchange: DepositWallet.execute is onlyFactory and
    # DepositWalletFactory.proxy is onlyOperator (Polymarket's relayer only),
    # and that relayer rejects approvals to the combo exchange 0xe333…
    # ("operator not in the allowed list"). So a DW combo signs fine but fails
    # at on-chain settlement on missing allowance. Block it with a clear
    # message instead of a confusing settlement failure. Auto-overridable:
    # once Polymarket whitelists 0xe333 on the DW relayer and the DW is
    # approved, pass allow_deposit_wallet=True. (Verified 2026-06-16.)
    if signature_type == 3 and not allow_deposit_wallet:
        raise ComboPlacementError(
            "Deposit-wallet combos are not available yet. A deposit wallet can "
            "only approve contracts via Polymarket's relayer (DW.execute is "
            "onlyFactory -> Factory.proxy is onlyOperator), and that relayer "
            "currently rejects approvals to the combo exchange 0xe3333700…  "
            "('operator not in the allowed list'). So the order would sign but "
            "fail at settlement on a missing allowance. EOA / self-custody "
            "wallets work today. This auto-resolves once Polymarket whitelists "
            "the combo exchange on the deposit-wallet relayer; pass "
            "allow_deposit_wallet=True to override once your DW is approved."
        )

    return asyncio.run(_place_combo_ws(
        creds=creds, private_key=private_key, eoa_address=eoa_address,
        deposit_wallet_address=deposit_wallet_address, signature_type=signature_type,
        leg_position_ids=leg_position_ids, direction=direction, side=side,
        size_usdc_e6=size_usdc_e6, builder_code=builder_code, metadata=metadata,
        max_retries=max_retries, on_status=on_status,
    ))
