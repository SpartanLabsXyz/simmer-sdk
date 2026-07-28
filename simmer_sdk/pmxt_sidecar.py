"""
pmxt sidecar client — Hyperliquid order CONSTRUCTION only (SIM-4222, A1).

pmxt's role in the Hyperliquid path is deliberately narrow: it *builds* a
venue-correct unsigned order action; signing and submission stay in the SDK
(``HyperliquidSigner`` → direct POST to ``api.hyperliquid.xyz``). No key, no
balance, and no network egress to anything but the local sidecar happens here.

Verified against the published ``pmxt-core`` 2.54.0 dist and two live mainnet
fills (2026-07-28 builder-attribution gate run — see
``simmer/_dev/active/_hyperliquid-rd/pmxt-hl-venue-adapter-spec.md``):

- ``POST {sidecar}/api/hyperliquid/buildOrder`` with a bare JSON object body;
  the generic ``/api/:exchange/:method`` dispatcher passes it as the single
  ``CreateOrderParams`` argument.
- ``CreateOrderParams``: ``outcomeId`` is HL's NUMERIC asset id as a string
  (pmxt ``parseInt``'s it), ``side`` is ``'buy'|'sell'``, ``type: 'market'``
  maps to tif ``Ioc`` **at the supplied price** (an omitted price silently
  defaults to ``'0.5'`` — always supply one), ``builder`` is a STRING address
  and ``builderFee`` a separate int in tenths of a basis point. The nested
  ``{b, f}`` object is buildOrder's *output* shape, never its input.
- Response envelope: ``{"success": true, "data": {"exchange", "params",
  "raw"}}`` where ``raw`` is the unsigned action, msgpack key order already
  correct (``type, orders, grouping, builder``).

The dispatcher route is NOT in pmxt's OpenAPI, so the shape is treated as
unstable: every response is validated and every mismatch raises loudly.
Callers pin the sidecar version and run the contract tests on bumps.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import requests

DEFAULT_TIMEOUT = 15.0

#: The pmxt-core version the contract tests recorded their fixtures against.
#: Bump deliberately: update fixtures, re-run contract tests, dry-run diff.
PINNED_PMXT_VERSION = "2.54.0"


class PmxtSidecarError(Exception):
    """Raised when the sidecar is unreachable, errors, or returns an
    unexpected shape. Never swallowed into a default — a drifted dispatcher
    must fail loudly, not construct a subtly wrong order."""


class PmxtSidecarClient:
    """Thin client for a self-hosted pmxt sidecar (construction only).

    Args:
        base_url: sidecar origin, e.g. ``http://127.0.0.1:8080``. Localhost
            deployment is the intended shape — the sidecar runs next to the
            agent process, unauthenticated, bound to 127.0.0.1.
        timeout: per-request timeout in seconds.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8080", timeout: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout

    # ---- transport -------------------------------------------------------

    def _post(self, path: str, body: Dict[str, Any]) -> Any:
        try:
            resp = requests.post(f"{self.base_url}{path}", json=body, timeout=self._timeout)
        except requests.RequestException as e:
            raise PmxtSidecarError(f"sidecar unreachable at {self.base_url}: {e}") from e
        if resp.status_code != 200:
            raise PmxtSidecarError(
                f"sidecar {path} HTTP {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()

    # ---- surface ---------------------------------------------------------

    def health(self) -> bool:
        """True iff the sidecar answers its health endpoint with status ok."""
        try:
            resp = requests.get(f"{self.base_url}/health", timeout=self._timeout)
        except requests.RequestException as e:
            raise PmxtSidecarError(f"sidecar unreachable at {self.base_url}: {e}") from e
        if resp.status_code != 200:
            return False
        try:
            return resp.json().get("status") == "ok"
        except ValueError:
            return False

    def build_order(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Build an unsigned HL order action via pmxt. Returns the raw action.

        ``params`` is passed through as ``CreateOrderParams`` verbatim — the
        caller (``PmxtHyperliquidVenue``) owns constructing it correctly; this
        layer owns validating what comes back.
        """
        body = self._post("/api/hyperliquid/buildOrder", params)
        if not (isinstance(body, dict) and body.get("success") and isinstance(body.get("data"), dict)):
            raise PmxtSidecarError(
                f"buildOrder unexpected envelope (dispatcher drift?): {_head(body)}"
            )
        action = body["data"].get("raw")
        if not isinstance(action, dict):
            raise PmxtSidecarError(f"buildOrder returned no raw action: {_head(body['data'])}")
        return action

    # ---- verification ----------------------------------------------------

    @staticmethod
    def assert_built_action(
        action: Dict[str, Any],
        *,
        asset_id: int,
        is_buy: bool,
        builder: Optional[str],
        builder_fee_tenths_bp: Optional[int],
    ) -> None:
        """Assert the built action matches what we asked for, BEFORE signing.

        The 2026-07-28 gate run proved the failure texture here: a wrong input
        shape produced an error *naming the builder* while the real problem was
        the whole param mapping. The adapter must never sign an action it has
        not verified — an exact-object check, not substring matching.

        Raises PmxtSidecarError on any mismatch.
        """
        if action.get("type") != "order" or action.get("grouping") != "na":
            raise PmxtSidecarError(f"unexpected action header: {_head(action)}")

        orders = action.get("orders") or []
        if len(orders) != 1:
            raise PmxtSidecarError(f"expected exactly 1 order wire, got {len(orders)}")
        # Types are checked exactly, not just values: Python's `==` treats
        # `1.0 == 1` and `1 == True` as equal, but msgpack encodes them
        # differently, so a type-drifted action would pass this gate and then
        # fail opaquely at submit with a signature/hash error. Catch it here,
        # where the message says what actually changed.
        wire = orders[0]
        if type(wire.get("a")) is not int or wire["a"] != asset_id:
            raise PmxtSidecarError(f"asset mismatch: wire a={wire.get('a')!r}, wanted {asset_id}")
        if type(wire.get("b")) is not bool or wire["b"] != is_buy:
            raise PmxtSidecarError(f"side mismatch: wire b={wire.get('b')!r}, wanted is_buy={is_buy}")

        if builder is None:
            if "builder" in action:
                raise PmxtSidecarError(
                    f"unexpected builder on action when none requested: {action['builder']!r}"
                )
            return
        want = {"b": builder.lower(), "f": builder_fee_tenths_bp}
        got = action.get("builder")
        if (
            not isinstance(got, dict)
            or list(got.keys()) != ["b", "f"]
            or type(got.get("f")) is not int
            or got != want
        ):
            raise PmxtSidecarError(f"builder mismatch: got {got!r}, wanted {want!r}")


def _head(obj: Any, limit: int = 300) -> str:
    """Compact repr for error messages — never dump a full payload."""
    import json

    try:
        s = json.dumps(obj)
    except (TypeError, ValueError):
        s = repr(obj)
    return s[:limit]
