"""
Hyperliquid venue adapter with pmxt-constructed orders (SIM-4222, A2).

Same venue, different *construction locus*. The chain:

    pmxt sidecar buildOrder    → constructs the venue-correct unsigned action
      → assert_built_action    → verify it is what we asked for, BEFORE signing
      → HyperliquidSigner      → signs locally (raw key or OWS); no key ever
                                 goes near pmxt
      → HL /exchange           → we submit directly; pmxt is not in the money
                                 path and takes no toll

What pmxt buys is **outsourced construction maintenance** — asset universe,
wire encoding, perps/spot/HIP-4 coverage, and future HL protocol drift handled
upstream instead of by us. That is the whole rent-vs-build trade, so this
adapter stays deliberately thin: it maps our arguments onto pmxt's
``CreateOrderParams``, verifies the result, and delegates everything else
(signing, submission, reads) to the same code paths ``HyperliquidVenue`` uses.

``HyperliquidVenue`` remains the anti-corruption fallback: same signer, same
submit, same reads, native construction. If pmxt drifts or goes away, callers
swap the class they construct.

Verified against pmxt-core 2.54.0 (published dist read + a live sidecar,
2026-07-28) and two attributed mainnet fills from the builder-attribution gate
run. See ``simmer/_dev/active/_hyperliquid-rd/pmxt-hl-venue-adapter-spec.md``.

Deliberate capability gaps (pmxt's ``buildOrder`` cannot express these — it
hardcodes ``r: false``, never emits ``c``, and reaches only ``Gtc``/``Ioc``):
``reduce_only``, ``cloid``, and post-only (``Alo``). Each raises rather than
being silently dropped — a reduce-only flag that quietly becomes ``False`` on
a closing order is a money-path bug. Use ``HyperliquidVenue`` for those.
"""

from typing import Any, Dict, List, Optional
import re

from simmer_sdk.hyperliquid_signing import HyperliquidSigner, now_ms
from simmer_sdk.hyperliquid_venue import (
    DEFAULT_TIMEOUT,
    HyperliquidVenue,
    HyperliquidVenueError,
)
from simmer_sdk.pmxt_sidecar import PmxtSidecarClient, PmxtSidecarError

#: Builder fee in TENTHS of a basis point. 10 = 1bp, the rate the gate run
#: filled at. The user-side approval ceiling is 10bp (``maxBuilderFee=100``),
#: so this is raisable to 100 without re-prompting users — a pricing decision.
DEFAULT_BUILDER_FEE_TENTHS_BP = 10

#: pmxt order ``type`` → the tif it produces (read from pmxt-core 2.54.0
#: ``dist/exchanges/hyperliquid/index.js``; confirmed live). Only these two
#: are reachable through pmxt construction.
_TIF_BY_ORDER_TYPE = {"limit": "Gtc", "market": "Ioc"}
_ORDER_TYPE_BY_TIF = {v: k for k, v in _TIF_BY_ORDER_TYPE.items()}

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class PmxtHyperliquidVenue:
    """``VenueAdapter`` implementation that builds orders via a pmxt sidecar.

    Args:
        signer: a ``HyperliquidSigner`` (RawKey or OWS). Signs locally; the key
            never reaches the sidecar, which is construction-only and holds no
            credentials.
        builder_address: the PUBLIC Simmer builder address for attribution. The
            builder's private key is not used at order time and must never be
            configured here — orders are signed by the trader's key, and the
            builder is named only by address inside the signed action.
        sidecar_url: origin of the local pmxt sidecar (127.0.0.1, next to the
            agent process — not a shared service).
        builder_fee_tenths_bp: fee in tenths of a basis point (10 = 1bp).
        main_address: the account-of-record for reads and fill recording.
            Under HL's delegated ``approveAgent`` setup the signer is a
            trade-only agent key whose address is NOT the account — always set
            this when signing with an agent key.
        is_mainnet / base_url / vault_address / timeout: as ``HyperliquidVenue``.
        sidecar: inject a pre-built ``PmxtSidecarClient`` (tests, custom
            transport). Overrides ``sidecar_url``.

    Keep one venue instance per key and avoid concurrent ``place_order`` calls
    on the same instance — HL nonces are per-key and monotonic.
    """

    venue = "hyperliquid"

    #: Where the unsigned action comes from. Signing/submission/reads are
    #: identical to the native adapter; this is the only axis that differs.
    construction = "pmxt"

    def __init__(
        self,
        signer: HyperliquidSigner,
        *,
        builder_address: str,
        sidecar_url: str = "http://127.0.0.1:8080",
        builder_fee_tenths_bp: int = DEFAULT_BUILDER_FEE_TENTHS_BP,
        is_mainnet: bool = True,
        base_url: Optional[str] = None,
        vault_address: Optional[str] = None,
        main_address: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
        sidecar: Optional[PmxtSidecarClient] = None,
    ):
        if not isinstance(builder_address, str) or not _ADDRESS_RE.match(builder_address):
            # Mirrors pmxt's own validation, but locally and before any network
            # call. Also the guard that a private key can never be passed here:
            # a 32-byte key is 64 hex chars and fails this 40-char match.
            raise ValueError(
                f"builder_address must be a 0x-prefixed 20-byte address, got {builder_address!r}"
            )
        if not isinstance(builder_fee_tenths_bp, int) or isinstance(builder_fee_tenths_bp, bool):
            raise ValueError("builder_fee_tenths_bp must be an int (tenths of a basis point)")
        if builder_fee_tenths_bp < 0:
            raise ValueError("builder_fee_tenths_bp must be non-negative")

        self.builder_address = builder_address
        self.builder_fee_tenths_bp = builder_fee_tenths_bp

        # Composition, not inheritance: `place_order` takes a raw HL asset id
        # where the native adapter takes a HIP-4 outcome id, so the two are not
        # substitutable on that method. Everything else is shared by delegation.
        self._native = HyperliquidVenue(
            signer,
            is_mainnet=is_mainnet,
            base_url=base_url,
            vault_address=vault_address,
            main_address=main_address,
            timeout=timeout,
        )
        self._signer = signer
        self._sidecar = sidecar or PmxtSidecarClient(sidecar_url, timeout=timeout)

    # ---- identity (mirrors the native adapter) ---------------------------

    @property
    def address(self) -> str:
        """Account-of-record — what positions/balances/fills belong to."""
        return self._native.address

    @property
    def signer_address(self) -> str:
        """The signing key's address. Equals ``address`` only for raw-key
        setups; under ``approveAgent`` it is the agent key, not the account."""
        return self._native.signer_address

    @property
    def is_mainnet(self) -> bool:
        return self._native.is_mainnet

    @property
    def base_url(self) -> str:
        return self._native.base_url

    @property
    def vault_address(self) -> Optional[str]:
        return self._native.vault_address

    # ---- startup handshake ------------------------------------------------

    def preflight(self, *, asset_id: int = 1, probe_price: float = 1000.0) -> None:
        """Verify the sidecar is up and still speaks the contract we pinned.

        Runs the health check plus one real ``buildOrder`` whose result is put
        through the same pre-sign gate a live order would face — so a drifted
        dispatcher is caught at startup rather than on the first trade. Call
        before trading; it is deliberately not run in ``__init__`` (no network
        in a constructor).

        Raises ``PmxtSidecarError`` if the sidecar is down or the shape drifted.
        """
        if not self._sidecar.health():
            raise PmxtSidecarError(f"pmxt sidecar unhealthy at {self._sidecar.base_url}")
        params = self._build_params(
            asset_id=asset_id,
            is_buy=True,
            size=0.01,
            limit_px=probe_price,
            order_type="limit",
            market_id=None,
        )
        action = self._sidecar.build_order(params)
        self._assert_built(
            action,
            asset_id=asset_id,
            is_buy=True,
            size=0.01,
            limit_px=probe_price,
            want_tif="Gtc",
        )

    # ---- VenueAdapter core ------------------------------------------------

    def place_order(
        self,
        *,
        size: float,
        limit_px: float,
        is_buy: bool,
        asset_id: int,
        order_type: str = "limit",
        tif: Optional[str] = None,
        market_id: Optional[str] = None,
        reduce_only: bool = False,
        cloid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Place an order built by pmxt, signed and submitted by us.

        Args:
            size: order size in the asset's units.
            limit_px: limit price. Always sent — pmxt silently defaults an
                omitted price to ``'0.5'``, which would be catastrophic on a
                perp, so this argument is required even for market orders
                (where it acts as the IOC's price bound).
            is_buy: True to buy, False to sell.
            asset_id: HL's NUMERIC asset id (e.g. 1 = ETH perp). This is not
                the HIP-4 outcome id the native adapter takes; HIP-4 asset-id
                resolution is A3 and unwired here.
            order_type: ``"limit"`` (tif Gtc, rests) or ``"market"`` (tif Ioc
                at ``limit_px``). pmxt reaches no other tif.
            tif: optional native-parity alias — ``"Gtc"``/``"Ioc"``. Must agree
                with ``order_type`` if both are given. ``"Alo"`` is unsupported.
            market_id: optional venue symbol (e.g. ``"ETH"``). pmxt echoes it
                back but ``buildOrder`` does not use it; ``asset_id`` is what
                addresses the market.
            reduce_only / cloid: NOT supported on this construction path (pmxt
                hardcodes ``r: false`` and emits no ``c``). Raises rather than
                dropping them silently — use ``HyperliquidVenue`` instead.

        Returns the parsed ``/exchange`` response. On a resting order the
        ``oid`` is under ``response.data.statuses[i]``.
        """
        if reduce_only:
            raise HyperliquidVenueError(
                "reduce_only is not supported via pmxt construction (pmxt hardcodes "
                "r=false); use HyperliquidVenue for reduce-only orders"
            )
        if cloid is not None:
            raise HyperliquidVenueError(
                "cloid is not supported via pmxt construction (pmxt emits no 'c' "
                "field); use HyperliquidVenue for client order ids"
            )

        order_type, want_tif = self._resolve_order_type(order_type, tif)

        params = self._build_params(
            asset_id=asset_id,
            is_buy=is_buy,
            size=size,
            limit_px=limit_px,
            order_type=order_type,
            market_id=market_id,
        )
        action = self._sidecar.build_order(params)
        self._assert_built(
            action,
            asset_id=asset_id,
            is_buy=is_buy,
            size=size,
            limit_px=limit_px,
            want_tif=want_tif,
        )

        nonce = now_ms()
        signature = self._signer.sign_l1_action(
            action, nonce, self.is_mainnet, vault_address=self.vault_address
        )
        return self._native.submit_action(action, signature, nonce)

    def cancel_order(self, *, order_id: int, asset_id: int) -> Dict[str, Any]:
        """Cancel a resting order by venue order id.

        Cancels are built natively — pmxt's construction surface adds nothing
        here (no builder attribution, no wire subtleties), so this goes through
        the native cancel path with a raw asset id.
        """
        from simmer_sdk.hyperliquid_signing import build_cancel_action

        action = build_cancel_action(asset_id, order_id)
        nonce = now_ms()
        signature = self._signer.sign_l1_action(
            action, nonce, self.is_mainnet, vault_address=self.vault_address
        )
        return self._native.submit_action(action, signature, nonce)

    def get_positions(self, address: Optional[str] = None) -> List[Dict[str, Any]]:
        """Open positions for ``address`` (defaults to the account-of-record)."""
        return self._native.get_positions(address)

    def get_balances(self, address: Optional[str] = None) -> Dict[str, Any]:
        """Collateral summary for ``address`` (defaults to account-of-record)."""
        return self._native.get_balances(address)

    # ---- HL-specific extras (delegated) -----------------------------------

    def get_open_orders(self, address: Optional[str] = None) -> List[Dict[str, Any]]:
        """Resting open orders for ``address``."""
        return self._native.get_open_orders(address)

    # ---- internals --------------------------------------------------------

    @staticmethod
    def _resolve_order_type(order_type: str, tif: Optional[str]) -> tuple:
        """Normalize (order_type, tif) to a single pmxt type + expected tif."""
        if order_type not in _TIF_BY_ORDER_TYPE:
            raise HyperliquidVenueError(
                f"order_type must be one of {sorted(_TIF_BY_ORDER_TYPE)}, got {order_type!r}"
            )
        want_tif = _TIF_BY_ORDER_TYPE[order_type]
        if tif is None:
            return order_type, want_tif
        if tif not in _ORDER_TYPE_BY_TIF:
            raise HyperliquidVenueError(
                f"tif {tif!r} is not reachable via pmxt construction "
                f"(only {sorted(_ORDER_TYPE_BY_TIF)}); use HyperliquidVenue"
            )
        if tif != want_tif:
            raise HyperliquidVenueError(
                f"conflicting order_type={order_type!r} (tif {want_tif}) and tif={tif!r}"
            )
        return order_type, want_tif

    def _build_params(
        self,
        *,
        asset_id: int,
        is_buy: bool,
        size: float,
        limit_px: float,
        order_type: str,
        market_id: Optional[str],
    ) -> Dict[str, Any]:
        """Map our arguments onto pmxt ``CreateOrderParams``.

        ``builder`` is a STRING address and ``builderFee`` a separate int; the
        nested ``{b, f}`` object is buildOrder's OUTPUT shape, never its input.
        """
        params: Dict[str, Any] = {
            "outcomeId": str(asset_id),
            "side": "buy" if is_buy else "sell",
            "type": order_type,
            "amount": size,
            "price": limit_px,
            "builder": self.builder_address,
            "builderFee": self.builder_fee_tenths_bp,
        }
        if market_id is not None:
            params["marketId"] = market_id
        return params

    def _assert_built(
        self,
        action: Dict[str, Any],
        *,
        asset_id: int,
        is_buy: bool,
        size: float,
        limit_px: float,
        want_tif: str,
    ) -> None:
        """Verify the built action matches the order we asked for, pre-signing.

        Layered on A1's ``assert_built_action`` (header/asset/side/builder) with
        the economic terms it does not cover: price, size and tif. Prices and
        sizes are compared for EXACT numeric equality, which is safe because
        pmxt's ``floatToWire`` raises rather than rounding — a wire value that
        differs from what we asked is drift, not normalization.
        """
        self._sidecar.assert_built_action(
            action,
            asset_id=asset_id,
            is_buy=is_buy,
            builder=self.builder_address,
            builder_fee_tenths_bp=self.builder_fee_tenths_bp,
        )
        wire = action["orders"][0]

        # "Never sign an action we have not verified" means the whole action,
        # not just the fields we happen to check. An upstream addition — a
        # generated cloid, a trigger/TP-SL leg — would otherwise be signed
        # blind. Pinned version + contract tests make an exact key set the
        # right strictness: a bump re-records fixtures, drift refuses to trade.
        # This adapter always requests builder attribution, so the key set is
        # fixed. Comparing lists also re-checks msgpack key ORDER, which is
        # hash-relevant.
        if list(action.keys()) != ["type", "orders", "grouping", "builder"]:
            raise PmxtSidecarError(
                f"unexpected action fields: got {list(action.keys())}, "
                "wanted ['type', 'orders', 'grouping', 'builder']"
            )
        if list(wire.keys()) != ["a", "b", "p", "s", "r", "t"]:
            raise PmxtSidecarError(
                f"unexpected order-wire fields: got {list(wire.keys())}, "
                "wanted ['a', 'b', 'p', 's', 'r', 't']"
            )
        if list((wire.get("t") or {}).keys()) != ["limit"]:
            raise PmxtSidecarError(
                f"unexpected order type — only plain limit orders are built here: {wire.get('t')!r}"
            )

        got_tif = (wire["t"]["limit"] or {}).get("tif")
        if got_tif != want_tif:
            raise PmxtSidecarError(f"tif mismatch: wire {got_tif!r}, wanted {want_tif!r}")

        # `p` catches the omitted-price footgun (pmxt defaults to '0.5').
        if not _wire_equals(wire.get("p"), limit_px):
            raise PmxtSidecarError(f"price mismatch: wire p={wire.get('p')!r}, wanted {limit_px}")
        if not _wire_equals(wire.get("s"), size):
            raise PmxtSidecarError(f"size mismatch: wire s={wire.get('s')!r}, wanted {size}")

        if wire.get("r") is not False:
            raise PmxtSidecarError(f"unexpected reduce_only on wire: r={wire.get('r')!r}")


def _wire_equals(wire_value: Any, requested: float) -> bool:
    """True iff a wire string equals the requested number exactly."""
    if not isinstance(wire_value, str):
        return False
    try:
        return float(wire_value) == float(requested)
    except (TypeError, ValueError):
        return False
