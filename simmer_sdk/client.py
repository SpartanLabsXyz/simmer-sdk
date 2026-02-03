"""
Simmer SDK Client

Simple Python client for trading on Simmer prediction markets.
"""

import os
import logging
import requests
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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
    shares_bought: float = 0  # Actual shares filled (for Polymarket, assumes full fill if matched)
    shares_requested: float = 0  # Shares requested (for partial fill detection)
    order_status: Optional[str] = None  # Polymarket order status: "matched", "live", "delayed"
    cost: float = 0
    new_price: float = 0
    balance: Optional[float] = None  # Remaining balance after trade (simmer only)
    error: Optional[str] = None

    @property
    def fully_filled(self) -> bool:
        """Check if order was fully filled (shares_bought >= shares_requested)."""
        if self.shares_requested <= 0:
            return self.success
        return self.shares_bought >= self.shares_requested


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
        # Simmer trading (default) - uses $SIM virtual currency
        client = SimmerClient(api_key="sk_live_...")
        markets = client.get_markets(limit=10)
        result = client.trade(market_id=markets[0].id, side="yes", amount=10)
        print(f"Bought {result.shares_bought} shares for ${result.cost}")

        # Real trading on Polymarket - uses real USDC (requires wallet linked in dashboard)
        client = SimmerClient(api_key="sk_live_...", venue="polymarket")
        result = client.trade(market_id=markets[0].id, side="yes", amount=10)
    """

    # Valid venue options (sandbox is deprecated alias for simmer)
    VENUES = ("simmer", "sandbox", "polymarket", "kalshi")
    # Valid order types for Polymarket CLOB
    ORDER_TYPES = ("GTC", "GTD", "FOK", "FAK")
    # Private key format: 0x + 64 hex characters (EVM)
    PRIVATE_KEY_LENGTH = 66
    # Environment variable for EVM private key auto-detection (Polymarket)
    PRIVATE_KEY_ENV_VAR = "SIMMER_PRIVATE_KEY"
    # Environment variable for Solana private key (Kalshi via DFlow)
    SOLANA_PRIVATE_KEY_ENV_VAR = "SIMMER_SOLANA_KEY"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.simmer.markets",
        venue: str = "simmer",
        private_key: Optional[str] = None
    ):
        """
        Initialize the Simmer client.

        Args:
            api_key: Your SDK API key (sk_live_...)
            base_url: API base URL (default: production)
            venue: Trading venue (default: "simmer")
                - "simmer": Trade on Simmer's LMSR market with $SIM (virtual currency)
                - "polymarket": Execute real trades on Polymarket CLOB with USDC
                  (requires wallet linked in dashboard + real trading enabled)
                - "kalshi": Execute real trades on Kalshi via DFlow
                  (requires SIMMER_SOLANA_KEY env var with base58 secret key)
                Note: "sandbox" is a deprecated alias for "simmer" (will be removed in 30 days)
            private_key: Optional EVM wallet private key for Polymarket trading.
                When provided, orders are signed locally instead of server-side.
                This enables trading with your own Polymarket wallet.

                If not provided, the SDK will auto-detect from the SIMMER_PRIVATE_KEY
                environment variable. This allows existing skills/bots to use external
                wallets without code changes.

                For Kalshi trading, use SIMMER_SOLANA_KEY env var instead (base58 format).

                SECURITY WARNING:
                - Never log or print the private key
                - Never commit it to version control
                - Use environment variables or secure secret management
                - Ensure your bot runs in a secure environment
        """
        if venue not in self.VENUES:
            raise ValueError(f"Invalid venue '{venue}'. Must be one of: {self.VENUES}")

        # Normalize deprecated venue name
        if venue == "sandbox":
            import warnings
            warnings.warn(
                "'sandbox' venue is deprecated, use 'simmer' instead. Will be removed in 30 days.",
                DeprecationWarning,
                stacklevel=2
            )
            venue = "simmer"

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.venue = venue
        self._private_key: Optional[str] = None  # EVM private key (Polymarket)
        self._wallet_address: Optional[str] = None  # EVM wallet address
        self._wallet_linked: Optional[bool] = None  # Cached linking status
        self._approvals_checked: bool = False  # Track if we've warned about approvals
        self._solana_key_available: bool = False  # Solana key configured (Kalshi)
        self._solana_wallet_address: Optional[str] = None  # Solana wallet address

        # EVM key: Use provided private_key, or auto-detect from environment
        env_key = os.environ.get(self.PRIVATE_KEY_ENV_VAR)
        effective_key = private_key or env_key

        if effective_key:
            self._validate_and_set_wallet(effective_key)
            self._private_key = effective_key
            # Log that external wallet mode is active (but never log the key!)
            if not private_key and env_key:
                logger.info(
                    "External wallet mode (EVM): detected %s env var, wallet %s",
                    self.PRIVATE_KEY_ENV_VAR,
                    self._wallet_address[:10] + "..." if self._wallet_address else "unknown"
                )

        # Solana key: Auto-detect from environment for Kalshi trading
        if os.environ.get(self.SOLANA_PRIVATE_KEY_ENV_VAR):
            self._solana_key_available = True
            # Derive wallet address (deferred until needed to avoid import if not used)
            try:
                from .solana_signing import get_solana_public_key
                self._solana_wallet_address = get_solana_public_key()
                if self._solana_wallet_address:
                    logger.info(
                        "External wallet mode (Solana): detected %s env var, wallet %s",
                        self.SOLANA_PRIVATE_KEY_ENV_VAR,
                        self._solana_wallet_address[:10] + "..."
                    )
            except Exception as e:
                logger.warning("Could not derive Solana wallet address: %s", e)

        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        })

    def _validate_and_set_wallet(self, private_key: str) -> None:
        """Validate private key format and derive wallet address."""
        if not private_key.startswith("0x"):
            raise ValueError("Private key must start with '0x'")
        if len(private_key) != self.PRIVATE_KEY_LENGTH:
            raise ValueError("Invalid private key length")

        try:
            from .signing import get_wallet_address
            self._wallet_address = get_wallet_address(private_key)
        except ImportError as e:
            # eth_account not installed - raise clear error
            raise ImportError(
                "External wallet requires eth_account package. "
                "Install with: pip install eth-account"
            ) from e

    @property
    def wallet_address(self) -> Optional[str]:
        """Get the EVM wallet address (only available when private_key is set)."""
        return self._wallet_address

    @property
    def has_external_wallet(self) -> bool:
        """Check if client is configured for external EVM wallet trading (Polymarket)."""
        return self._private_key is not None

    @property
    def solana_wallet_address(self) -> Optional[str]:
        """Get the Solana wallet address (only available when SIMMER_SOLANA_KEY is set)."""
        return self._solana_wallet_address

    @property
    def has_solana_wallet(self) -> bool:
        """Check if client is configured for external Solana wallet trading (Kalshi)."""
        return self._solana_key_available

    def _ensure_wallet_linked(self) -> None:
        """
        Ensure wallet is linked to Simmer account before trading.

        Called automatically before external wallet trades.
        Caches the result to avoid repeated API calls.
        """
        if not self._private_key or not self._wallet_address:
            return

        # If we've already confirmed it's linked, skip
        if self._wallet_linked is True:
            return

        # Check if wallet is already linked via API
        try:
            settings = self._request("GET", "/api/sdk/settings")
            linked_address = settings.get("linked_wallet_address") or settings.get("wallet_address")

            if linked_address and linked_address.lower() == self._wallet_address.lower():
                self._wallet_linked = True
                logger.debug("Wallet %s already linked", self._wallet_address[:10] + "...")
                return
        except Exception as e:
            logger.debug("Could not check wallet link status: %s", e)

        # Wallet not linked - attempt to link automatically
        logger.info("Auto-linking wallet %s to Simmer account...", self._wallet_address[:10] + "...")
        try:
            result = self.link_wallet(signature_type=0)
            if result.get("success"):
                self._wallet_linked = True
                logger.info("Wallet linked successfully")
            else:
                logger.warning("Wallet linking returned: %s", result.get("error", "unknown error"))
        except Exception as e:
            # Log warning but don't fail - the trade API will return proper error
            logger.warning("Auto-link failed: %s. Trade may fail if wallet not linked.", e)

    def _warn_approvals_once(self) -> None:
        """
        Check and warn about missing approvals (once per session).

        Called before first external wallet trade.
        """
        if self._approvals_checked or not self._wallet_address:
            return

        self._approvals_checked = True

        try:
            status = self.check_approvals()
            if not status.get("all_set", False):
                logger.warning(
                    "Polymarket approvals may be missing for wallet %s. "
                    "Trade may fail. Use client.ensure_approvals() to check status.",
                    self._wallet_address[:10] + "..."
                )
        except Exception as e:
            logger.debug("Could not check approvals: %s", e)

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
        amount: float = 0,
        shares: float = 0,
        action: str = "buy",
        venue: Optional[str] = None,
        order_type: str = "FAK",
        reasoning: Optional[str] = None,
        source: Optional[str] = None
    ) -> TradeResult:
        """
        Execute a trade on a market.

        Args:
            market_id: Market ID to trade on
            side: 'yes' or 'no'
            amount: Dollar amount to spend (for buys)
            shares: Number of shares to sell (for sells)
            action: 'buy' or 'sell' (default: 'buy')
            venue: Override client's default venue for this trade.
                - "simmer": Simmer LMSR, $SIM virtual currency
                - "polymarket": Real Polymarket CLOB, USDC (requires linked wallet)
                - "kalshi": Real Kalshi trading via DFlow, USDC on Solana
                  (requires SIMMER_SOLANA_KEY env var with base58 secret key)
                - None: Use client's default venue
            order_type: Order type for Polymarket trades (default: "FAK").
                - "FAK": Fill And Kill - fill what you can immediately, cancel rest (recommended for bots)
                - "FOK": Fill Or Kill - fill 100% immediately or cancel entirely
                - "GTC": Good Till Cancelled - limit order, stays on book until filled
                - "GTD": Good Till Date - limit order with expiry
                Only applies to venue="polymarket". Ignored for simmer.
            reasoning: Optional explanation for the trade. This will be displayed
                publicly on the market's trade history page, allowing spectators
                to see why your bot made this trade.
            source: Optional source tag for tracking (e.g., "sdk:weather", "sdk:copytrading").
                Used to track which strategy opened each position.

        Returns:
            TradeResult with execution details

        Example:
            # Use client default venue
            result = client.trade(market_id, "yes", 10.0)

            # Override venue for single trade
            result = client.trade(market_id, "yes", 10.0, venue="polymarket")

            # Use FOK for all-or-nothing execution
            result = client.trade(market_id, "yes", 10.0, venue="polymarket", order_type="FOK")

            # Include reasoning and source tag
            result = client.trade(
                market_id, "yes", 10.0,
                reasoning="Strong bullish signal from sentiment analysis",
                source="sdk:my-strategy"
            )

            # External wallet trading - Polymarket (local EVM signing)
            client = SimmerClient(
                api_key="sk_live_...",
                venue="polymarket",
                private_key="0x..."  # Your EVM wallet's private key
            )
            result = client.trade(market_id, "yes", 10.0)  # Signs locally

            # External wallet trading - Kalshi (local Solana signing)
            # Set SIMMER_SOLANA_KEY env var to your base58 Solana secret key
            import os
            os.environ["SIMMER_SOLANA_KEY"] = "your_base58_secret_key"
            client = SimmerClient(api_key="sk_live_...", venue="kalshi")
            result = client.trade(market_id, "yes", 10.0)  # Signs locally with Solana key
        """
        effective_venue = venue or self.venue
        if effective_venue not in self.VENUES:
            raise ValueError(f"Invalid venue '{effective_venue}'. Must be one of: {self.VENUES}")
        if order_type not in self.ORDER_TYPES:
            raise ValueError(f"Invalid order_type '{order_type}'. Must be one of: {self.ORDER_TYPES}")
        if action not in ("buy", "sell"):
            raise ValueError(f"Invalid action '{action}'. Must be 'buy' or 'sell'")

        # Validate amount/shares based on action
        is_sell = action == "sell"
        if is_sell and shares <= 0:
            raise ValueError("shares required for sell orders")
        if not is_sell and amount <= 0:
            raise ValueError("amount required for buy orders")

        payload = {
            "market_id": market_id,
            "side": side,
            "amount": amount,
            "shares": shares,
            "action": action,
            "venue": effective_venue,
            "order_type": order_type
        }
        if reasoning:
            payload["reasoning"] = reasoning
        if source:
            payload["source"] = source

        # External wallet: ensure linked, check approvals, sign locally
        if self._private_key and effective_venue == "polymarket":
            # Auto-link wallet if not already linked
            self._ensure_wallet_linked()
            # Warn about missing approvals (once per session)
            self._warn_approvals_once()
            # Sign order locally
            signed_order = self._build_signed_order(
                market_id, side, amount if not is_sell else 0,
                shares if is_sell else 0, action
            )
            if signed_order:
                payload["signed_order"] = signed_order

        # Kalshi BYOW: sign transactions locally using SIMMER_SOLANA_KEY
        if effective_venue == "kalshi":
            return self._execute_kalshi_byow_trade(
                market_id=market_id,
                side=side,
                amount=amount,
                shares=shares,
                action=action,
                reasoning=reasoning,
                source=source
            )

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
            shares_requested=data.get("shares_requested", 0),
            order_status=data.get("order_status"),
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

    def import_market(self, polymarket_url: str, sandbox: bool = None) -> Dict[str, Any]:
        """
        Import a Polymarket market to Simmer.

        Creates a public tracking market on Simmer that:
        - Is visible on simmer.markets dashboard
        - Can be traded by any agent (simmer with $SIM)
        - Tracks external Polymarket prices
        - Resolves based on Polymarket outcome

        After importing, you can:
        - Trade with $SIM: client.trade(market_id, "yes", 10)
        - Trade real USDC: client.trade(market_id, "yes", 10, venue="polymarket")

        Args:
            polymarket_url: Full Polymarket URL to import
            sandbox: DEPRECATED - ignored. All imports are now public.

        Returns:
            Dict with market_id, question, and import details

        Rate Limits:
            - 10 imports per day per agent
            - Requires claimed agent for imports

        Example:
            # Import a market
            result = client.import_market("https://polymarket.com/event/will-x-happen")
            print(f"Imported: {result['market_id']}")

            # Trade on it (simmer - $SIM)
            client.trade(market_id=result['market_id'], side="yes", amount=10)

            # Or trade real money
            client.trade(market_id=result['market_id'], side="yes", amount=50, venue="polymarket")
        """
        if sandbox is not None:
            import warnings
            warnings.warn(
                "The 'sandbox' parameter is deprecated and ignored. "
                "All imports are now public. Remove the sandbox parameter. "
                "Update with: pip install --upgrade simmer-sdk",
                DeprecationWarning,
                stacklevel=2
            )
        data = self._request(
            "POST",
            "/api/sdk/markets/import",
            json={"polymarket_url": polymarket_url}
        )
        return data

    def list_importable_markets(
        self,
        min_volume: float = 10000,
        limit: int = 50,
        category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        List active Polymarket markets that can be imported.

        Returns markets that are:
        - Open for trading (not resolved)
        - Not already imported to Simmer
        - Above minimum volume threshold

        Use this to discover markets before calling import_market().

        Args:
            min_volume: Minimum 24h volume in USD (default: 10000)
            limit: Max markets to return (default: 50, max: 100)
            category: Filter by category (e.g., "politics", "crypto", "sports")

        Returns:
            List of dicts with question, url, condition_id, current_price, volume_24h

        Example:
            # Find importable crypto markets
            markets = client.list_importable_markets(category="crypto", limit=10)
            for m in markets:
                print(f"{m['question']} - ${m['volume_24h']:,.0f} volume")
                result = client.import_market(m['url'])
        """
        params = {
            "min_volume": min_volume,
            "limit": limit,
        }
        if category:
            params["category"] = category

        data = self._request("GET", "/api/sdk/markets/importable", params=params)
        return data.get("markets", [])

    def get_portfolio(self) -> Optional[Dict[str, Any]]:
        """
        Get portfolio summary with balance, exposure, and positions by source.

        Returns:
            Dict containing:
            - balance_usdc: Available USDC balance
            - total_exposure: Total value in open positions
            - positions: List of current positions
            - by_source: Breakdown by trade source (e.g., "sdk:weather", "sdk:copytrading")

        Example:
            portfolio = client.get_portfolio()
            print(f"Balance: ${portfolio['balance_usdc']}")
            print(f"Weather positions: {portfolio['by_source'].get('sdk:weather', {})}")
        """
        return self._request("GET", "/api/sdk/portfolio")

    def get_market_context(self, market_id: str) -> Optional[Dict[str, Any]]:
        """
        Get market context with trading safeguards.

        Returns context useful for making trading decisions, including:
        - Current position (if any)
        - Recent trade history
        - Flip-flop detection (trading discipline)
        - Slippage estimates
        - Warnings (time decay, low liquidity, etc.)

        Args:
            market_id: Market ID to get context for

        Returns:
            Dict containing:
            - market: Market details (question, prices, resolution criteria)
            - position: Current position in this market (if any)
            - discipline: Trading discipline info (flip-flop detection)
            - slippage: Estimated execution costs
            - warnings: List of warnings (e.g., "Market resolves in 2 hours")

        Example:
            context = client.get_market_context(market_id)
            if context['warnings']:
                print(f"Warnings: {context['warnings']}")
            if context['discipline'].get('is_flip_flop'):
                print("Warning: This would be a flip-flop trade")
        """
        return self._request("GET", f"/api/sdk/context/{market_id}")

    def get_price_history(self, market_id: str) -> List[Dict[str, Any]]:
        """
        Get price history for trend detection.

        Args:
            market_id: Market ID to get history for

        Returns:
            List of price points, each containing:
            - timestamp: ISO timestamp
            - price_yes: YES price at that time
            - price_no: NO price at that time

        Example:
            history = client.get_price_history(market_id)
            if len(history) >= 2:
                trend = history[-1]['price_yes'] - history[0]['price_yes']
                print(f"Price trend: {'+' if trend > 0 else ''}{trend:.2f}")
        """
        data = self._request("GET", f"/api/sdk/markets/{market_id}/history")
        return data.get("points", []) if data else []

    # ==========================================
    # PRICE ALERTS
    # ==========================================

    def create_alert(
        self,
        market_id: str,
        side: str,
        condition: str,
        threshold: float,
        webhook_url: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a price alert.

        Alerts trigger when market price crosses the specified threshold.
        Unlike risk monitors, alerts don't require a position.

        Args:
            market_id: Market to monitor
            side: Which price to monitor ('yes' or 'no')
            condition: Trigger condition:
                - 'above': Trigger when price >= threshold
                - 'below': Trigger when price <= threshold
                - 'crosses_above': Trigger when price crosses from below to above threshold
                - 'crosses_below': Trigger when price crosses from above to below threshold
            threshold: Price threshold (0-1)
            webhook_url: Optional HTTPS URL to receive webhook notification

        Returns:
            Dict containing alert details (id, market_id, side, condition, threshold, etc.)

        Example:
            # Alert when YES price drops below 30%
            alert = client.create_alert(
                market_id="...",
                side="yes",
                condition="below",
                threshold=0.30,
                webhook_url="https://my-server.com/webhook"
            )
            print(f"Created alert {alert['id']}")
        """
        return self._request("POST", "/api/sdk/alerts", json={
            "market_id": market_id,
            "side": side,
            "condition": condition,
            "threshold": threshold,
            "webhook_url": webhook_url
        })

    def get_alerts(self, include_triggered: bool = False) -> List[Dict[str, Any]]:
        """
        List alerts.

        Args:
            include_triggered: If True, include alerts that have already triggered.
                              Default is False (only active alerts).

        Returns:
            List of alert dicts with id, market_id, side, condition, threshold, etc.

        Example:
            alerts = client.get_alerts()
            print(f"You have {len(alerts)} active alerts")
        """
        params = {"include_triggered": include_triggered}
        data = self._request("GET", "/api/sdk/alerts", params=params)
        return data.get("alerts", [])

    def delete_alert(self, alert_id: str) -> Dict[str, Any]:
        """
        Delete an alert.

        Args:
            alert_id: ID of the alert to delete

        Returns:
            Dict with success status

        Example:
            client.delete_alert("abc123...")
        """
        return self._request("DELETE", f"/api/sdk/alerts/{alert_id}")

    def get_triggered_alerts(self, hours: int = 24) -> List[Dict[str, Any]]:
        """
        Get alerts that triggered within the last N hours.

        Args:
            hours: Look back period in hours (default: 24, max: 168 = 1 week)

        Returns:
            List of triggered alert dicts

        Example:
            triggered = client.get_triggered_alerts(hours=48)
            for alert in triggered:
                print(f"Alert {alert['id']} triggered at {alert['triggered_at']}")
        """
        data = self._request("GET", "/api/sdk/alerts/triggered", params={"hours": hours})
        return data.get("alerts", [])

    # ==========================================
    # EXTERNAL WALLET SUPPORT
    # ==========================================

    def _build_signed_order(
        self,
        market_id: str,
        side: str,
        amount: float = 0,
        shares: float = 0,
        action: str = "buy"
    ) -> Optional[Dict[str, Any]]:
        """
        Build and sign a Polymarket order locally.

        Internal method used when private_key is set.

        Args:
            market_id: Market to trade on
            side: 'yes' or 'no'
            amount: Dollar amount (for buys)
            shares: Number of shares (for sells)
            action: 'buy' or 'sell'
        """
        if not self._private_key or not self._wallet_address:
            return None

        try:
            from .signing import build_and_sign_order
        except ImportError:
            raise ImportError(
                "Local signing requires py_order_utils. "
                "Install with: pip install py-order-utils py-clob-client eth-account"
            )

        is_sell = action == "sell"

        # Get market data to find token IDs and price
        market_data = self._request("GET", f"/api/sdk/markets/{market_id}")
        if not market_data:
            raise ValueError(f"Market {market_id} not found")

        # Get token ID based on side
        if side.lower() == "yes":
            token_id = market_data.get("polymarket_token_id")
        else:
            token_id = market_data.get("polymarket_no_token_id")

        if not token_id:
            raise ValueError(f"Market {market_id} does not have Polymarket token IDs")

        # Get price - use external price for the side
        if side.lower() == "yes":
            price = market_data.get("external_price_yes") or 0.5
        else:
            external_yes = market_data.get("external_price_yes") or 0.5
            price = 1.0 - external_yes

        # Clamp price to valid range to avoid division issues
        if price <= 0 or price >= 1:
            price = 0.5  # Fallback to 50%

        # Calculate size based on action
        if is_sell:
            size = shares  # Sell uses shares directly
        else:
            size = amount / price  # Buy calculates shares from amount

        # Determine CLOB side
        clob_side = "SELL" if is_sell else "BUY"

        neg_risk = market_data.get("polymarket_neg_risk", False)

        # Build and sign the order
        signed = build_and_sign_order(
            private_key=self._private_key,
            wallet_address=self._wallet_address,
            token_id=token_id,
            side=clob_side,
            price=price,
            size=size,
            neg_risk=neg_risk,
            signature_type=0,  # EOA
        )

        return signed.to_dict()

    def _execute_kalshi_byow_trade(
        self,
        market_id: str,
        side: str,
        amount: float = 0,
        shares: float = 0,
        action: str = "buy",
        reasoning: Optional[str] = None,
        source: Optional[str] = None
    ) -> TradeResult:
        """
        Execute a Kalshi trade using BYOW (Bring Your Own Wallet).

        Uses SIMMER_SOLANA_KEY environment variable for local signing.
        The private key never leaves the local machine.

        Flow:
        1. Get unsigned transaction from Simmer API (via DFlow)
        2. Sign locally using SIMMER_SOLANA_KEY
        3. Submit signed transaction to Simmer API

        Args:
            market_id: Market ID to trade on
            side: 'yes' or 'no'
            amount: Dollar amount (for buys)
            shares: Number of shares (for sells)
            action: 'buy' or 'sell'
            reasoning: Optional trade explanation
            source: Optional source tag

        Returns:
            TradeResult with execution details
        """
        # Check for Solana key
        if not self._solana_key_available:
            return TradeResult(
                success=False,
                market_id=market_id,
                side=side,
                error=(
                    "SIMMER_SOLANA_KEY environment variable required for Kalshi trading. "
                    "Set it to your base58-encoded Solana secret key."
                )
            )

        try:
            from .solana_signing import sign_solana_transaction
        except ImportError as e:
            return TradeResult(
                success=False,
                market_id=market_id,
                side=side,
                error=f"Solana signing not available: {e}"
            )

        is_sell = action == "sell"

        # Step 1: Get unsigned transaction from Simmer API
        try:
            quote_payload = {
                "market_id": market_id,
                "side": side,
                "amount": amount,
                "shares": shares,
                "action": action,
                "wallet_address": self._solana_wallet_address
            }
            quote = self._request(
                "POST",
                "/api/sdk/trade/kalshi/quote",
                json=quote_payload
            )
        except Exception as e:
            return TradeResult(
                success=False,
                market_id=market_id,
                side=side,
                error=f"Failed to get quote: {e}"
            )

        if not quote.get("success"):
            return TradeResult(
                success=False,
                market_id=market_id,
                side=side,
                error=quote.get("error", "Failed to get quote from Simmer")
            )

        unsigned_tx = quote.get("transaction")
        if not unsigned_tx:
            return TradeResult(
                success=False,
                market_id=market_id,
                side=side,
                error="Quote missing transaction data"
            )

        # Step 2: Sign locally
        try:
            signed_tx = sign_solana_transaction(unsigned_tx)
        except Exception as e:
            return TradeResult(
                success=False,
                market_id=market_id,
                side=side,
                error=f"Local signing failed: {e}"
            )

        # Step 3: Submit signed transaction
        try:
            submit_payload = {
                "market_id": market_id,
                "side": side,
                "action": action,
                "signed_transaction": signed_tx,
                "quote_id": quote.get("quote_id"),  # For tracking
                "reasoning": reasoning,
                "source": source
            }
            data = self._request(
                "POST",
                "/api/sdk/trade/kalshi/submit",
                json=submit_payload
            )
        except Exception as e:
            return TradeResult(
                success=False,
                market_id=market_id,
                side=side,
                error=f"Failed to submit trade: {e}"
            )

        return TradeResult(
            success=data.get("success", False),
            trade_id=data.get("trade_id"),
            market_id=data.get("market_id", market_id),
            side=data.get("side", side),
            shares_bought=data.get("shares_bought", 0) if not is_sell else 0,
            shares_requested=data.get("shares_requested", 0),
            order_status=data.get("order_status"),
            cost=data.get("cost", 0),
            new_price=data.get("new_price", 0),
            balance=None,  # Real trading doesn't track $SIM balance
            error=data.get("error")
        )

    def link_wallet(self, signature_type: int = 0) -> Dict[str, Any]:
        """
        Link an external wallet to your Simmer account.

        This proves ownership of the wallet by signing a challenge message.
        Once linked, you can trade using your own wallet instead of
        Simmer-managed wallets.

        Args:
            signature_type: Signature type for the wallet.
                - 0: EOA (standard wallet, default)
                - 1: Polymarket proxy wallet
                - 2: Gnosis Safe

        Returns:
            Dict with success status and wallet info

        Raises:
            ValueError: If no private_key is configured
            Exception: If linking fails

        Example:
            client = SimmerClient(
                api_key="sk_live_...",
                private_key="0x..."
            )
            result = client.link_wallet()
            if result["success"]:
                print(f"Linked wallet: {result['wallet_address']}")
        """
        if not self._private_key or not self._wallet_address:
            raise ValueError(
                "private_key required for wallet linking. "
                "Initialize client with private_key parameter."
            )

        if signature_type not in (0, 1, 2):
            raise ValueError(
                f"Invalid signature_type {signature_type}. "
                "Must be 0 (EOA), 1 (Polymarket proxy), or 2 (Gnosis Safe)"
            )

        try:
            from .signing import sign_message
        except ImportError:
            raise ImportError(
                "Wallet linking requires eth_account. "
                "Install with: pip install eth-account"
            )

        # Step 1: Request challenge nonce
        challenge = self._request(
            "GET",
            "/api/sdk/wallet/link/challenge",
            params={"address": self._wallet_address}
        )

        nonce = challenge.get("nonce")
        message = challenge.get("message")

        if not nonce or not message:
            raise ValueError("Failed to get challenge from server")

        # Step 2: Sign the challenge message
        signature = sign_message(self._private_key, message)

        # Step 3: Submit signed challenge
        result = self._request(
            "POST",
            "/api/sdk/wallet/link",
            json={
                "address": self._wallet_address,
                "signature": signature,
                "nonce": nonce,
                "signature_type": signature_type
            }
        )

        return result

    def check_approvals(self, address: Optional[str] = None) -> Dict[str, Any]:
        """
        Check Polymarket token approvals for a wallet.

        Polymarket requires several token approvals before trading.
        This method checks the status of all required approvals.

        Args:
            address: Wallet address to check. Defaults to the configured
                    wallet if private_key was provided.

        Returns:
            Dict containing:
            - all_set: True if all approvals are in place
            - usdc_approved: USDC.e approval status
            - ctf_approved: CTF token approval status
            - Individual spender approval details

        Example:
            approvals = client.check_approvals()
            if not approvals["all_set"]:
                print("Please set approvals in your Polymarket wallet")
                print(f"Missing: {approvals}")
        """
        check_address = address or self._wallet_address
        if not check_address:
            raise ValueError(
                "No wallet address provided. Either pass address parameter "
                "or initialize client with private_key."
            )

        return self._request(
            "GET",
            f"/api/polymarket/allowances/{check_address}"
        )

    def ensure_approvals(self) -> Dict[str, Any]:
        """
        Check approvals and return transaction data for any missing ones.

        Convenience method that combines check_approvals() with
        get_missing_approval_transactions() from the approvals module.

        Returns:
            Dict containing:
            - ready: True if all approvals are set
            - missing_transactions: List of tx data for missing approvals
            - guide: Human-readable status message

        Raises:
            ValueError: If no wallet is configured

        Example:
            result = client.ensure_approvals()
            if not result["ready"]:
                print(result["guide"])
                for tx in result["missing_transactions"]:
                    # Sign and send tx
                    print(f"Send tx to {tx['to']}: {tx['description']}")
        """
        if not self._wallet_address:
            raise ValueError(
                "No wallet configured. Initialize client with private_key."
            )

        from .approvals import get_missing_approval_transactions, format_approval_guide

        status = self.check_approvals()
        missing_txs = get_missing_approval_transactions(status)
        guide = format_approval_guide(status)

        return {
            "ready": status.get("all_set", False),
            "missing_transactions": missing_txs,
            "guide": guide,
            "raw_status": status,
        }

    @staticmethod
    def check_for_updates(warn: bool = True) -> Dict[str, Any]:
        """
        Check PyPI for a newer version of the SDK.

        Args:
            warn: If True, print a warning message when outdated (default: True)

        Returns:
            Dict containing:
            - current: Currently installed version
            - latest: Latest version on PyPI
            - update_available: True if a newer version exists
            - message: Human-readable status message

        Example:
            result = SimmerClient.check_for_updates()
            if result["update_available"]:
                print(result["message"])

            # Or just check silently
            info = SimmerClient.check_for_updates(warn=False)
            if info["update_available"]:
                # Handle update logic
                pass
        """
        from . import __version__

        result = {
            "current": __version__,
            "latest": None,
            "update_available": False,
            "message": "",
        }

        try:
            response = requests.get(
                "https://pypi.org/pypi/simmer-sdk/json",
                timeout=5
            )
            response.raise_for_status()
            latest = response.json()["info"]["version"]
            result["latest"] = latest

            # Simple version comparison (works for semver)
            if latest != __version__:
                # Parse versions for proper comparison
                def parse_version(v):
                    return tuple(int(x) for x in v.split(".")[:3])

                try:
                    current_tuple = parse_version(__version__)
                    latest_tuple = parse_version(latest)
                    result["update_available"] = latest_tuple > current_tuple
                except (ValueError, IndexError):
                    # Can't parse version - don't assume update available
                    result["update_available"] = False
                    logger.debug("Could not parse versions for comparison: %s vs %s", __version__, latest)

            if result["update_available"]:
                result["message"] = (
                    f"⚠️  simmer-sdk {latest} available (you have {__version__})\n"
                    f"   Update with: pip install --upgrade simmer-sdk"
                )
                if warn:
                    print(result["message"])
            else:
                result["message"] = f"✓ simmer-sdk {__version__} is up to date"

        except requests.RequestException as e:
            logger.debug("Could not check for updates: %s", e)
            result["message"] = f"Could not check for updates: {e}"

        return result
