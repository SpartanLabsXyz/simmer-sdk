"""
Venue-adapter interface — the common trade-execution contract across venues.

Scoped as part of the Hyperliquid integration per the decision record
(``_dev/reference/venue-adapter-decision.md``, SIM-1017): trade execution was
a venue-specific ``if/elif`` chain; at the third real venue we define the
shared shape rather than add another branch. Hyperliquid (``HyperliquidVenue``)
is the first implementer. Polymarket and Kalshi migrate opportunistically when
their paths are next touched — this is a structural ``Protocol``, so it imposes
no runtime coupling and no forced retrofit.

The four core methods come straight from the decision record:
``place_order``, ``cancel_order``, ``get_positions``, ``get_balances``.
Implementations may add venue-specific extras (order books, agent-key
approval) as plain additional methods.
"""

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class VenueAdapter(Protocol):
    """Common trade-execution surface a venue must provide.

    Return shapes are intentionally ``dict``/``list`` (not dataclasses) so each
    venue can pass through its native response while callers read a documented
    subset of keys. The SDK's higher-level ``TradeResult`` normalization lives
    above this layer.
    """

    #: Stable venue identifier, e.g. "hyperliquid".
    venue: str

    #: The address that signs and holds positions for this adapter instance.
    address: str

    def place_order(
        self,
        *,
        size: float,
        limit_px: float,
        is_buy: bool,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Place an order. Returns the venue response incl. an order id and
        any immediate fill. Venue-specific routing args (market identifier,
        outcome side, time-in-force) come through kwargs."""
        ...

    def cancel_order(self, *, order_id: int, **kwargs: Any) -> Dict[str, Any]:
        """Cancel a resting order by venue order id."""
        ...

    def get_positions(self, address: Optional[str] = None) -> List[Dict[str, Any]]:
        """Open positions for ``address`` (defaults to this adapter's address)."""
        ...

    def get_balances(self, address: Optional[str] = None) -> Dict[str, Any]:
        """Collateral / balance summary for ``address``."""
        ...
