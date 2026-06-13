"""
Hyperliquid HIP-4 venue adapter.

Talks directly to ``api.hyperliquid.xyz`` — the agent host signs locally and
submits to the public ``/exchange`` endpoint; the Simmer server is not in the
execution path (it only records the fill afterward). Reads go to ``/info``.

This is the first implementer of ``VenueAdapter`` (see ``venue_adapter.py``).
Signing is delegated to a ``HyperliquidSigner`` (raw key or OWS), so the
adapter is custody-agnostic.

Status: signing + submission + order-book reads are validated against the live
API (P0 spike). Position/balance response *parsing* is implemented to the
documented API shape but the exact field paths for HIP-4 outcome holdings need
confirmation against a funded account (P2 / funded leg of P0).
"""

from typing import Any, Dict, List, Optional

import requests

from simmer_sdk.hyperliquid_signing import (
    HyperliquidSigner,
    MAINNET_API_URL,
    TESTNET_API_URL,
    build_cancel_action,
    build_order_action,
    now_ms,
    outcome_asset_id,
)

DEFAULT_TIMEOUT = 15.0


class HyperliquidVenueError(Exception):
    """Raised when the HL API returns an error status or an unexpected shape."""


class HyperliquidVenue:
    """VenueAdapter implementation for Hyperliquid HIP-4 outcome markets.

    Args:
        signer: a ``HyperliquidSigner`` (RawKey or OWS). Its ``address`` is the
            account that holds positions and signs orders.
        is_mainnet: True for ``api.hyperliquid.xyz``, False for testnet.
        base_url: override the API host (defaults by ``is_mainnet``).
        vault_address: optional sub-account/vault to trade on behalf of.
    """

    venue = "hyperliquid"

    def __init__(
        self,
        signer: HyperliquidSigner,
        is_mainnet: bool = True,
        base_url: Optional[str] = None,
        vault_address: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self._signer = signer
        self.address = signer.address
        self.is_mainnet = is_mainnet
        self.base_url = base_url or (MAINNET_API_URL if is_mainnet else TESTNET_API_URL)
        self.vault_address = vault_address
        self._timeout = timeout

    # ---- transport -------------------------------------------------------

    def _post(self, path: str, body: Dict[str, Any]) -> Any:
        resp = requests.post(f"{self.base_url}{path}", json=body, timeout=self._timeout)
        if resp.status_code != 200:
            raise HyperliquidVenueError(
                f"HL {path} HTTP {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()

    def _post_action(self, action: Dict[str, Any], signature: Dict[str, Any], nonce: int) -> Any:
        payload: Dict[str, Any] = {
            "action": action,
            "nonce": nonce,
            "signature": signature,
            "vaultAddress": self.vault_address,
        }
        result = self._post("/exchange", payload)
        if isinstance(result, dict) and result.get("status") == "err":
            raise HyperliquidVenueError(f"HL exchange error: {result.get('response')}")
        return result

    # ---- VenueAdapter core ----------------------------------------------

    def place_order(
        self,
        *,
        size: float,
        limit_px: float,
        is_buy: bool,
        outcome_id: int,
        side: str = "yes",
        tif: str = "Gtc",
        reduce_only: bool = False,
        cloid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Place a HIP-4 order.

        Args:
            size: number of contracts.
            limit_px: limit price (0-1 for outcome contracts).
            is_buy: True to buy the ``side``, False to sell it. (HL merges the
                YES/NO books — buying YES at P is selling NO at 1-P.)
            outcome_id: the HIP-4 outcome id (``markets.hyperliquid_outcome_id``).
            side: "yes" or "no" — selects the asset.
            tif: "Gtc" (resting), "Ioc", or "Alo" (post-only).
            reduce_only: only reduce an existing position.
            cloid: optional client order id (hex string).

        Returns the parsed ``/exchange`` response. On a resting order the
        ``oid`` is under ``response.data.statuses[i]``.
        """
        asset = outcome_asset_id(outcome_id, side)
        action = build_order_action(
            asset, is_buy, limit_px, size, reduce_only=reduce_only, tif=tif, cloid=cloid
        )
        nonce = now_ms()
        signature = self._signer.sign_l1_action(
            action, nonce, self.is_mainnet, vault_address=self.vault_address
        )
        return self._post_action(action, signature, nonce)

    def cancel_order(self, *, order_id: int, outcome_id: int, side: str = "yes") -> Dict[str, Any]:
        """Cancel a resting order by its venue order id."""
        asset = outcome_asset_id(outcome_id, side)
        action = build_cancel_action(asset, order_id)
        nonce = now_ms()
        signature = self._signer.sign_l1_action(
            action, nonce, self.is_mainnet, vault_address=self.vault_address
        )
        return self._post_action(action, signature, nonce)

    def get_positions(self, address: Optional[str] = None) -> List[Dict[str, Any]]:
        """Open positions for ``address`` (defaults to this adapter's address).

        HIP-4 outcome holdings sit in the unified HyperCore margin account
        (``clearinghouseState``). Field paths confirmed against a funded
        account in P2; this returns the ``assetPositions`` list as-is.
        """
        addr = address or self.address
        state = self._post("/info", {"type": "clearinghouseState", "user": addr})
        if isinstance(state, dict):
            return state.get("assetPositions", []) or []
        return []

    def get_balances(self, address: Optional[str] = None) -> Dict[str, Any]:
        """Collateral summary (account value + withdrawable USDC)."""
        addr = address or self.address
        state = self._post("/info", {"type": "clearinghouseState", "user": addr})
        if not isinstance(state, dict):
            return {}
        margin = state.get("marginSummary", {}) or {}
        return {
            "account_value": margin.get("accountValue"),
            "withdrawable": state.get("withdrawable"),
            "raw": state,
        }

    # ---- HL-specific extras ---------------------------------------------

    def get_order_book(self, outcome_id: int, side: str = "yes") -> Dict[str, Any]:
        """L2 order book for an outcome (public, no auth)."""
        coin = f"#{outcome_id * 10 + (0 if side.lower() == 'yes' else 1)}"
        return self._post("/info", {"type": "l2Book", "coin": coin})

    def get_open_orders(self, address: Optional[str] = None) -> List[Dict[str, Any]]:
        """Resting open orders for ``address``."""
        addr = address or self.address
        orders = self._post("/info", {"type": "openOrders", "user": addr})
        return orders if isinstance(orders, list) else []

    def approve_agent(self, agent_address: str, agent_name: str = "") -> Dict[str, Any]:
        """Approve a delegated trade-only agent key (one-time, signed by the
        main key). The agent key can place/cancel orders but cannot withdraw —
        the recommended bot-host setup (P0 finding Q3).
        """
        nonce = now_ms()
        action: Dict[str, Any] = {
            "type": "approveAgent",
            "agentAddress": agent_address,
            "agentName": agent_name,
            "nonce": nonce,
        }
        # Field order + primaryType must match the official SDK's sign_agent
        # exactly (HyperliquidTransaction:ApproveAgent), or HL rejects the sig.
        payload_types = [
            {"name": "hyperliquidChain", "type": "string"},
            {"name": "agentAddress", "type": "address"},
            {"name": "agentName", "type": "string"},
            {"name": "nonce", "type": "uint64"},
        ]
        signature = self._signer.sign_user_action(
            action, payload_types, "HyperliquidTransaction:ApproveAgent", self.is_mainnet
        )
        # Official SDK drops agentName from the submitted action when unnamed
        # (HL reads absent agentName as ""); the signature was already computed
        # over agentName="" so this stays valid.
        if not agent_name:
            del action["agentName"]
        return self._post_action(action, signature, nonce)
