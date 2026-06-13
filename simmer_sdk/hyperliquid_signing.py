"""
Hyperliquid HIP-4 order signing — dual signer (raw key + OWS).

HIP-4 outcome contracts trade on HyperCore via EIP-712 "phantom agent"
signatures submitted to ``https://api.hyperliquid.xyz/exchange``. The wallet
signs a small fixed-shape EIP-712 message (domain "Exchange", primaryType
"Agent") whose ``connectionId`` is the keccak hash of the msgpack-packed
action. This means the signer never has to understand HL action schemas —
both the raw-key and OWS paths sign the identical typed-data envelope.

The wire-building + action-hash + float-formatting primitives are reused
verbatim from the official ``hyperliquid-python-sdk`` (optional extra
``simmer-sdk[hyperliquid]``) — the same code path validated byte-for-byte in
the P0 spike (server recovered the exact signer from a locally-built order).
This module adds only the thin layer the SDK lacks: outcome-market asset-id
math and an OWS signer that decomposes ``sign_l1_action`` into its public
parts and routes the typed-data dict through ``ows_sign_typed_data``.

SECURITY: the raw private key is only used in-memory for signing and is never
logged, transmitted, or persisted. The OWS path never sees the key at all —
it lives in the local OWS vault.
"""

import json
from typing import Any, Dict, Optional, Protocol, runtime_checkable

# HIP-4 asset encoding (matches simmer_v3/hyperliquid_client.py):
#   assetId = 100_000_000 + 10 * outcomeId + side   (side: 0 = Yes, 1 = No)
OUTCOME_ASSET_BASE = 100_000_000
OUTCOME_ASSET_MULTIPLIER = 10
SIDE_YES = 0
SIDE_NO = 1

MAINNET_API_URL = "https://api.hyperliquid.xyz"
TESTNET_API_URL = "https://api.hyperliquid-testnet.xyz"


class HyperliquidSDKNotInstalled(ImportError):
    """Raised when the optional ``hyperliquid-python-sdk`` extra is missing."""

    def __init__(self) -> None:
        super().__init__(
            "Hyperliquid trading requires the official SDK. Install it with:\n"
            "    pip install 'simmer-sdk[hyperliquid]'\n"
            "(provides the validated EIP-712 wire-building + action-hash "
            "primitives)."
        )


def _hl_signing():
    """Lazy-import the official SDK's signing primitives.

    Kept lazy so importing simmer_sdk without the [hyperliquid] extra never
    fails — only actual HL trading needs it.
    """
    try:
        from hyperliquid.utils import signing  # type: ignore
    except (ImportError, ModuleNotFoundError):
        raise HyperliquidSDKNotInstalled()
    return signing


def outcome_asset_id(outcome_id: int, side: str = "yes") -> int:
    """HIP-4 outcome asset id for the exchange ``order`` action.

    Note: distinct from the ``#<n>`` *coin notation* used for ``/info``
    queries (which is ``10*outcomeId + side``). The exchange action keys on
    the full asset id.
    """
    side_val = SIDE_YES if side.lower() == "yes" else SIDE_NO
    return OUTCOME_ASSET_BASE + OUTCOME_ASSET_MULTIPLIER * outcome_id + side_val


def build_order_action(
    asset: int,
    is_buy: bool,
    limit_px: float,
    sz: float,
    reduce_only: bool = False,
    tif: str = "Gtc",
    cloid: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a single-order HL exchange action (the object that gets hashed).

    ``tif`` is the time-in-force: "Gtc" (resting), "Ioc", or "Alo" (post-only).
    Prices/sizes are formatted to HL's wire rules by the SDK primitives.
    """
    signing = _hl_signing()
    order = {
        "is_buy": is_buy,
        "limit_px": limit_px,
        "sz": sz,
        "reduce_only": reduce_only,
        "order_type": {"limit": {"tif": tif}},
    }
    if cloid is not None:
        from hyperliquid.utils.types import Cloid  # type: ignore

        order["cloid"] = Cloid.from_str(cloid)
    wire = signing.order_request_to_order_wire(order, asset)
    return signing.order_wires_to_order_action([wire])


def build_cancel_action(asset: int, oid: int) -> Dict[str, Any]:
    """Build a cancel-by-order-id action."""
    return {"type": "cancel", "cancels": [{"a": asset, "o": oid}]}


def now_ms() -> int:
    """Millisecond timestamp used as the action nonce."""
    return _hl_signing().get_timestamp_ms()


def _split_signature(sig_hex: str) -> Dict[str, Any]:
    """Split a 65-byte hex signature into HL's ``{r, s, v}`` shape.

    OWS returns a packed 65-byte hex signature; HL's /exchange wants r/s/v
    separately with v in {27, 28}.
    """
    raw = sig_hex[2:] if sig_hex.startswith("0x") else sig_hex
    if len(raw) != 130:
        raise ValueError(
            f"expected a 65-byte (130 hex char) signature, got {len(raw)} chars"
        )
    r = "0x" + raw[0:64]
    s = "0x" + raw[64:128]
    v = int(raw[128:130], 16)
    if v < 27:  # some signers return 0/1 recovery id; HL wants 27/28
        v += 27
    return {"r": r, "s": s, "v": v}


@runtime_checkable
class HyperliquidSigner(Protocol):
    """Signs HL exchange actions. Implementations: raw key or OWS vault."""

    address: str

    def sign_l1_action(
        self,
        action: Dict[str, Any],
        nonce: int,
        is_mainnet: bool,
        vault_address: Optional[str] = None,
        expires_after: Optional[int] = None,
    ) -> Dict[str, Any]:
        ...

    def sign_user_action(
        self,
        action: Dict[str, Any],
        payload_types: list,
        primary_type: str,
        is_mainnet: bool,
    ) -> Dict[str, Any]:
        ...


class RawKeyHyperliquidSigner:
    """Signs with an in-memory EVM private key via eth_account.

    Thin wrapper over the official SDK's ``sign_l1_action`` /
    ``sign_user_signed_action`` (which use eth_account under the hood).
    """

    def __init__(self, private_key: str):
        from eth_account import Account

        self._account = Account.from_key(private_key)
        self.address = self._account.address

    def sign_l1_action(
        self,
        action: Dict[str, Any],
        nonce: int,
        is_mainnet: bool,
        vault_address: Optional[str] = None,
        expires_after: Optional[int] = None,
    ) -> Dict[str, Any]:
        signing = _hl_signing()
        return signing.sign_l1_action(
            self._account, action, vault_address, nonce, expires_after, is_mainnet
        )

    def sign_user_action(
        self,
        action: Dict[str, Any],
        payload_types: list,
        primary_type: str,
        is_mainnet: bool,
    ) -> Dict[str, Any]:
        signing = _hl_signing()
        return signing.sign_user_signed_action(
            self._account, action, payload_types, primary_type, is_mainnet
        )


class OwsHyperliquidSigner:
    """Signs HL actions with a key held in the local OWS vault.

    Decomposes the official SDK's ``sign_l1_action`` into its public parts
    (``action_hash`` → ``construct_phantom_agent`` → ``l1_payload``) and
    routes the resulting EIP-712 typed-data dict through
    ``ows_sign_typed_data``. The P0 spike verified this produces a
    bit-identical signature to the raw-key path.

    Gotcha (P0 finding): ``construct_phantom_agent`` puts the action hash in
    ``connectionId`` as raw bytes32 — must be hex-encoded before the dict can
    be JSON-serialized for OWS.
    """

    def __init__(self, wallet_name: str):
        from simmer_sdk.ows_utils import get_ows_wallet_address

        self.wallet_name = wallet_name
        self.address = get_ows_wallet_address(wallet_name)

    def _ows_sign_typed(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        from simmer_sdk.ows_utils import ows_sign_typed_data

        sig_hex = ows_sign_typed_data(self.wallet_name, json.dumps(payload))
        return _split_signature(sig_hex)

    def sign_l1_action(
        self,
        action: Dict[str, Any],
        nonce: int,
        is_mainnet: bool,
        vault_address: Optional[str] = None,
        expires_after: Optional[int] = None,
    ) -> Dict[str, Any]:
        signing = _hl_signing()
        action_hash = signing.action_hash(action, vault_address, nonce, expires_after)
        phantom_agent = signing.construct_phantom_agent(action_hash, is_mainnet)
        payload = signing.l1_payload(phantom_agent)
        # connectionId is raw bytes32 — hex-encode for JSON/OWS (P0 finding).
        payload["message"]["connectionId"] = "0x" + payload["message"]["connectionId"].hex()
        return self._ows_sign_typed(payload)

    def sign_user_action(
        self,
        action: Dict[str, Any],
        payload_types: list,
        primary_type: str,
        is_mainnet: bool,
    ) -> Dict[str, Any]:
        signing = _hl_signing()
        # Mutate the action IN PLACE — exactly as the official
        # sign_user_signed_action does — so the caller submits the same dict
        # (with signatureChainId + hyperliquidChain) it was signed over.
        # Copying here would make the OWS path submit an action missing those
        # fields, diverging from the raw path and failing HL's verification.
        action["signatureChainId"] = "0x66eee"
        action["hyperliquidChain"] = "Mainnet" if is_mainnet else "Testnet"
        payload = signing.user_signed_payload(primary_type, payload_types, action)
        return self._ows_sign_typed(payload)
