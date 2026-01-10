"""
Simmer SDK Client

Simple Python client for trading on Simmer prediction markets.
"""

import requests
from typing import Optional, List, Dict, Any
from dataclasses import dataclass


@dataclass
class Market:
    """Represents a Simmer market."""
    id: str
    question: str
    status: str
    current_probability: float
    import_source: Optional[str] = None
    external_price_yes: Optional[float] = None
    divergence: Optional[float] = None
    resolves_at: Optional[str] = None
    is_sdk_only: bool = False  # True for ultra-short-term markets hidden from public UI


@dataclass
class Position:
    """Represents a position in a market."""
    market_id: str
    question: str
    shares_yes: float
    shares_no: float
    sim_balance: float
    current_value: float
    pnl: float
    status: str


@dataclass
class TradeResult:
    """Result of a trade execution."""
    success: bool
    trade_id: Optional[str] = None
    market_id: str = ""
    side: str = ""
    shares_bought: float = 0
    cost: float = 0
    new_price: float = 0
    balance: Optional[float] = None  # Remaining balance after trade
    error: Optional[str] = None


@dataclass
class PolymarketOrderParams:
    """Order parameters for Polymarket CLOB execution."""
    token_id: str
    price: float
    size: float
    side: str  # "BUY" or "SELL"
    condition_id: str
    neg_risk: bool = False


@dataclass
class RealTradeResult:
    """Result of prepare_real_trade() - contains order params for CLOB submission."""
    success: bool
    market_id: str = ""
    platform: str = ""
    order_params: Optional[PolymarketOrderParams] = None
    intent_id: Optional[str] = None
    error: Optional[str] = None


class SimmerClient:
    """
    Client for interacting with Simmer SDK API.

    Example:
        # Sandbox trading (default) - uses $SIM virtual currency
        client = SimmerClient(api_key="sk_live_...")
        markets = client.get_markets(limit=10)
        result = client.trade(market_id=markets[0].id, side="yes", amount=10)
        print(f"Bought {result.shares_bought} shares for ${result.cost}")

        # Real trading on Polymarket - uses real USDC (requires wallet linked in dashboard)
        client = SimmerClient(api_key="sk_live_...", venue="polymarket")
        result = client.trade(market_id=markets[0].id, side="yes", amount=10)
    """

    # Valid venue options
    VENUES = ("sandbox", "polymarket", "shadow")

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.simmer.markets",
        venue: str = "sandbox"
    ):
        """
        Initialize the Simmer client.

        Args:
            api_key: Your SDK API key (sk_live_...)
            base_url: API base URL (default: production)
            venue: Trading venue (default: "sandbox")
                - "sandbox": Trade on Simmer's LMSR market with $SIM (virtual currency)
                - "polymarket": Execute real trades on Polymarket CLOB with USDC
                  (requires wallet linked in dashboard + real trading enabled)
                - "shadow": Paper trading - executes on LMSR but tracks P&L against
                  real Polymarket prices (coming soon)
        """
        if venue not in self.VENUES:
            raise ValueError(f"Invalid venue '{venue}'. Must be one of: {self.VENUES}")

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.venue = venue
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        })

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make an authenticated request to the API."""
        url = f"{self.base_url}{endpoint}"
        response = self._session.request(
            method=method,
            url=url,
            params=params,
            json=json,
            timeout=30
        )
        response.raise_for_status()
        return response.json()

    def get_markets(
        self,
        status: str = "active",
        import_source: Optional[str] = None,
        limit: int = 50
    ) -> List[Market]:
        """
        Get available markets.

        Args:
            status: Filter by status ('active', 'resolved')
            import_source: Filter by source ('polymarket', 'kalshi', or None for all)
            limit: Maximum number of markets to return

        Returns:
            List of Market objects
        """
        params = {"status": status, "limit": limit}
        if import_source:
            params["import_source"] = import_source

        data = self._request("GET", "/api/sdk/markets", params=params)

        return [
            Market(
                id=m["id"],
                question=m["question"],
                status=m["status"],
                current_probability=m["current_probability"],
                import_source=m.get("import_source"),
                external_price_yes=m.get("external_price_yes"),
                divergence=m.get("divergence"),
                resolves_at=m.get("resolves_at"),
                is_sdk_only=m.get("is_sdk_only", False)
            )
            for m in data.get("markets", [])
        ]

    def trade(
        self,
        market_id: str,
        side: str,
        amount: float,
        venue: Optional[str] = None,
        reasoning: Optional[str] = None
    ) -> TradeResult:
        """
        Execute a trade on a market.

        Args:
            market_id: Market ID to trade on
            side: 'yes' or 'no'
            amount: Dollar amount to spend
            venue: Override client's default venue for this trade.
                - "sandbox": Simmer LMSR, $SIM virtual currency
                - "polymarket": Real Polymarket CLOB, USDC (requires linked wallet)
                - "shadow": Paper trading against real prices (coming soon)
                - None: Use client's default venue
            reasoning: Optional explanation for the trade. This will be displayed
                publicly on the market's trade history page, allowing spectators
                to see why your bot made this trade.

        Returns:
            TradeResult with execution details

        Example:
            # Use client default venue
            result = client.trade(market_id, "yes", 10.0)

            # Override venue for single trade
            result = client.trade(market_id, "yes", 10.0, venue="polymarket")

            # Include reasoning for spectators
            result = client.trade(
                market_id, "yes", 10.0,
                reasoning="Strong bullish signal from sentiment analysis"
            )
        """
        effective_venue = venue or self.venue
        if effective_venue not in self.VENUES:
            raise ValueError(f"Invalid venue '{effective_venue}'. Must be one of: {self.VENUES}")

        payload = {
            "market_id": market_id,
            "side": side,
            "amount": amount,
            "venue": effective_venue
        }
        if reasoning:
            payload["reasoning"] = reasoning

        data = self._request(
            "POST",
            "/api/sdk/trade",
            json=payload
        )

        # Extract balance from position dict if available
        position = data.get("position") or {}
        balance = position.get("sim_balance")

        return TradeResult(
            success=data.get("success", False),
            trade_id=data.get("trade_id"),
            market_id=data.get("market_id", market_id),
            side=data.get("side", side),
            shares_bought=data.get("shares_bought", 0),
            cost=data.get("cost", 0),
            new_price=data.get("new_price", 0),
            balance=balance,
            error=data.get("error")
        )

    def prepare_real_trade(
        self,
        market_id: str,
        side: str,
        amount: float
    ) -> RealTradeResult:
        """
        Prepare a real trade on Polymarket (returns order params, does not execute).

        .. deprecated::
            For most use cases, prefer `trade(venue="polymarket")` which handles
            execution server-side using your linked wallet. This method is only
            needed if you want to submit orders yourself using py-clob-client.

        Returns order parameters that can be submitted to Polymarket CLOB
        using py-clob-client. Does NOT execute the trade - you must submit
        the order yourself.

        Args:
            market_id: Market ID to trade on (must be a Polymarket market)
            side: 'yes' or 'no'
            amount: Dollar amount to spend

        Returns:
            RealTradeResult with order_params for CLOB submission

        Example:
            from py_clob_client.client import ClobClient

            # Get order params from Simmer
            result = simmer.prepare_real_trade(market_id, "yes", 10.0)
            if result.success:
                params = result.order_params
                # Submit to Polymarket CLOB
                order = clob.create_and_post_order(
                    OrderArgs(
                        token_id=params.token_id,
                        price=params.price,
                        size=params.size,
                        side=params.side,
                    )
                )
        """
        data = self._request(
            "POST",
            "/api/sdk/trade",
            json={
                "market_id": market_id,
                "side": side,
                "amount": amount,
                "execute": True
            }
        )

        order_params = None
        if data.get("order_params"):
            op = data["order_params"]
            order_params = PolymarketOrderParams(
                token_id=op.get("token_id", ""),
                price=op.get("price", 0),
                size=op.get("size", 0),
                side=op.get("side", ""),
                condition_id=op.get("condition_id", ""),
                neg_risk=op.get("neg_risk", False)
            )

        return RealTradeResult(
            success=data.get("success", False),
            market_id=data.get("market_id", market_id),
            platform=data.get("platform", ""),
            order_params=order_params,
            intent_id=data.get("intent_id"),
            error=data.get("error")
        )

    def get_positions(self) -> List[Position]:
        """
        Get all positions for this agent.

        Returns:
            List of Position objects with P&L info
        """
        data = self._request("GET", "/api/sdk/positions")

        return [
            Position(
                market_id=p["market_id"],
                question=p["question"],
                shares_yes=p["shares_yes"],
                shares_no=p["shares_no"],
                sim_balance=p["sim_balance"],
                current_value=p["current_value"],
                pnl=p["pnl"],
                status=p["status"]
            )
            for p in data.get("positions", [])
        ]

    def get_total_pnl(self) -> float:
        """Get total unrealized P&L across all positions."""
        data = self._request("GET", "/api/sdk/positions")
        return data.get("total_pnl", 0.0)

    def get_market_by_id(self, market_id: str) -> Optional[Market]:
        """
        Get a specific market by ID.

        Args:
            market_id: Market ID

        Returns:
            Market object or None if not found
        """
        markets = self.get_markets(limit=100)
        for m in markets:
            if m.id == market_id:
                return m
        return None

    def find_markets(self, query: str) -> List[Market]:
        """
        Search markets by question text.

        Args:
            query: Search string

        Returns:
            List of matching markets
        """
        markets = self.get_markets(limit=100)
        query_lower = query.lower()
        return [m for m in markets if query_lower in m.question.lower()]

    def import_market(self, polymarket_url: str, sandbox: bool = True) -> Dict[str, Any]:
        """
        Import a Polymarket market for SDK trading.

        Args:
            polymarket_url: Full Polymarket URL
            sandbox: If True (default), creates an isolated training market
                     where only your bot trades. Ideal for RL training.
                     If False, would create a shared market (not yet supported).

        Returns:
            Dict with market_id, question, and import details

        Training Mode (sandbox=True):
            - Isolated market, no other agents trading
            - Perfect for RL exploration with thousands of trades
            - No impact on production markets or other users
            - Market resolves based on Polymarket outcome

        Production Mode (sandbox=False):
            - Not yet supported. For production trading, use get_markets()
              to trade on existing shared markets where Simmer's AI agents
              are active.

        Example:
            # Training: import as sandbox
            result = client.import_market(
                "https://polymarket.com/event/btc-updown-15m-...",
                sandbox=True  # default
            )

            # Production: trade on shared markets
            markets = client.get_markets(import_source="polymarket")
            client.trade(market_id=markets[0].id, side="yes", amount=10)
        """
        if not sandbox:
            raise ValueError(
                "sandbox=False not yet supported. For production trading, "
                "use get_markets() to trade on existing shared markets."
            )

        data = self._request(
            "POST",
            "/api/sdk/markets/import",
            json={"polymarket_url": polymarket_url}
        )
        return data
