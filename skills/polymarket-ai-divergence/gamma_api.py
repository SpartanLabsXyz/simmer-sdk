"""
Gamma API client for Polymarket market research.

Provides direct access to Polymarket's Gamma API for market metadata,
search, and analytics not available through the Simmer API.

The Gamma API is free, requires no authentication, and has a generous
rate limit of 15,000 requests per 10 seconds.

Usage:
    from simmer_sdk.gamma_api import GammaClient

    gamma = GammaClient()

    # Search for markets
    results = gamma.search("US elections")
    for event in results:
        print(event["title"], len(event["markets"]), "markets")

    # List active markets with filters
    markets = gamma.get_markets(active=True, limit=10)

    # Get a single event by slug
    event = gamma.get_event("will-trump-win-2024")
"""

import json
import logging
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

GAMMA_API_BASE = "https://gamma-api.polymarket.com"


class GammaClient:
    """Lightweight client for Polymarket's Gamma API.

    All methods are synchronous and use only stdlib (no extra deps).
    """

    def __init__(self, base_url: str = GAMMA_API_BASE, timeout: int = 10):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Make a GET request to the Gamma API."""
        url = f"{self.base_url}{path}"
        if params:
            # Filter out None values
            filtered = {k: v for k, v in params.items() if v is not None}
            if filtered:
                url = f"{url}?{urlencode(filtered)}"
        try:
            req = Request(url, headers={"User-Agent": "simmer-sdk"})
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except (HTTPError, URLError, json.JSONDecodeError) as e:
            logger.warning("Gamma API request failed: %s %s", url, e)
            return None

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        active: bool = True,
        closed: bool = False,
        pages: int = 1,
    ) -> List[Dict[str, Any]]:
        """Search Polymarket events and markets via /public-search.

        Args:
            query: Free-text search string.
            active: Only return active events (default True).
            closed: Include closed/resolved markets (default False).
            pages: Number of result pages to fetch (5 events per page).

        Returns:
            List of event dicts, each with a nested ``markets`` list.
        """
        events: Dict[str, Dict] = {}  # dedup by event ID

        for page in range(1, pages + 1):
            params = {
                "q": query,
                "page": page,
                "events_status": "active" if active else None,
                "keep_closed_markets": "1" if closed else "0",
            }
            data = self._get("/public-search", params)
            if not data:
                break

            # Response is {"events": [...], "pagination": {...}}
            event_list = data.get("events", []) if isinstance(data, dict) else data
            if not event_list or not isinstance(event_list, list):
                break

            for event in event_list:
                eid = event.get("id")
                if eid and eid not in events:
                    events[eid] = self._parse_event(event)

        return list(events.values())

    # ------------------------------------------------------------------
    # Markets
    # ------------------------------------------------------------------

    def get_markets(
        self,
        *,
        active: bool = True,
        closed: bool = False,
        limit: int = 50,
        offset: int = 0,
        order: str = "volume24hr",
        ascending: bool = False,
    ) -> List[Dict[str, Any]]:
        """List markets with optional filters, sorted by a field.

        Args:
            active: Only active markets.
            closed: Only closed markets.
            limit: Max results (API caps at ~100).
            offset: Pagination offset.
            order: Sort field (volume24hr, liquidity, volume, startDate, endDate).
            ascending: Sort direction.

        Returns:
            List of parsed market dicts.
        """
        params: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "order": order,
            "ascending": str(ascending).lower(),
        }
        if active:
            params["active"] = "true"
            params["closed"] = "false"
        elif closed:
            params["closed"] = "true"

        data = self._get("/markets", params)
        if not data or not isinstance(data, list):
            return []
        return [self._parse_market(m) for m in data]

    def get_market(self, condition_id: str) -> Optional[Dict[str, Any]]:
        """Get a single market by its condition ID."""
        data = self._get(f"/markets/{condition_id}")
        if not data or not isinstance(data, dict):
            return None
        return self._parse_market(data)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def get_events(
        self,
        *,
        active: bool = True,
        closed: bool = False,
        limit: int = 50,
        offset: int = 0,
        order: str = "volume24hr",
        ascending: bool = False,
    ) -> List[Dict[str, Any]]:
        """List events (groups of related markets).

        Args:
            active: Only active events.
            closed: Only closed events.
            limit: Max results.
            offset: Pagination offset.
            order: Sort field.
            ascending: Sort direction.

        Returns:
            List of parsed event dicts with nested markets.
        """
        params: Dict[str, Any] = {
            "limit": limit,
            "offset": offset,
            "order": order,
            "ascending": str(ascending).lower(),
        }
        if active:
            params["active"] = "true"
            params["closed"] = "false"
        elif closed:
            params["closed"] = "true"

        data = self._get("/events", params)
        if not data or not isinstance(data, list):
            return []
        return [self._parse_event(e) for e in data]

    def get_event(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get a single event by slug.

        Args:
            slug: Event URL slug (e.g. 'will-trump-win-2024').

        Returns:
            Parsed event dict or None.
        """
        data = self._get("/events", {"slug": slug})
        if not data or not isinstance(data, list) or len(data) == 0:
            return None
        return self._parse_event(data[0])

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_float(val: Any, default: float = 0.0) -> float:
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _parse_json_field(val: Any) -> Any:
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return val
        return val

    def _parse_market(self, data: dict) -> Dict[str, Any]:
        """Parse a raw Gamma API market into a clean dict."""
        outcomes = self._parse_json_field(data.get("outcomes", []))
        outcome_prices_raw = self._parse_json_field(data.get("outcomePrices", []))
        clob_token_ids = self._parse_json_field(data.get("clobTokenIds", []))

        outcome_prices = []
        for p in (outcome_prices_raw if isinstance(outcome_prices_raw, list) else []):
            outcome_prices.append(self._safe_float(p, 0.5))

        yes_price = outcome_prices[0] if outcome_prices else 0.5
        no_price = outcome_prices[1] if len(outcome_prices) > 1 else 1.0 - yes_price

        return {
            "id": data.get("id"),
            "condition_id": data.get("conditionId", ""),
            "slug": data.get("slug", ""),
            "question": data.get("question", ""),
            "description": data.get("description", ""),
            "category": data.get("category", ""),
            "end_date": data.get("endDate", ""),
            "outcomes": outcomes if isinstance(outcomes, list) else [],
            "outcome_prices": outcome_prices,
            "yes_price": yes_price,
            "no_price": no_price,
            "clob_token_ids": clob_token_ids if isinstance(clob_token_ids, list) else [],
            "active": bool(data.get("active", False)),
            "closed": bool(data.get("closed", False)),
            "volume": self._safe_float(data.get("volume")),
            "volume_24h": self._safe_float(data.get("volume24hr")),
            "liquidity": self._safe_float(data.get("liquidity")),
            "one_day_price_change": self._safe_float(data.get("oneDayPriceChange")),
            "one_week_price_change": self._safe_float(data.get("oneWeekPriceChange")),
            "one_month_price_change": self._safe_float(data.get("oneMonthPriceChange")),
            "image_url": data.get("image"),
            "neg_risk": bool(
                data.get("neg_risk", False)
                or data.get("negRisk", False)
                or data.get("enableNegRisk", False)
            ),
            "featured": bool(data.get("featured", False)),
            "competitive": bool(data.get("competitive", False)),
            "is_new": bool(data.get("new", False)),
            "group_item_title": data.get("groupItemTitle"),
            "tags": data.get("tags", []),
        }

    def _parse_event(self, data: dict) -> Dict[str, Any]:
        """Parse a raw Gamma API event (with nested markets) into a clean dict."""
        raw_markets = data.get("markets", [])
        markets = [self._parse_market(m) for m in raw_markets] if raw_markets else []

        return {
            "id": data.get("id"),
            "slug": data.get("slug", ""),
            "title": data.get("title", ""),
            "description": data.get("description", ""),
            "category": data.get("category", ""),
            "end_date": data.get("endDate", ""),
            "active": bool(data.get("active", False)),
            "closed": bool(data.get("closed", False)),
            "volume": self._safe_float(data.get("volume")),
            "volume_24h": self._safe_float(data.get("volume24hr")),
            "liquidity": self._safe_float(data.get("liquidity")),
            "image_url": data.get("image"),
            "neg_risk": bool(
                data.get("neg_risk", False)
                or data.get("negRisk", False)
                or data.get("enableNegRisk", False)
            ),
            "tags": data.get("tags", []),
            "markets": markets,
            "market_count": len(markets),
        }
