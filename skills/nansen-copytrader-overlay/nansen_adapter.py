"""
Nansen API adapter for Simmer.

Direct HTTPS client for the Nansen REST API.  All calls are read-only
(research/prediction-market endpoints); no trading happens here.

Responsibilities:
- POST to https://api.nansen.ai/api/v1/... and normalise JSON responses
- Retry transient failures (up to MAX_RETRIES) with exponential backoff
- Rate-limit between calls (INTER_CALL_DELAY_S)
- Surface a *specific* NansenError subclass per failure mode

Design constraints:
- No dependency on the local `nansen` CLI — this skill ships to users who
  won't have that binary installed.  Auth is the NANSEN_API_KEY env var,
  sent as the `apiKey` header (not `X-Api-Key`, not `Authorization: Bearer`).
- Standard library only (urllib) so the skill installs with zero deps.
- All endpoints are POST.  GET returns 405.
- A browser-like User-Agent is required; Cloudflare blocks the default
  python-urllib/curl UA.
- All public functions return plain dicts/lists (no dataclasses) so callers
  can serialise freely.

Failure modes are distinct and must not be conflated (each maps to its own
exception below) — observed against the live API:
    401  NansenAuthError            missing/invalid apiKey
    402  NansenPaymentRequiredError  x402 paywall — request had NO apiKey
    403  NansenAccessDeniedError     request refused: plan/entitlement OR balance
    404  NansenRouteError            wrong path
    422  NansenRequestError          malformed body (e.g. missing `date`)
    429/5xx                          transient — retried

On 403 specifically: an access problem and a cost problem look identical
from here, and this integration has been bitten in both directions. The
API does not say which one it is, so neither does this module — check
`GET /api/v1/account` (free) to tell them apart before concluding anything.
"""

import json
import os
import time
import datetime
import logging
import urllib.request
import urllib.error
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
INTER_CALL_DELAY_S = 1.5     # pause between wallet calls to avoid rate-limits
HTTP_TIMEOUT_S = 60          # address-summary can take >30s on busy wallets

NANSEN_API_BASE = os.environ.get(
    "NANSEN_API_BASE", "https://api.nansen.ai/api/v1"
).rstrip("/")

# Cloudflare in front of api.nansen.ai rejects the default urllib UA.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class NansenError(Exception):
    """Base class for all Nansen API failures."""
    pass


class NansenAuthError(NansenError):
    """401 — the apiKey header was missing, malformed, or rejected."""
    pass


class NansenPaymentRequiredError(NansenError):
    """
    402 — the x402 paywall answered instead of the API.

    This means the request carried NO apiKey at all.  It is NOT a quota
    problem: an authenticated request never sees a 402.  Treat it as an
    auth/config bug, not as "the data is gated".
    """
    pass


class NansenAccessDeniedError(NansenError):
    """
    403 — the API refused the call. Cause is ambiguous by design of the API.

    Two very different things produce this and the response does not
    distinguish them:
      - entitlement: the account's plan doesn't include this endpoint
      - balance:     the call costs more credits than the account has left

    Do NOT read this as "credits exhausted" on its own. Observed while
    porting this module: `profiler/address/labels` returned 403 on a
    `plan: free` key with 27 credits remaining, while cheaper
    prediction-market endpoints on the same key still returned 200. Since
    label lookups run 100-500 credits, balance alone explains that — but a
    plan exclusion would look exactly the same, and on a well-funded
    account it could only be entitlement.

    `GET /api/v1/account` returns {"plan", "credits_remaining"} and is free
    and uncounted; call it before concluding which one you have.
    """
    pass


# Back-compat alias. The old name asserted a cause the API never reports.
NansenCreditsExhaustedError = NansenAccessDeniedError


class NansenRouteError(NansenError):
    """
    404 — the path is wrong (not a data or permission issue).

    Note this is only reliable *with* a valid key. Unauthenticated, the API
    answers 401 for every path including nonexistent ones, so a bare request
    cannot be used to test whether a route exists.
    """
    pass


class NansenRequestError(NansenError):
    """422 — the request body was malformed or missing a required field."""
    pass


class CreditGuardExceeded(NansenError):
    """Raised when a CreditGuard's credit budget would be exceeded mid-run."""
    pass


def _api_key() -> str:
    """
    Return the Nansen API key.

    Reads NANSEN_API_KEY from the environment; falls back to a local .env
    file so a bring-your-own-key checkout works without extra setup.
    """
    key = os.environ.get("NANSEN_API_KEY", "").strip()
    if key:
        return key

    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(env_path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("NANSEN_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------------------
# Credit guard — hard cap on Nansen credits per run, with a TTL cache so
# repeated lookups (e.g. the same market_id across several leaders) don't
# both count against the budget and re-spend credits.
#
# Credit costs measured live 2026-08-11:
#   pnl-by-market     5 credits   (Nansen docs say 1 — they are wrong)
#   top-holders       5 credits
#   address-summary   1 credit
#   pnl-by-address    1 credit
#   trades-by-address 1 credit
#   market-screener   1 credit
#   account           0 credits   (free; no guard needed)
# ---------------------------------------------------------------------------

CREDITS_PNL_BY_MARKET = 5   # measured live 2026-08-11
CREDITS_TOP_HOLDERS = 5     # measured live 2026-08-11
CREDITS_DEFAULT = 1         # all other prediction-market endpoints

DEFAULT_MAX_CREDITS_PER_RUN = 45   # ~1 pnl-by-market + ~1 top-holders + 35 single-credit calls
DEFAULT_CACHE_TTL_S = 300.0  # 5 minutes


class CreditGuard:
    """
    Tracks a hard credit budget and a short-lived cache for one enrichment run.

    Every module in this repo that calls into `nansen_adapter` should be
    given a CreditGuard instance (or construct a default one) rather than
    calling `_post` unbounded — credits, not rate limits, are the binding
    constraint on this account (see README "Credits" section).

    Endpoints cost different numbers of credits per call (see CREDITS_* constants
    above). The guard tracks total credits spent, not call count, so the cap
    means what a user thinks it means.
    """

    def __init__(self, max_credits: int = DEFAULT_MAX_CREDITS_PER_RUN,
                 cache_ttl_s: float = DEFAULT_CACHE_TTL_S):
        # A nonpositive budget is never what a caller means. It makes the very
        # first request raise CreditGuardExceeded, which surfaces as a traceback
        # rather than the tagged CREDIT_GUARD_EXHAUSTED recovery path the
        # overlays implement. Fail here, where the number came from.
        if max_credits < 1:
            raise ValueError(
                f"max_credits must be >= 1, got {max_credits}. A budget below 1 "
                "trips the guard on the first call."
            )
        self.max_credits = max_credits
        self.cache_ttl_s = cache_ttl_s
        self.credits_spent = 0
        self._cache: dict[tuple, tuple[float, Any]] = {}

    def get_cached(self, key: tuple) -> Optional[Any]:
        hit = self._cache.get(key)
        if hit is None:
            return None
        ts, value = hit
        if (time.time() - ts) >= self.cache_ttl_s:
            return None
        return value

    def consume(self, key: tuple, cost: int = CREDITS_DEFAULT) -> None:
        """Charge `cost` credits against the budget. Raises if budget would be exceeded."""
        if self.credits_spent + cost > self.max_credits:
            raise CreditGuardExceeded(
                f"credit guard tripped: max_credits={self.max_credits} would be exceeded "
                f"(spent={self.credits_spent}, next cost={cost}, key={key[:3]})"
            )
        self.credits_spent += cost

    def put_cache(self, key: tuple, value: Any) -> None:
        self._cache[key] = (time.time(), value)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Status codes that are worth retrying — everything else is a hard failure
# that retrying would only burn time (and, for 403, credits) on.
_TRANSIENT_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

_STATUS_EXCEPTIONS = {
    401: NansenAuthError,
    402: NansenPaymentRequiredError,
    403: NansenAccessDeniedError,
    404: NansenRouteError,
    422: NansenRequestError,
}


def _post(path: str, body: Optional[dict], retries: int = MAX_RETRIES,
          guard: Optional[CreditGuard] = None, method: str = "POST",
          cost: int = CREDITS_DEFAULT) -> Any:
    """
    POST `body` to `NANSEN_API_BASE/path` and return parsed JSON.

    If `guard` is given: a cache hit within `guard.cache_ttl_s` short-circuits
    the call entirely (no credits spent, no budget consumed); otherwise `cost`
    credits are charged against `guard.max_credits` before the call runs, raising
    CreditGuardExceeded if the budget would be exceeded.

    Raises the NansenError subclass matching the HTTP status (see module
    docstring). Transient statuses are retried with exponential backoff.
    """
    cache_key = (path, json.dumps(body, sort_keys=True))
    if guard is not None:
        cached = guard.get_cached(cache_key)
        if cached is not None:
            return cached
        guard.consume(cache_key, cost)

    key = _api_key()
    if not key:
        # Fail loudly here rather than letting the request go out bare and
        # come back as a confusing 402 x402 paywall response.
        raise NansenAuthError(
            "NANSEN_API_KEY is not set — set the env var (or add it to .env). "
            "An unauthenticated request returns a 402 x402 paywall error, "
            "which is an auth bug, not a quota problem."
        )

    url = f"{NANSEN_API_BASE}/{path.lstrip('/')}"
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "apiKey": key,
        "Content-Type": "application/json",
        "User-Agent": os.environ.get("NANSEN_USER_AGENT", DEFAULT_USER_AGENT),
        "Accept": "application/json",
    }

    last_err: Optional[Exception] = None

    for attempt in range(retries):
        if attempt > 0:
            time.sleep(2 ** attempt)  # exponential backoff

        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                raw = resp.read().decode("utf-8")
            parsed = json.loads(raw)
            if guard is not None:
                guard.put_cache(cache_key, parsed)
            return parsed

        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass

            exc_cls = _STATUS_EXCEPTIONS.get(e.code)
            if exc_cls is not None:
                # Hard failure — retrying cannot change the outcome.
                raise exc_cls(f"{e.code} on {path}: {detail}") from None

            last_err = NansenError(f"HTTP {e.code} on {path}: {detail}")
            if e.code not in _TRANSIENT_STATUSES:
                raise last_err from None
            logger.warning("nansen %s transient %d (attempt %d/%d)",
                           path, e.code, attempt + 1, retries)

        except urllib.error.URLError as e:
            last_err = NansenError(f"network error calling {path}: {e.reason}")
            logger.warning("nansen %s network error (attempt %d/%d): %s",
                           path, attempt + 1, retries, e.reason)
        except TimeoutError:
            last_err = NansenError(f"nansen timed out after {HTTP_TIMEOUT_S}s: {path}")
            logger.warning("nansen %s timeout (attempt %d/%d)", path, attempt + 1, retries)
        except json.JSONDecodeError as e:
            # Not transient — a 200 with non-JSON means something structural.
            raise NansenError(f"nansen returned non-JSON from {path}: {e}") from None

    raise last_err or NansenError(f"nansen call failed: {path}")


def _rows(raw: Any) -> list[dict]:
    """Unwrap the standard {"pagination": {...}, "data": [...]} envelope."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        data = raw.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
    return []


def _paginate(limit: int) -> dict:
    return {"page": 1, "per_page": limit}


def _order_by(field: str, direction: str = "DESC") -> list[dict]:
    return [{"direction": direction, "field": field}]


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Public API
#
# Endpoint paths and request bodies below are verified against the live API.
# ---------------------------------------------------------------------------

def account() -> dict:
    """
    Return {"plan": str, "credits_remaining": int} for the current key.

    GET /account, and it is free — it does not spend credits and takes no
    CreditGuard. Use it to tell an entitlement 403 from a balance 403
    before assuming either (see NansenAccessDeniedError).
    """
    raw = _post("account", None, method="GET")
    return raw if isinstance(raw, dict) else {}


def pnl_by_market(market_id: str, limit: int = 50,
                   guard: Optional[CreditGuard] = None) -> list[dict]:
    """
    Return PnL leaderboard for a single Polymarket market — who is actually
    profitable IN THIS MARKET, ranked by realised + unrealised PnL.

    This is the core copytrader signal: it is Polymarket-market-specific,
    unlike pnl_by_address/trades_by_address which reflect a wallet's whole
    trading history and may not say anything about this market.

    Rows are keyed by the wallet's PROXY address, and each row also carries
    `owner_address` for free — though that field is frequently the literal
    placeholder "0x" (see `owner_address_from_row`).

    Fields per entry:
        address, owner_address, side_held, net_buy_cost_usd,
        net_sell_proceeds_usd, redemption_value_usd, unrealized_value_usd,
        total_pnl_usd, question, market_id, market_resolved
    """
    raw = _post("prediction-market/pnl-by-market", {
        "market_id": str(market_id),
        "order_by": _order_by("total_pnl_usd"),
        "pagination": _paginate(limit),
    }, guard=guard, cost=CREDITS_PNL_BY_MARKET)
    return [_normalise_pnl_row(r) for r in _rows(raw)]


def pnl_by_address(address: str, limit: int = 30,
                    guard: Optional[CreditGuard] = None) -> list[dict]:
    """
    Return per-market PnL breakdown for a wallet's PROXY address.

    Secondary/general signal — broad wallet history, not market-specific.
    Prefer pnl_by_market() as the primary copytrader ranking signal.

    Fields per entry:
        market_id, question, side_held, net_buy_cost_usd,
        unrealized_value_usd, total_pnl_usd, market_resolved
    """
    raw = _post("prediction-market/pnl-by-address", {
        "address": address,
        "order_by": _order_by("total_pnl_usd"),
        "pagination": _paginate(limit),
    }, guard=guard)
    return [_normalise_pnl_address_row(r) for r in _rows(raw)]


def trades_by_address(address: str, limit: int = 100,
                       guard: Optional[CreditGuard] = None) -> list[dict]:
    """
    Return trade history for a wallet's PROXY address.

    Fields per entry:
        timestamp, market_id, market_question, side, price,
        size, usdc_value, taker_action, buyer, seller
    """
    raw = _post("prediction-market/trades-by-address", {
        "address": address,
        "order_by": _order_by("timestamp"),
        "pagination": _paginate(limit),
    }, guard=guard)
    return [_normalise_trade_row(r) for r in _rows(raw)]


def trades_by_market(market_id: str, limit: int = 50,
                      guard: Optional[CreditGuard] = None) -> list[dict]:
    """Return recent trades for a market."""
    raw = _post("prediction-market/trades-by-market", {
        "market_id": str(market_id),
        "order_by": _order_by("timestamp"),
        "pagination": _paginate(limit),
    }, guard=guard)
    return [_normalise_trade_row(r) for r in _rows(raw)]


def historical_balances(address: str, chain: str = "polygon",
                         days: int = 365, limit: int = 100,
                         guard: Optional[CreditGuard] = None) -> list[dict]:
    """
    Return historical token balances for a wallet's OWNER (signing) address.

    Profiler endpoints track general on-chain wallet activity, indexed by
    the EOA that signs — not the Polymarket proxy contract. Pass the owner
    address, not the proxy address (see README proxy/owner note).

    `date` is a REQUIRED field on this endpoint; omitting it returns 422.

    Fields: block_timestamp, value_usd, token_symbol
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    raw = _post("profiler/address/historical-balances", {
        "address": address,
        "chain": chain,
        "date": {
            "from": (now - datetime.timedelta(days=days)).strftime("%Y-%m-%d"),
            "to": now.strftime("%Y-%m-%d"),
        },
        "pagination": _paginate(limit),
    }, guard=guard)
    return _rows(raw)


def address_summary(address: str, chain: str = "polygon",
                    guard: Optional[CreditGuard] = None) -> dict:
    """
    Return a one-call wallet summary using the Polymarket-specific endpoint.

    Takes the wallet's PROXY address. This endpoint is Polymarket-indexed:
    passing the OWNER (EOA) address returns a valid-looking row with every
    metric zeroed, which silently fails any downstream history gate. Verified
    on a live wallet: proxy -> 6375 markets_traded, owner -> 0.

    Kept `chain` arg for backward compatibility; endpoint is address-only.

    Fields: address, first_seen, wallet_age_days, realized_pnl_usd,
        unrealized_pnl_usd, total_pnl_usd, markets_won, markets_traded,
        win_rate, p2p_tokens_sent, p2p_tokens_received
    """
    raw = _post("prediction-market/address-summary", {
        "address": address,
    }, guard=guard)

    rows = _rows(raw)
    row = rows[0] if rows else {}
    return _normalise_address_summary(row, address)


# NOTE: there is deliberately no `profiler_labels` wrapper here.
# This skill's framing is realised PnL only — it does not market on entity
# or smart-money labels, and Nansen's labels aren't Polymarket-specific.
# Shipping an unverified stub for an endpoint we've decided not to use is
# worse than not shipping it. Cheap to add back if Nansen ever exposes
# prediction-market labels.


def top_holders(market_id: str, limit: int = 30,
                 guard: Optional[CreditGuard] = None) -> list[dict]:
    """
    Return top position holders for a live market.

    Fields: market_id, outcome_index, address, owner_address, side,
        position_size, avg_entry_price, current_price, unrealized_pnl_usd
    """
    raw = _post("prediction-market/top-holders", {
        "market_id": str(market_id),
        "order_by": _order_by("position_size"),
        "pagination": _paginate(limit),
    }, guard=guard, cost=CREDITS_TOP_HOLDERS)
    return _rows(raw)


def market_screener(sort_by: str = "volume_24hr", limit: int = 20,
                    status: str = "active", query: Optional[str] = None,
                    guard: Optional[CreditGuard] = None) -> list[dict]:
    """
    Return markets from the Polymarket screener.

    Fields include: market_id, question, slug, event_id, event_title,
        active, closed, end_date, tags, volume, volume_24hr, liquidity,
        open_interest, best_bid, best_ask
    """
    body: dict[str, Any] = {
        "order_by": _order_by(sort_by),
        "status": status,
        "pagination": _paginate(limit),
    }
    if query:
        body["query"] = query
    raw = _post("prediction-market/market-screener", body, guard=guard)
    return _rows(raw)


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalise_pnl_row(r: dict) -> dict:
    return {
        "address": r.get("address") or r.get("proxy_wallet") or "",
        "owner_address": _clean_owner(r.get("owner_address") or r.get("ownerAddress")),
        "side_held": (r.get("side_held") or r.get("sideHeld") or "").lower(),
        "net_buy_cost_usd": _safe_float(r.get("net_buy_cost_usd") or r.get("netBuyCostUsd")),
        "unrealized_value_usd": _safe_float(r.get("unrealized_value_usd") or r.get("unrealizedValueUsd")),
        "total_pnl_usd": _safe_float(r.get("total_pnl_usd") or r.get("totalPnlUsd")),
        "market_resolved": bool(r.get("market_resolved") or r.get("marketResolved")),
        "question": r.get("question") or "",
        "market_id": str(r.get("market_id") or r.get("marketId") or ""),
    }


def _normalise_pnl_address_row(r: dict) -> dict:
    return {
        "market_id": str(r.get("market_id") or r.get("marketId") or ""),
        "question": r.get("question") or r.get("market_question") or "",
        "side_held": (r.get("side_held") or r.get("sideHeld") or "").lower(),
        "net_buy_cost_usd": _safe_float(r.get("net_buy_cost_usd") or r.get("netBuyCostUsd")),
        "unrealized_value_usd": _safe_float(r.get("unrealized_value_usd") or r.get("unrealizedValueUsd")),
        "total_pnl_usd": _safe_float(r.get("total_pnl_usd") or r.get("totalPnlUsd")),
        "market_resolved": bool(r.get("market_resolved") or r.get("marketResolved")),
    }


def _normalise_address_summary(r: dict, fallback_address: str) -> dict:
    """
    Normalise a prediction-market/address-summary row.

    Written against the verified live shape (see
    artifacts/nansen/address_summary_raw.json). This endpoint reports
    `markets_traded` / `markets_won`, NOT `resolved_count` / `avg_roi` —
    `resolved_count` is kept as an alias of markets_traded for callers that
    gate on history depth, and `avg_roi` is not offered by this endpoint at
    all (compute it from pnl_by_address instead).
    """
    r = r or {}
    win_rate = r.get("win_rate")
    markets_traded = int(_safe_float(r.get("markets_traded"), 0))
    return {
        "address": r.get("address") or fallback_address,
        "win_rate": _safe_float(win_rate, None) if win_rate is not None else None,
        "avg_roi": None,  # not exposed by this endpoint
        "markets_traded": markets_traded,
        "markets_won": int(_safe_float(r.get("markets_won"), 0)),
        "resolved_count": markets_traded,  # back-compat alias for history gates
        "wallet_age_days": _safe_float(r.get("wallet_age_days"), None)
                           if r.get("wallet_age_days") is not None else None,
        "first_seen": r.get("first_seen") or "",
        "realized_pnl_usd": _safe_float(r.get("realized_pnl_usd")),
        "unrealized_pnl_usd": _safe_float(r.get("unrealized_pnl_usd")),
        "total_pnl_usd": _safe_float(r.get("total_pnl_usd") or r.get("totalPnlUsd")),
        "p2p_tokens_sent": _safe_float(r.get("p2p_tokens_sent")),
        "p2p_tokens_received": _safe_float(r.get("p2p_tokens_received")),
    }


def _normalise_trade_row(r: dict) -> dict:
    return {
        "timestamp": r.get("timestamp") or r.get("block_timestamp") or "",
        "market_id": str(r.get("market_id") or r.get("marketId") or ""),
        "market_question": r.get("market_question") or r.get("question") or "",
        "side": (r.get("side") or "").lower(),
        "price": _safe_float(r.get("price")),
        "size": _safe_float(r.get("size")),
        "usdc_value": _safe_float(r.get("usdc_value") or r.get("usdcValue")),
        "taker_action": (r.get("taker_action") or r.get("takerAction") or "").lower(),
        "buyer": r.get("buyer") or "",
        "seller": r.get("seller") or "",
    }


# ---------------------------------------------------------------------------
# Proxy vs owner wallet routing
# ---------------------------------------------------------------------------
# Nansen indexes Polymarket (`prediction-market/...`) activity by the PROXY
# wallet — the contract wallet Polymarket trades through. Profiler endpoints
# (`profiler/...`) track general on-chain activity and are indexed by the
# OWNER (signing/EOA) wallet instead.
#
# address_summary is a prediction-market endpoint and therefore takes the
# PROXY address, despite summarising "the wallet".

# Placeholder values the API returns when it has no owner mapping. "0x" is
# by far the most common (30 of 50 rows on a live market) — passing it to a
# profiler endpoint would query a nonexistent wallet and silently return
# empty history, so it must be treated as absent.
_NULL_ADDRESSES = frozenset({
    "",
    "0x",
    "0x0",
    "0x0000000000000000000000000000000000000000",
})


def _clean_owner(value: Optional[str]) -> str:
    """Return a usable owner address, or "" for API placeholders like "0x"."""
    v = (value or "").strip()
    return "" if v.lower() in _NULL_ADDRESSES else v


def get_proxy_address(leader: dict) -> str:
    """Address to use for `prediction-market/...` calls (pnl-by-market,
    pnl-by-address, trades-by-address, trades-by-market, top-holders,
    address-summary)."""
    return leader.get("proxy_address") or leader.get("address") or ""


def get_owner_address(leader: dict, row: Optional[dict] = None) -> str:
    """
    Address to use for `profiler/...` calls (historical-balances).

    Prefers an explicit `owner_address` on the leader dict, then falls back to
    the `owner_address` carried on an already-fetched pnl_by_market /
    top_holders `row` — that field comes back for free on every leaderboard
    row, so callers needn't supply it separately.

    Never falls back to `address`/`proxy_address`: profiler data keyed by the
    wrong wallet type silently returns someone else's (or nobody's) history.
    Placeholder values such as "0x" are treated as absent for the same reason.
    """
    owner = _clean_owner(leader.get("owner_address"))
    if owner:
        return owner
    if row:
        return _clean_owner(row.get("owner_address"))
    return ""


def owner_address_from_row(row: Optional[dict]) -> str:
    """
    Extract a usable owner address from a leaderboard row, or "".

    Note: roughly 60% of live pnl_by_market rows carry the placeholder "0x"
    rather than a real owner, so callers must handle "" as a normal outcome
    and skip profiler enrichment for those wallets rather than erroring.
    """
    return _clean_owner((row or {}).get("owner_address"))


# ---------------------------------------------------------------------------
# Scoring helpers (shared across overlay modules)
# ---------------------------------------------------------------------------

def compute_roi(net_buy_cost_usd: float, total_pnl_usd: float) -> Optional[float]:
    """ROI as a fraction (1.0 = 100%). None if cost <= 0."""
    if net_buy_cost_usd <= 0:
        return None
    return total_pnl_usd / net_buy_cost_usd


def wallet_age_days(balances: list[dict]) -> Optional[float]:
    """
    Days since first non-zero balance record, or None if unknown.
    Expects records sorted by block_timestamp ascending.
    """
    for row in balances:
        v = _safe_float(row.get("value_usd"), -1)
        if v > 0:
            ts = row.get("block_timestamp")
            if not ts:
                continue
            try:
                dt = datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                now = datetime.datetime.now(datetime.timezone.utc)
                return (now - dt).total_seconds() / 86400
            except (ValueError, TypeError):
                continue
    return None


def pnl_quality_score(
    pnl_rows: list[dict],
    min_resolved: int = 3,
) -> dict:
    """
    Compute PnL quality features from per-market PnL history.

    Returns:
        win_rate:       fraction of resolved profitable markets
        avg_roi:        mean ROI across resolved markets (None if < min_resolved)
        consistency:    1 - (std(roi) / (mean(roi) + 1e-9)) clamped [0, 1]
        concentration:  largest single-market PnL / total_pnl (1.0 = one-market wonder)
        recency_days:   days since most recent resolved market
        resolved_count: number of resolved markets in history
    """
    import math

    resolved = [r for r in pnl_rows if r.get("market_resolved")]
    if not resolved:
        return {
            "win_rate": None,
            "avg_roi": None,
            "consistency": None,
            "concentration": None,
            "recency_days": None,
            "resolved_count": 0,
        }

    rois = []
    for r in resolved:
        roi = compute_roi(r["net_buy_cost_usd"], r["total_pnl_usd"])
        if roi is not None:
            rois.append(roi)

    wins = sum(1 for r in resolved if r["total_pnl_usd"] > 0)
    win_rate = wins / len(resolved)

    avg_roi = None
    consistency = None
    if len(rois) >= min_resolved:
        avg_roi = sum(rois) / len(rois)
        if len(rois) >= 2:
            variance = sum((x - avg_roi) ** 2 for x in rois) / len(rois)
            std_roi = math.sqrt(variance)
            consistency = max(0.0, 1.0 - std_roi / (abs(avg_roi) + 1e-9))
            consistency = min(1.0, consistency)
        else:
            consistency = 1.0

    total_pnl = sum(r["total_pnl_usd"] for r in resolved)
    if total_pnl != 0:
        max_market_pnl = max(abs(r["total_pnl_usd"]) for r in resolved)
        concentration = max_market_pnl / (abs(total_pnl) + 1e-9)
        concentration = min(1.0, concentration)
    else:
        concentration = None

    # Recency: find most recent resolved market (we don't have resolution timestamps
    # in pnl_by_address, so we use position in the list as a proxy — Nansen returns
    # most recent first by default)
    recency_days = None  # Would need timestamp field; left as None here

    return {
        "win_rate": win_rate,
        "avg_roi": avg_roi,
        "consistency": consistency,
        "concentration": concentration,
        "recency_days": recency_days,
        "resolved_count": len(resolved),
    }
