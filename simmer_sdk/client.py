"""
Simmer SDK Client

Simple Python client for trading on Simmer prediction markets.
"""

import os
import time
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
    is_live_now: Optional[bool] = None  # True if market window has started; None if field not returned by API
    opens_at: Optional[str] = None  # When the market window opens (fast markets only)
    polymarket_token_id: Optional[str] = None  # YES token ID for CLOB trading
    polymarket_no_token_id: Optional[str] = None  # NO token ID for CLOB trading
    polymarket_neg_risk: bool = False
    spread_cents: Optional[float] = None  # Bid-ask spread in cents (fast markets only)
    liquidity_tier: Optional[str] = None  # "tight", "moderate", or "wide" (fast markets only)


@dataclass
class Position:
    """Represents a position in a market.
    
    For simmer venue: sim_balance tracks remaining paper trading balance.
    For polymarket venue: cost_basis tracks real USDC spent.
    """
    market_id: str
    question: str
    shares_yes: float
    shares_no: float
    current_value: float
    pnl: float
    status: str
    venue: str = "simmer"  # "simmer" or "polymarket"
    sim_balance: Optional[float] = None  # Simmer only: remaining $SIM balance
    cost_basis: Optional[float] = None  # Polymarket only: USDC spent
    avg_cost: Optional[float] = None  # Average cost per share
    current_price: Optional[float] = None  # Current market price
    sources: Optional[List[str]] = None  # Trade sources (e.g., ["sdk:weather"])


@dataclass
class TradeResult:
    """Result of a trade execution."""
    success: bool
    trade_id: Optional[str] = None
    market_id: str = ""
    side: str = ""
    venue: str = "simmer"  # "simmer", "polymarket", or "kalshi"
    shares_bought: float = 0  # Actual shares filled (for Polymarket, assumes full fill if matched)
    shares_requested: float = 0  # Shares requested (for partial fill detection)
    order_status: Optional[str] = None  # Polymarket order status: "matched", "live", "delayed"
    cost: float = 0  # Cost in $SIM (simmer) or USDC (polymarket/kalshi)
    new_price: float = 0
    balance: Optional[float] = None  # Remaining $SIM balance (simmer only, None for real venues)
    error: Optional[str] = None
    simulated: bool = False  # True for paper trades (dry-run with real prices)
    skip_reason: Optional[str] = None  # Why trade was skipped (e.g. "conflicts skipped")

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
    # Primary: WALLET_PRIVATE_KEY. Fallback: SIMMER_PRIVATE_KEY (deprecated, backward compat)
    PRIVATE_KEY_ENV_VAR = "WALLET_PRIVATE_KEY"
    PRIVATE_KEY_ENV_VAR_LEGACY = "SIMMER_PRIVATE_KEY"
    # Environment variable for Solana private key (Kalshi via DFlow)
    SOLANA_PRIVATE_KEY_ENV_VAR = "SOLANA_PRIVATE_KEY"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.simmer.markets",
        venue: str = "simmer",
        private_key: Optional[str] = None,
        live: bool = True
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
                  (requires SOLANA_PRIVATE_KEY env var with base58 secret key)
                Note: "sandbox" is a deprecated alias for "simmer" (will be removed in 30 days)
            live: Whether to execute real trades (default: True).
                When False, trades are simulated with real market prices
                and tracked in memory for the duration of the run. All read
                endpoints (get_markets, get_context, etc.) work normally.
            private_key: Optional EVM wallet private key for Polymarket trading.
                When provided, orders are signed locally instead of server-side.
                This enables trading with your own Polymarket wallet.

                If not provided, the SDK will auto-detect from the WALLET_PRIVATE_KEY
                environment variable (or deprecated SIMMER_PRIVATE_KEY fallback).
                This allows existing skills/bots to use external wallets without code changes.

                For Kalshi trading, use SOLANA_PRIVATE_KEY env var instead (base58 format).

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
        if not os.environ.get("TRADING_VENUE"):
            logger.info(
                "TRADING_VENUE not set, using venue='%s'. "
                "Set TRADING_VENUE=simmer for paper trading with $SIM.",
                venue
            )
        self._private_key: Optional[str] = None  # EVM private key (Polymarket)
        self._wallet_address: Optional[str] = None  # EVM wallet address
        self._wallet_linked: Optional[bool] = None  # Cached linking status
        self._approvals_checked: bool = False  # Track if we've warned about approvals
        self._solana_key_available: bool = False  # Solana key configured (Kalshi)
        self._solana_wallet_address: Optional[str] = None  # Solana wallet address
        self._held_markets_cache: Optional[dict] = None  # {market_id: [source_tags]}
        self._held_markets_ts: float = 0  # Cache timestamp
        self._clob_client = None  # Cached ClobClient for local CLOB operations

        # EVM key: Use provided private_key, or auto-detect from environment
        # Check WALLET_PRIVATE_KEY first, fall back to deprecated SIMMER_PRIVATE_KEY
        import warnings
        _wallet_key = os.environ.get(self.PRIVATE_KEY_ENV_VAR)
        _legacy_key = os.environ.get(self.PRIVATE_KEY_ENV_VAR_LEGACY)
        if _wallet_key and _legacy_key and _wallet_key != _legacy_key:
            warnings.warn(
                "Both WALLET_PRIVATE_KEY and SIMMER_PRIVATE_KEY are set with different values. "
                "Using WALLET_PRIVATE_KEY. Remove SIMMER_PRIVATE_KEY to avoid confusion.",
                UserWarning,
                stacklevel=2
            )
        elif not _wallet_key and _legacy_key:
            warnings.warn(
                "SIMMER_PRIVATE_KEY is deprecated. Use WALLET_PRIVATE_KEY instead.",
                DeprecationWarning,
                stacklevel=2
            )
        env_key = _wallet_key or _legacy_key
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
                self._solana_key_available = False

        from simmer_sdk import __version__ as _sdk_version
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"simmer-sdk/{_sdk_version}",
        })

        # Cache for auto_redeem toggle (TTL: 5 minutes)
        self._auto_redeem_enabled: bool = True
        self._auto_redeem_enabled_fetched_at: float = 0.0

        # Paper trading mode
        self.live = live
        self._paper_portfolio = None
        if not self.live:
            from .paper import PaperPortfolio
            self._paper_portfolio = PaperPortfolio()
            logger.info("Paper trading mode enabled. Trades will be simulated with real prices.")

        # Auto-process risk alerts on init (external wallets only)
        if self.live and self._private_key and venue in ("polymarket",):
            try:
                self._process_risk_alerts()
            except Exception as e:
                logger.warning("Risk alert check failed: %s", e)

    def __repr__(self):
        return f"SimmerClient(venue={self.venue!r}, base_url={self.base_url!r})"

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
        """Get the Solana wallet address (only available when SOLANA_PRIVATE_KEY is set)."""
        return self._solana_wallet_address

    @property
    def has_solana_wallet(self) -> bool:
        """Check if client is configured for external Solana wallet trading (Kalshi)."""
        return self._solana_key_available

    # ==========================================
    # RISK ALERT AUTO-PROCESSING
    # ==========================================

    def _process_risk_alerts(self):
        """Check for and execute triggered risk exits (called on init for external wallets)."""
        try:
            response = self._request("GET", "/api/sdk/risk-alerts")
        except Exception:
            return  # API unreachable — skip silently

        alerts = response.get("risk_alerts", [])
        if not alerts:
            return

        print(f"[SimmerSDK] {len(alerts)} risk alert(s) detected — processing exits")

        for alert in alerts:
            market_id = alert["market_id"]
            side = alert["side"]
            shares = float(alert["shares"])
            reason = alert["exit_reason"]
            token_id = alert.get("token_id")

            try:
                # 1. Cancel open orders on this market (client-side)
                if token_id:
                    self._cancel_orders_for_token(token_id)

                # 2. Execute the sell
                result = self.trade(
                    market_id=market_id,
                    side=side,
                    shares=shares,
                    action="sell",
                    order_type="FAK",
                )

                # 3. Delete the risk setting (position is exited)
                try:
                    self.delete_monitor(market_id, side)
                except Exception:
                    pass  # Non-fatal — server will clean up

                # 4. Delete the Redis alert to prevent re-triggering
                try:
                    self._request("DELETE", f"/api/sdk/risk-alerts/{market_id}/{side}")
                except Exception:
                    pass  # Non-fatal — alert expires via TTL

                print(f"[SimmerSDK] Risk exit executed: {reason} on {market_id[:8]}... "
                      f"{side} — sold {shares:.2f} shares")

            except Exception as e:
                print(f"[SimmerSDK] Risk exit failed for {market_id[:8]}... {side}: {e}")
                # Alert persists in Redis — will retry next cycle

    def _get_clob_client(self):
        """Get or create an authenticated ClobClient for local CLOB operations."""
        if self._clob_client is not None:
            return self._clob_client

        from py_clob_client.client import ClobClient

        client = ClobClient(
            host="https://clob.polymarket.com",
            key=self._private_key,
            chain_id=137,
            signature_type=0,
            funder=self._wallet_address,
        )
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)
        self._clob_client = client
        return client

    def _cancel_orders_for_token(self, token_id: str):
        """Cancel all open orders for a token using local py_clob_client."""
        try:
            client = self._get_clob_client()
            result = client.cancel_market_orders(asset_id=token_id)
            cancelled = result.get("canceled", [])
            if cancelled:
                print(f"[SimmerSDK] Cancelled {len(cancelled)} open order(s)")
        except Exception as e:
            print(f"[SimmerSDK] Order cancel failed (non-fatal): {e}")

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
                self._ensure_clob_credentials()
                return
        except Exception as e:
            logger.debug("Could not check wallet link status: %s", e)

        # Wallet not linked - attempt to link automatically
        print(f"Auto-linking wallet {self._wallet_address[:10]}... to Simmer account...")
        try:
            result = self.link_wallet(signature_type=0)
            if result.get("success"):
                self._wallet_linked = True
                print("Wallet linked successfully")
                # Derive and register CLOB credentials right after linking
                self._ensure_clob_credentials()
            else:
                error = result.get("error") or result.get("message") or f"Server returned: {result}"
                print(f"ERROR: Wallet linking failed: {error}")
                raise RuntimeError(f"Wallet linking failed: {error}")
        except RuntimeError:
            raise
        except Exception as e:
            print(f"ERROR: Auto-link failed: {e}. Call client.link_wallet() manually.")
            raise RuntimeError(f"Wallet linking failed: {e}")

    def _ensure_clob_credentials(self) -> None:
        """
        Derive and register Polymarket CLOB API credentials if not already done.

        Uses py_clob_client to derive credentials from the private key, then
        sends them to the backend for encrypted storage. One-time per wallet.
        """
        if not self._private_key or not self._wallet_address:
            return

        if getattr(self, '_clob_creds_registered', False):
            return

        # Check server first to avoid unnecessary derivation + rate-limited POST
        try:
            check = self._request("GET", "/api/sdk/wallet/credentials/check")
            if check.get("has_credentials"):
                self._clob_creds_registered = True
                return
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 404:
                pass  # Old server without the check endpoint — fall through to register
            elif status in (401, 403, 429):
                logger.warning("Credentials check returned %s — skipping re-registration", status)
                return
            else:
                logger.warning("Credentials check failed (HTTP %s) — will attempt registration", status)
        except requests.exceptions.ConnectionError:
            logger.warning("Cannot reach server for credentials check — will attempt registration")
        except Exception as e:
            logger.debug("Credentials check failed unexpectedly: %s — will attempt registration", e)

        try:
            from py_clob_client.client import ClobClient

            client = ClobClient(
                host="https://clob.polymarket.com",
                key=self._private_key,
                chain_id=137,
                signature_type=0,  # EOA
                funder=self._wallet_address
            )

            creds = client.create_or_derive_api_creds()

            # Register with backend
            self._request("POST", "/api/sdk/wallet/credentials", json={
                "api_key": creds.api_key,
                "api_secret": creds.api_secret,
                "api_passphrase": creds.api_passphrase
            })

            self._clob_creds_registered = True
            logger.info("CLOB credentials registered for wallet %s", self._wallet_address[:10] + "...")

        except ImportError:
            logger.warning(
                "py-clob-client not installed — cannot derive CLOB credentials. "
                "Install with: pip install py-clob-client"
            )
        except Exception as e:
            logger.warning("Failed to derive/register CLOB credentials: %s", e)

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
                    "Trade may fail. Use client.set_approvals() to set them.",
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

        return [self._parse_market(m) for m in data.get("markets", [])]

    def get_fast_markets(
        self,
        asset: Optional[str] = None,
        window: Optional[str] = None,
        limit: int = 50,
        sort: Optional[str] = None,
    ) -> List[Market]:
        """
        Get fast-resolving markets (5m, 15m, 1h, etc.).

        Args:
            asset: Crypto ticker (BTC, ETH, SOL, etc.)
            window: Time window (5m, 15m, 1h, 4h, daily)
            limit: Maximum number of markets to return
            sort: Sort order ('volume', 'opportunity', or None for soonest-first)

        Returns:
            List of Market objects sorted by is_live_now (live first), then resolves_at
        """
        params: Dict[str, Any] = {"limit": limit}
        if asset:
            params["asset"] = asset
        if window:
            params["window"] = window
        if sort:
            params["sort"] = sort

        data = self._request("GET", "/api/sdk/fast-markets", params=params)

        return [self._parse_market(m) for m in data.get("markets", [])]

    @staticmethod
    def _parse_market(m: dict) -> Market:
        """Parse a market dict from any /markets endpoint into a Market object."""
        return Market(
            id=m["id"],
            question=m["question"],
            status=m.get("status", "active"),
            current_probability=m.get("current_probability", 0.5),
            import_source=m.get("import_source"),
            external_price_yes=m.get("external_price_yes"),
            divergence=m.get("divergence"),
            resolves_at=m.get("resolves_at"),
            is_sdk_only=m.get("is_sdk_only", False),
            is_live_now=m.get("is_live_now"),
            opens_at=m.get("opens_at"),
            polymarket_token_id=m.get("polymarket_token_id"),
            polymarket_no_token_id=m.get("polymarket_no_token_id"),
            polymarket_neg_risk=m.get("polymarket_neg_risk", False),
            spread_cents=m.get("spread_cents"),
            liquidity_tier=m.get("liquidity_tier"),
        )

    def trade(
        self,
        market_id: str,
        side: str,
        amount: float = 0,
        shares: float = 0,
        action: str = "buy",
        venue: Optional[str] = None,
        order_type: str = "FAK",
        price: Optional[float] = None,
        reasoning: Optional[str] = None,
        source: Optional[str] = None,
        skill_slug: Optional[str] = None,
        allow_rebuy: bool = False
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
                  (requires SOLANA_PRIVATE_KEY env var with base58 secret key)
                - None: Use client's default venue
            order_type: Order type for Polymarket trades (default: "FAK").
                - "FAK": Fill And Kill - fill what you can immediately, cancel rest (recommended for bots)
                - "FOK": Fill Or Kill - fill 100% immediately or cancel entirely
                - "GTC": Good Till Cancelled - limit order, stays on book until filled
                - "GTD": Good Till Date - limit order with expiry
                Only applies to venue="polymarket". Ignored for simmer.
            price: Limit price (0.001-0.999) for the outcome being traded. For side="yes",
                this is the YES token price. For side="no", this is the NO token price
                (NOT 1-price). If omitted, uses current market price for that outcome.
                Sub-cent prices (e.g. 0.009 for 0.9¢) are supported for neg_risk markets.
                Only applies to venue="polymarket". Ignored for simmer.
            reasoning: Optional explanation for the trade. This will be displayed
                publicly on the market's trade history page, allowing spectators
                to see why your bot made this trade.
            source: Optional source tag for tracking (e.g., "sdk:weather", "sdk:copytrading").
                Used to track which strategy opened each position.
            skill_slug: Optional skill slug for volume attribution (e.g., "polymarket-weather-trader").
                Matches the ClawHub slug. Used by Simmer to track skill-level trading volume.
            allow_rebuy: If False (default), skip buying a market you already hold a
                position on (same source). Set True for DCA or averaging-in strategies.

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
            # Set SOLANA_PRIVATE_KEY env var to your base58 Solana secret key
            import os
            os.environ["SOLANA_PRIVATE_KEY"] = "your_base58_secret_key"
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

        # Paper trading: simulate with real prices (no live API calls)
        if not self.live:
            return self._paper_trade(
                market_id, side, amount, shares, action, effective_venue
            )

        # Position conflict checks (buy only — sells always allowed)
        if action == "buy" and not allow_rebuy and not source:
            held = self._get_held_markets()
            if market_id in held:
                logger.debug("Rebuy skipped on %s: already hold position", market_id)
                return TradeResult(
                    success=False,
                    market_id=market_id,
                    side=side,
                    error="Already hold position on this market. Pass allow_rebuy=True to override.",
                    skip_reason="rebuy skipped",
                )
        if action == "buy" and source:
            held = self._get_held_markets()
            market_sources = held.get(market_id, [])
            if market_sources:
                # Cross-skill conflict: different skill holds this market
                other_sources = [s for s in market_sources if s != source]
                if other_sources:
                    logger.debug(
                        "Cross-skill conflict on %s: my_source=%r, other_sources=%r",
                        market_id, source, other_sources
                    )
                    return TradeResult(
                        success=False,
                        market_id=market_id,
                        side=side,
                        error=f"Cross-skill conflict: {other_sources} already hold position on this market",
                        skip_reason="conflicts skipped",
                    )
                # Same-skill rebuy: already hold from this source
                if not allow_rebuy and source in market_sources:
                    logger.debug(
                        "Rebuy skipped on %s: already hold position from source=%r",
                        market_id, source
                    )
                    return TradeResult(
                        success=False,
                        market_id=market_id,
                        side=side,
                        error=f"Already hold position on this market (source: {source}). Pass allow_rebuy=True to override.",
                        skip_reason="rebuy skipped",
                    )

        # Validate price if provided
        if price is not None:
            if price < 0.001 or price > 0.999:
                raise ValueError("price must be between 0.001 and 0.999 (Polymarket share prices)")
            if effective_venue != "polymarket":
                raise ValueError(f"price parameter only supported for venue='polymarket' (you specified venue='{effective_venue}')")

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
        if skill_slug:
            payload["skill_slug"] = skill_slug
        if price is not None:
            payload["price"] = price

        # External wallet: ensure linked, check approvals, sign locally
        if self._private_key and effective_venue == "polymarket":
            # Auto-link wallet if not already linked
            self._ensure_wallet_linked()
            # Warn about missing approvals (once per session)
            self._warn_approvals_once()
            # Sign order locally
            signed_order = self._build_signed_order(
                market_id, side, amount if not is_sell else 0,
                shares if is_sell else 0, action, order_type, price
            )
            if signed_order:
                payload["signed_order"] = signed_order

        # Kalshi BYOW: sign transactions locally using SOLANA_PRIVATE_KEY
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

        # Extract balance: only meaningful for simmer venue ($SIM balance)
        # Polymarket/Kalshi trades don't return a balance (use get_portfolio() instead)
        position = data.get("position") or {}
        balance = position.get("sim_balance") if effective_venue == "simmer" else None

        result = TradeResult(
            success=data.get("success", False),
            trade_id=data.get("trade_id"),
            market_id=data.get("market_id", market_id),
            side=data.get("side", side),
            venue=effective_venue,
            shares_bought=data.get("shares_bought", 0),
            shares_requested=data.get("shares_requested", 0),
            order_status=data.get("order_status"),
            cost=data.get("cost", 0),
            new_price=data.get("new_price", 0),
            balance=balance,
            error=data.get("error")
        )
        if result.success:
            self._held_markets_cache = None  # Invalidate so next check sees new position
        return result

    def _paper_trade(self, market_id, side, amount, shares, action, venue):
        """Simulate a trade using real market prices."""
        import time as _time

        # Fetch current price from the venue
        try:
            ctx = self.get_market_context(market_id)
        except Exception as e:
            return TradeResult(
                success=False, market_id=market_id,
                error=f"Could not fetch market price: {e}", simulated=True
            )

        if not ctx or "market" not in ctx:
            return TradeResult(
                success=False, market_id=market_id,
                error="Could not fetch market price", simulated=True
            )

        market = ctx["market"]
        price = float(market.get("external_price_yes") or market.get("current_probability") or 0.5)
        if side == "no":
            price = 1.0 - price
        price = max(price, 0.001)  # Floor to avoid division by zero (supports sub-cent neg_risk markets)

        if action == "buy":
            shares_filled = amount / price
            cost = amount
        else:
            pos = self._paper_portfolio.get_position(market_id)
            available = getattr(pos, f"shares_{side}", 0)
            shares_filled = min(shares, available)
            if shares_filled <= 0:
                return TradeResult(
                    success=False, market_id=market_id, side=side,
                    error=f"No paper position to sell (have {available:.2f} {side} shares)",
                    simulated=True
                )
            cost = shares_filled * price

        self._paper_portfolio.log_trade(market_id, side, action, shares_filled, cost, price)

        return TradeResult(
            success=True,
            trade_id=f"paper_{int(_time.time())}",
            market_id=market_id,
            side=side,
            venue=venue,
            shares_bought=shares_filled,
            shares_requested=shares_filled,
            order_status="simulated",
            cost=round(cost, 4),
            new_price=price,
            simulated=True,
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

    def get_positions(self, venue: Optional[str] = None, source: Optional[str] = None) -> List[Position]:
        """
        Get all positions for this agent.

        Args:
            venue: Filter by venue ("simmer" or "polymarket"). If None, returns both.
            source: Filter by trade source (e.g., "weather", "copytrading"). Partial match.

        Returns:
            List of Position objects with P&L info
        """
        params = {}
        if venue:
            params["venue"] = venue
        if source:
            params["source"] = source
            
        data = self._request("GET", "/api/sdk/positions", params=params if params else None)

        positions = []
        for p in data.get("positions", []):
            pos_venue = p.get("venue", "simmer")
            positions.append(Position(
                market_id=p["market_id"],
                question=p.get("question", ""),
                shares_yes=p.get("shares_yes", 0),
                shares_no=p.get("shares_no", 0),
                current_value=p.get("current_value", 0),
                pnl=p.get("pnl", 0),
                status=p.get("status", "active"),
                venue=pos_venue,
                sim_balance=p.get("sim_balance"),  # Only present for simmer
                cost_basis=p.get("cost_basis"),  # Only present for polymarket
                avg_cost=p.get("avg_cost"),
                current_price=p.get("current_price"),
                sources=p.get("sources"),
            ))
        return positions

    _HELD_MARKETS_TTL = 30  # seconds

    def _get_held_markets(self) -> dict:
        """Get market_id -> [source_tags] for all held positions. Cached 30s."""
        import time as _t
        now = _t.time()
        if self._held_markets_cache is not None and (now - self._held_markets_ts) < self._HELD_MARKETS_TTL:
            return self._held_markets_cache

        positions = self.get_positions()
        held = {}
        for p in positions:
            if (p.shares_yes or 0) > 0 or (p.shares_no or 0) > 0:
                held[p.market_id] = p.sources or []
        self._held_markets_cache = held
        self._held_markets_ts = now
        return held

    def get_held_markets(self) -> dict:
        """
        Get map of market_id -> source tags for all held positions.

        Returns:
            Dict mapping market_id to list of source tags (e.g. ["sdk:signal-sniper"])
        """
        return self._get_held_markets()

    def check_conflict(self, market_id: str, my_source: str) -> bool:
        """
        Check if another skill has an open position on this market.

        Args:
            market_id: Market to check
            my_source: This skill's source tag (e.g. "sdk:signal-sniper")

        Returns:
            True if another skill holds a position on this market
        """
        sources = self._get_held_markets().get(market_id, [])
        if not sources:
            return False
        return any(s != my_source for s in sources)

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
        try:
            data = self._request("GET", f"/api/sdk/markets/{market_id}")
            m = data.get("market")
            if not m:
                return None
            return self._parse_market(m)
        except Exception:
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

    def get_open_orders(self) -> Dict[str, Any]:
        """
        Get open (on-book) orders placed through Simmer.

        Returns GTC/GTD orders that Simmer believes are still on the CLOB.
        May include stale entries if filled/cancelled but not synced back.
        Only includes orders placed through the Simmer API.

        Returns:
            Dict with 'orders' list and 'count'
        """
        return self._request("GET", "/api/sdk/orders/open")

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

    def import_kalshi_market(self, kalshi_url: str) -> Dict[str, Any]:
        """
        Import a Kalshi market to Simmer.

        Creates a public tracking market on Simmer that:
        - Is visible on simmer.markets dashboard
        - Can be traded by any agent (simmer with $SIM)
        - Tracks external Kalshi prices
        - Resolves based on Kalshi outcome
        - Supports real USDC trading via venue="kalshi"

        After importing, you can:
        - Trade with $SIM: client.trade(market_id, "yes", 10)
        - Trade real USDC: client.trade(market_id, "yes", 10, venue="kalshi")

        Args:
            kalshi_url: Full Kalshi URL (e.g. https://kalshi.com/markets/KXHIGHNY-26FEB19/...)

        Returns:
            Dict with market_id, question, kalshi_ticker, and import details

        Rate Limits:
            - 10 imports per day per agent (50 for pro)
            - Requires claimed agent for imports

        Example:
            result = client.import_kalshi_market("https://kalshi.com/markets/KXHIGHNY-26FEB19/...")
            print(f"Imported: {result['market_id']}")
            client.trade(market_id=result['market_id'], side="yes", amount=10, venue="kalshi")
        """
        data = self._request(
            "POST",
            "/api/sdk/markets/import/kalshi",
            json={"kalshi_url": kalshi_url}
        )
        return data

    def list_importable_markets(
        self,
        min_volume: float = 10000,
        limit: int = 50,
        category: Optional[str] = None,
        venue: Optional[str] = None,
        q: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List active markets from external venues that can be imported.

        Returns markets that are:
        - Open for trading (not resolved)
        - Not already imported to Simmer
        - Above minimum volume threshold

        Use this to discover markets before calling import_market().

        Args:
            min_volume: Minimum 24h volume in USD (default: 10000)
            limit: Max markets to return (default: 50, max: 100)
            category: Filter by category (e.g., "politics", "crypto", "sports"). Polymarket only.
            venue: Filter by venue ("polymarket", "kalshi", or None for both)
            q: Keyword search on market title (min 2 chars)

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
        if venue:
            params["venue"] = venue
        if q:
            params["q"] = q

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
    # SETTINGS
    # ==========================================

    def get_settings(self) -> Dict[str, Any]:
        """
        Get your SDK trading settings.

        Returns:
            Dict containing:
            - max_trades_per_day: Daily trade limit (default: 20)
            - max_position_usd: Max USD per trade (default: 100)
            - default_stop_loss_pct: Default stop-loss percentage (0-1)
            - default_take_profit_pct: Default take-profit percentage (0-1)
            - auto_risk_monitor_enabled: Auto-create risk monitors on new positions
            - clawdbot_webhook_url: Webhook URL for notifications
            - clawdbot_chat_id: Chat ID for notifications
            - clawdbot_channel: Notification channel

        Example:
            settings = client.get_settings()
            print(f"Daily trade limit: {settings['max_trades_per_day']}")
        """
        return self._request("GET", "/api/sdk/user/settings")

    def update_settings(self, **kwargs) -> Dict[str, Any]:
        """
        Update your SDK trading settings.

        Keyword Args:
            max_trades_per_day: Daily trade limit (1-1000, default: 20)
            max_position_usd: Max USD per trade (1-10000, default: 100)
            default_stop_loss_pct: Stop-loss percentage (0-1)
            default_take_profit_pct: Take-profit percentage (0-1)
            auto_risk_monitor_enabled: Auto-create risk monitors
            clawdbot_webhook_url: Webhook URL for notifications
            clawdbot_chat_id: Chat ID for notifications
            clawdbot_channel: Notification channel

        Returns:
            Dict with updated settings

        Example:
            # Increase daily trade limit
            client.update_settings(max_trades_per_day=40)

            # Set multiple settings at once
            client.update_settings(
                max_trades_per_day=50,
                max_position_usd=200,
                auto_risk_monitor_enabled=True
            )
        """
        if not kwargs:
            raise ValueError("No settings provided. Pass keyword arguments to update.")
        return self._request("PATCH", "/api/sdk/user/settings", json=kwargs)

    # ==========================================
    # RISK MONITORS (Stop-Loss / Take-Profit)
    # ==========================================

    def set_monitor(
        self,
        market_id: str,
        side: str,
        stop_loss_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Set a stop-loss and/or take-profit monitor on a position.

        The system checks every 15 minutes and automatically sells
        when thresholds are hit.

        Args:
            market_id: Market ID to monitor
            side: Which side of your position ('yes' or 'no')
            stop_loss_pct: Sell if P&L drops below this % (e.g., 0.20 = -20%)
            take_profit_pct: Sell if P&L rises above this % (e.g., 0.50 = +50%)

        At least one threshold must be set.

        Returns:
            Dict with monitor details (market_id, side, stop_loss_pct, take_profit_pct)

        Example:
            # Set 20% stop-loss and 50% take-profit
            client.set_monitor("market-id", "yes", stop_loss_pct=0.20, take_profit_pct=0.50)

            # Stop-loss only
            client.set_monitor("market-id", "no", stop_loss_pct=0.30)
        """
        payload: Dict[str, Any] = {"side": side}
        if stop_loss_pct is not None:
            payload["stop_loss_pct"] = stop_loss_pct
        if take_profit_pct is not None:
            payload["take_profit_pct"] = take_profit_pct
        return self._request("POST", f"/api/sdk/positions/{market_id}/monitor", json=payload)

    def list_monitors(self) -> List[Dict[str, Any]]:
        """
        List all active risk monitors with current position P&L.

        Returns:
            List of monitors, each containing market_id, side, stop_loss_pct,
            take_profit_pct, current P&L, and position details.

        Example:
            monitors = client.list_monitors()
            for m in monitors:
                print(f"{m['market_id']} {m['side']}: SL={m['stop_loss_pct']}, TP={m['take_profit_pct']}")
        """
        resp = self._request("GET", "/api/sdk/positions/monitors")
        return resp.get("monitors", []) if isinstance(resp, dict) else resp

    def delete_monitor(self, market_id: str, side: str) -> Dict[str, Any]:
        """
        Remove a risk monitor from a position.

        Args:
            market_id: Market ID
            side: Which side ('yes' or 'no')

        Returns:
            Dict confirming deletion

        Example:
            client.delete_monitor("market-id", "yes")
        """
        return self._request("DELETE", f"/api/sdk/positions/{market_id}/monitor", params={"side": side})

    # ==========================================
    # ORDER CANCELLATION
    # ==========================================

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """
        Cancel a single open order by ID.

        For external wallets: cancels locally via CLOB API.
        For managed wallets: cancels via server endpoint.

        Args:
            order_id: The order ID to cancel

        Returns:
            Dict with cancellation result
        """
        if self._private_key:
            return self._cancel_order_local(order_id)
        return self._request("DELETE", f"/api/sdk/orders/{order_id}")

    def cancel_market_orders(self, market_id: str, side: Optional[str] = None) -> Dict[str, Any]:
        """
        Cancel all open orders on a market.

        Args:
            market_id: Market ID
            side: Optional side filter ('yes' or 'no')

        Returns:
            Dict with cancellation result
        """
        if self._private_key:
            # Look up token_id from market data (response wraps fields under "market" key)
            resp = self._request("GET", f"/api/sdk/markets/{market_id}")
            market = resp.get("market", resp)
            if side == "no":
                token_id = market.get("polymarket_no_token_id")
            else:
                token_id = market.get("polymarket_token_id")
            if not token_id:
                return {"canceled": [], "error": "No token ID found"}
            self._cancel_orders_for_token(token_id)
            return {"canceled": ["local"], "market_id": market_id}
        params = {"side": side} if side else {}
        return self._request("DELETE", f"/api/sdk/markets/{market_id}/orders", params=params)

    def cancel_all_orders(self) -> Dict[str, Any]:
        """
        Cancel all open orders across all markets.

        Returns:
            Dict with cancellation result
        """
        if self._private_key:
            return self._cancel_all_local()
        return self._request("DELETE", "/api/sdk/orders")

    def _cancel_order_local(self, order_id: str) -> Dict[str, Any]:
        """Cancel a single order via local py_clob_client."""
        try:
            client = self._get_clob_client()
            result = client.cancel(order_id)
            return {"success": True, "order_id": order_id, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _cancel_all_local(self) -> Dict[str, Any]:
        """Cancel all orders via local py_clob_client."""
        try:
            client = self._get_clob_client()
            result = client.cancel_all()
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ==========================================
    # REDEMPTIONS
    # ==========================================

    def redeem(self, market_id: str, side: str) -> Dict[str, Any]:
        """
        Redeem a winning Polymarket position for USDC.e.

        After a market resolves, call this to convert CTF tokens into USDC.e
        in your wallet. The server looks up all Polymarket details automatically.

        For managed wallets: server signs and submits, returns tx_hash.
        For external wallets: signs locally and broadcasts via relay.

        Args:
            market_id: Market ID (from positions response)
            side: Which side you hold ('yes' or 'no')

        Returns:
            Dict with 'success' (bool) and 'tx_hash' (str) on success

        Example:
            # Check for redeemable positions
            positions = client.get_positions()
            for p in positions:
                if p.get('redeemable'):
                    result = client.redeem(p['market_id'], p['redeemable_side'])
                    print(f"Redeemed: {result['tx_hash']}")
        """
        result = self._request("POST", "/api/sdk/redeem", json={
            "market_id": market_id,
            "side": side,
        })

        # Managed wallet — server already signed and submitted
        if not result.get("unsigned_tx"):
            return result

        # External wallet — sign locally and broadcast
        if not self._private_key:
            raise ValueError(
                "Redemption requires signing. Set WALLET_PRIVATE_KEY env var or pass private_key to constructor."
            )

        try:
            from eth_account import Account
        except ImportError:
            raise ImportError(
                "eth-account is required for external wallet redemption. "
                "Install with: pip install eth-account"
            )

        unsigned_tx = result["unsigned_tx"]

        # Validate unsigned tx before signing
        _REDEEM_CONTRACT_WHITELIST = {
            "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045".lower(): "0x01b7037c",   # CTF: redeemPositions(address,bytes32,bytes32,uint256[])
            "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296".lower(): "0xdbeccb23",   # NegRiskAdapter: redeemPositions(bytes32,uint256[])
        }
        tx_to = unsigned_tx.get("to", "")
        if not tx_to or tx_to.lower() not in _REDEEM_CONTRACT_WHITELIST:
            return {"success": False, "error": "Unsigned tx targets unknown contract"}
        tx_from = unsigned_tx.get("from", "")
        if tx_from and tx_from.lower() != self._wallet_address.lower():
            return {"success": False, "error": "Unsigned tx is for wrong wallet"}

        # Validate calldata targets expected function selector
        tx_data = unsigned_tx.get("data", "")
        expected_selector = _REDEEM_CONTRACT_WHITELIST[tx_to.lower()]
        if not tx_data or not tx_data.lower().startswith(expected_selector):
            return {"success": False, "error": f"Unsigned tx has unexpected function selector (expected {expected_selector})"}

        # Cap gas limit to prevent POL drain
        tx_gas = int(unsigned_tx.get("gas", 200000))
        if tx_gas > 500_000:
            return {"success": False, "error": f"Gas limit too high ({tx_gas}), max 500000"}

        print(f"  Signing redemption transaction locally...")

        # Use Simmer's RPC proxy for chain queries
        def _rpc_call(method: str, params: list) -> Any:
            resp = self._request("POST", "/api/rpc/polygon", json={
                "jsonrpc": "2.0", "method": method, "params": params, "id": 1,
            })
            return resp.get("result")

        # Use nonce from backend unsigned_tx (freshest), fall back to RPC
        backend_nonce = unsigned_tx.get("nonce")
        if backend_nonce is not None:
            nonce = int(backend_nonce) if isinstance(backend_nonce, (int, float)) else int(str(backend_nonce), 0)
        else:
            nonce = int(_rpc_call("eth_getTransactionCount", [self._wallet_address, "pending"]) or "0x0", 16)

        gas_price = int(_rpc_call("eth_gasPrice", []) or "0x0", 16)
        priority_fee = max(30_000_000_000, gas_price // 4)
        max_fee = gas_price * 2

        tx_fields = {
            "to": tx_to,
            "data": bytes.fromhex(tx_data[2:] if tx_data.startswith("0x") else tx_data),
            "value": 0,
            "chainId": 137,
            "nonce": nonce,
            "gas": tx_gas,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority_fee,
            "type": 2,
        }

        signed = Account.sign_transaction(tx_fields, self._private_key)
        signed_tx_hex = "0x" + signed.raw_transaction.hex()

        # Broadcast via Simmer's Alchemy relay
        broadcast = self._request("POST", "/api/sdk/wallet/broadcast-tx", json={
            "signed_tx": signed_tx_hex,
        })

        tx_hash = broadcast.get("tx_hash")
        if not broadcast.get("success") or not tx_hash:
            return {"success": False, "error": broadcast.get("error", "Broadcast failed")}

        print(f"  Broadcast OK ({tx_hash[:18]}...) — waiting for confirmation...")

        # Poll for receipt
        for attempt in range(30):
            time.sleep(2)
            try:
                receipt_data = _rpc_call("eth_getTransactionReceipt", [tx_hash])
                if receipt_data:
                    status = int(receipt_data.get("status", "0x0"), 16)
                    block = int(receipt_data.get("blockNumber", "0x0"), 16)
                    if status == 1:
                        print(f"  Confirmed in block {block}")
                        # Report to server so position stops showing as redeemable
                        try:
                            self._request("POST", "/api/sdk/redeem/report", json={
                                "market_id": market_id,
                                "side": side,
                                "tx_hash": tx_hash,
                            })
                        except Exception as report_err:
                            logger.warning("redeem: failed to report confirmed redemption: %s", report_err)
                        return {"success": True, "tx_hash": tx_hash}
                    else:
                        return {"success": False, "tx_hash": tx_hash, "error": f"Transaction reverted in block {block}"}
            except Exception:
                pass
            if attempt > 0 and attempt % 5 == 0:
                print(f"  Still waiting for confirmation... ({attempt * 2}s)")

        # Timed out but tx may still confirm
        print(f"  Confirmation timed out. Check: https://polygonscan.com/tx/{tx_hash}")
        # Report anyway — tx likely confirmed, prevents re-redemption next cycle
        try:
            self._request("POST", "/api/sdk/redeem/report", json={
                "market_id": market_id,
                "side": side,
                "tx_hash": tx_hash,
            })
        except Exception:
            pass
        return {"success": True, "tx_hash": tx_hash, "note": "confirmation_timeout"}

    def auto_redeem(self) -> List[Dict[str, Any]]:
        """
        Automatically redeem all winning Polymarket positions that are ready to claim.

        Checks all positions for redeemable wins and submits redemption transactions.
        For external wallets (WALLET_PRIVATE_KEY), signs and broadcasts locally.
        For managed wallets, the server handles signing.

        Reads the agent's ``auto_redeem_enabled`` setting. If ``False``, returns an
        empty list immediately. If the field is absent (older backend), defaults to
        ``True`` so existing agents continue to benefit.

        Safe to call every cycle — skips positions that are not redeemable and catches
        all errors internally (never raises).

        Returns:
            List of dicts, one per attempted redemption:
                - market_id: str
                - side: str ("yes" or "no")
                - success: bool
                - tx_hash: str or None
                - error: str or None

        Example:
            results = client.auto_redeem()
            for r in results:
                if r["success"]:
                    print(f"Redeemed {r['market_id']} {r['side']}: {r['tx_hash']}")
                else:
                    print(f"Failed {r['market_id']} {r['side']}: {r['error']}")
        """
        results = []

        # Check auto_redeem_enabled setting (from agents/me), cached with a 5-minute TTL.
        # Default True if field is absent (backward compat with older backend versions).
        _AUTO_REDEEM_TTL = 300  # 5 minutes
        now = time.time()
        if now - self._auto_redeem_enabled_fetched_at > _AUTO_REDEEM_TTL:
            try:
                agent_info = self._request("GET", "/api/sdk/agents/me")
                self._auto_redeem_enabled = agent_info.get("auto_redeem_enabled", True)
                self._auto_redeem_enabled_fetched_at = now
            except Exception as e:
                logger.warning("auto_redeem: could not read agent settings, using cached value (%s)", e)

        if not self._auto_redeem_enabled:
            logger.debug("auto_redeem: disabled by agent settings, skipping")
            return results

        # Fetch positions (raw request to get redeemable fields not on Position dataclass)
        try:
            data = self._request("GET", "/api/sdk/positions", params={"status": "resolved"})
        except Exception as e:
            logger.warning("auto_redeem: could not fetch positions (%s)", e)
            return results

        positions = data.get("positions", [])
        redeemable = [
            p for p in positions
            if p.get("redeemable") and p.get("redeemable_side")
            and p.get("venue", "polymarket") == "polymarket"
        ]

        if not redeemable:
            logger.debug("auto_redeem: no redeemable positions found")
            return results

        logger.info("auto_redeem: found %d redeemable position(s)", len(redeemable))

        # Note: for external wallet users, each redeem() call polls for on-chain
        # confirmation (up to 60s per position). With many redeemable positions
        # this can block for several minutes. Managed wallet users return immediately.
        for pos in redeemable:
            market_id = pos.get("market_id", "")
            side = pos.get("redeemable_side", "")
            if not market_id or not side:
                continue
            try:
                print(f"  Auto-redeem: {market_id} ({side})...")
                result = self.redeem(market_id, side)
                success = bool(result.get("success"))
                tx_hash = result.get("tx_hash")
                error = result.get("error") if not success else None
                if success:
                    print(f"  Auto-redeem OK: {market_id} ({side}) tx={tx_hash}")
                else:
                    print(f"  Auto-redeem failed: {market_id} ({side}) error={error}")
                results.append({
                    "market_id": market_id,
                    "side": side,
                    "success": success,
                    "tx_hash": tx_hash,
                    "error": error,
                })
            except Exception as e:
                logger.warning("auto_redeem: error redeeming %s %s: %s", market_id, side, e)
                results.append({
                    "market_id": market_id,
                    "side": side,
                    "success": False,
                    "tx_hash": None,
                    "error": str(e),
                })

        return results

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
    # WEBHOOKS
    # ==========================================

    def register_webhook(
        self,
        url: str,
        events: List[str] = None,
        secret: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Register a webhook URL to receive event notifications.

        Args:
            url: HTTPS URL to receive webhook POSTs
            events: Event types to subscribe to. Options:
                    - "trade.executed" (fires on trade fill/submit)
                    - "market.resolved" (fires when held market resolves)
                    - "price.movement" (fires on >5% price change for held markets)
                    Defaults to all events.
            secret: Optional HMAC signing key. If set, payloads include
                    X-Simmer-Signature header for verification.

        Returns:
            Dict with webhook subscription details (id, url, events, active)

        Example:
            webhook = client.register_webhook(
                url="https://my-bot.example.com/webhook",
                events=["trade.executed", "market.resolved"],
                secret="my-signing-secret"
            )
            print(f"Registered: {webhook['id']}")
        """
        if events is None:
            events = ["trade.executed", "market.resolved", "price.movement"]
        payload = {"url": url, "events": events}
        if secret:
            payload["secret"] = secret
        return self._request("POST", "/api/sdk/webhooks", json=payload)

    def list_webhooks(self) -> List[Dict[str, Any]]:
        """
        List all webhook subscriptions.

        Returns:
            List of webhook subscription dicts

        Example:
            for wh in client.list_webhooks():
                print(f"{wh['url']} -> {wh['events']} (active={wh['active']})")
        """
        data = self._request("GET", "/api/sdk/webhooks")
        return data.get("webhooks", [])

    def delete_webhook(self, webhook_id: str) -> Dict[str, Any]:
        """
        Delete a webhook subscription.

        Args:
            webhook_id: ID of the webhook to delete

        Returns:
            Dict with success status

        Example:
            client.delete_webhook("abc123...")
        """
        return self._request("DELETE", f"/api/sdk/webhooks/{webhook_id}")

    def test_webhook(self) -> Dict[str, Any]:
        """
        Send a test payload to all active webhook subscriptions.

        Returns:
            Dict with success status

        Example:
            client.test_webhook()
        """
        return self._request("POST", "/api/sdk/webhooks/test")

    # ==========================================
    # AUTOMATON
    # ==========================================

    def get_skill_config(self, slug: str) -> Dict[str, str]:
        """
        Fetch tuned config for a skill from the automaton.

        Returns env var overrides set by the automaton's tuning engine.
        If no automaton is configured or no overrides exist, returns {}.

        Args:
            slug: Skill slug (e.g. "polymarket-weather-trader")

        Returns:
            Dict of env var name → value (all strings)

        Example:
            config = client.get_skill_config("polymarket-weather-trader")
            # {"SIMMER_WEATHER_MAX_USD": "25", "SIMMER_WEATHER_ENTRY_THRESHOLD": "0.08"}
        """
        try:
            data = self._request("GET", "/api/sdk/automaton/my-config", params={"skill": slug})
            return data.get("config", {})
        except Exception:
            return {}

    def apply_skill_config(self, slug: str) -> Dict[str, str]:
        """
        Fetch tuned config and apply as environment variables.

        Call this at skill startup, before loading config from env vars.
        Values are set in os.environ so load_config() picks them up.

        Args:
            slug: Skill slug (e.g. "polymarket-weather-trader")

        Returns:
            Dict of env vars that were applied (empty if none)

        Example:
            client.apply_skill_config("polymarket-weather-trader")
            # Now os.environ has the tuned values; load_config() will use them
        """
        config = self.get_skill_config(slug)
        if config:
            # Only allow env vars prefixed with SIMMER_, excluding credentials
            _BLOCKED = {"SIMMER_API_KEY", "SIMMER_PRIVATE_KEY", "SIMMER_SECRET", "SIMMER_API_SECRET"}
            safe = {k: str(v) for k, v in config.items() if k.startswith("SIMMER_") and k not in _BLOCKED}
            os.environ.update(safe)
            logger.info("Applied %d automaton config override(s) for %s", len(config), slug)
        return config

    # ==========================================
    # EXTERNAL WALLET SUPPORT
    # ==========================================

    def _build_signed_order(
        self,
        market_id: str,
        side: str,
        amount: float = 0,
        shares: float = 0,
        action: str = "buy",
        order_type: str = "FAK",
        price: Optional[float] = None,
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
            order_type: Order type ('FAK', 'GTC', etc.)
            price: Optional limit price (0.001-0.999). If None, uses current market price.
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

        # Get market data to find token IDs, price, and tick_size
        markets_resp = self._request("GET", f"/api/sdk/markets/{market_id}")
        market_data = markets_resp.get("market") if isinstance(markets_resp, dict) else None
        if not market_data:
            raise ValueError(f"Market {market_id} not found")

        # Get token ID based on side
        if side.lower() == "yes":
            token_id = market_data.get("polymarket_token_id")
        else:
            token_id = market_data.get("polymarket_no_token_id")

        if not token_id:
            raise ValueError(f"Market {market_id} does not have Polymarket token IDs")

        # Get price - use custom price if provided, otherwise fetch from market data
        if price is None:
            # Fetch current market price for the side
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
        tick_size = market_data.get("tick_size", 0.01)
        fee_rate_bps = market_data.get("fee_rate_bps", 0)

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
            tick_size=tick_size,
            fee_rate_bps=fee_rate_bps,
            order_type=order_type,
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

        Uses SOLANA_PRIVATE_KEY environment variable for local signing.
        The private key never leaves the local machine.

        Flow:
        1. Get unsigned transaction from Simmer API (via DFlow)
        2. Sign locally using SOLANA_PRIVATE_KEY
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
                    "SOLANA_PRIVATE_KEY environment variable required for Kalshi trading. "
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

        result = TradeResult(
            success=data.get("success", False),
            trade_id=data.get("trade_id"),
            market_id=data.get("market_id", market_id),
            side=data.get("side", side),
            venue="kalshi",
            shares_bought=data.get("shares_bought", 0) if not is_sell else 0,
            shares_requested=data.get("shares_requested", 0),
            order_status=data.get("order_status"),
            cost=data.get("cost", 0),
            new_price=data.get("new_price", 0),
            balance=None,  # Real trading doesn't track $SIM balance
            error=data.get("error")
        )
        if result.success:
            self._held_markets_cache = None  # Invalidate so next check sees new position
        return result

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

    def check_approvals(self, address: Optional[str] = None, no_cache: bool = False, include_tx_params: bool = False) -> Dict[str, Any]:
        """
        Check Polymarket token approvals for a wallet.

        Polymarket requires several token approvals before trading.
        This method checks the status of all required approvals.

        Args:
            address: Wallet address to check. Defaults to the configured
                    wallet if private_key was provided.
            no_cache: If True, bypass server-side cache for fresh on-chain read.

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

        params = {}
        if no_cache:
            params["no_cache"] = "1"
        if include_tx_params:
            params["include_tx_params"] = "1"
        path = f"/api/polymarket/allowances/{check_address}"
        if params:
            path += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return self._request("GET", path)

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

    def set_approvals(self) -> Dict[str, Any]:
        """
        Set all required Polymarket token approvals for trading.

        Checks which approvals are missing, constructs and signs approval
        transactions locally, then relays them through Simmer's backend
        for reliable broadcasting via Alchemy RPC.

        Keys never leave the client — transactions are signed locally.

        Requires: eth-account package (pip install eth-account)

        Returns:
            Dict containing:
            - set: Number of approvals successfully set
            - skipped: Number of approvals already in place
            - failed: Number of approvals that failed
            - details: List of per-approval results

        Raises:
            ValueError: If no wallet is configured
            ImportError: If eth-account is not installed

        Example:
            client = SimmerClient(api_key="...")  # WALLET_PRIVATE_KEY auto-detected
            client.link_wallet()
            result = client.set_approvals()
            print(f"Set {result['set']} approvals, skipped {result['skipped']}")
        """
        if not self._private_key or not self._wallet_address:
            raise ValueError(
                "No wallet configured. Set WALLET_PRIVATE_KEY env var or pass private_key to constructor."
            )

        try:
            from eth_account import Account
        except ImportError:
            raise ImportError(
                "eth-account is required for set_approvals(). "
                "Install with: pip install eth-account"
            )

        from .approvals import get_missing_approval_transactions, get_approval_transactions

        # --- Helper functions (use Simmer's Alchemy RPC proxy for all chain queries) ---

        def _rpc_call(method: str, params: list) -> Any:
            """Make a JSON-RPC call through Simmer's Alchemy proxy."""
            resp = self._request("POST", "/api/rpc/polygon", json={
                "jsonrpc": "2.0", "method": method, "params": params, "id": 1,
            })
            return resp.get("result")

        def _fetch_nonce() -> int:
            """Fetch fresh nonce from chain (includes pending mempool txs)."""
            result = _rpc_call("eth_getTransactionCount", [self._wallet_address, "pending"])
            return int(result or "0x0", 16)

        def _fetch_gas_price() -> int:
            """Fetch current gas price from chain."""
            result = _rpc_call("eth_gasPrice", [])
            return int(result or "0x0", 16)

        def _calculate_fees(gas_price: int, bump_factor: float = 1.0) -> tuple:
            """Calculate EIP-1559 fees from current gas price.

            Args:
                gas_price: Current gas price in wei from eth_gasPrice
                bump_factor: Multiplier for retries (1.0 = no bump, 1.25 = 25% bump)

            Returns:
                (max_fee_per_gas, max_priority_fee_per_gas) in wei
            """
            priority_fee = max(30_000_000_000, gas_price // 4)  # min 30 gwei
            max_fee = gas_price * 2  # 2x current for headroom
            return int(max_fee * bump_factor), int(priority_fee * bump_factor)

        def _wait_for_receipt(tx_hash: str, approval_num: int, total_approvals: int) -> Optional[dict]:
            """Poll for tx receipt. Shows progress to user."""
            for attempt in range(30):  # ~60s max wait
                time.sleep(2)
                try:
                    receipt_data = self._request("POST", "/api/rpc/polygon", json={
                        "jsonrpc": "2.0",
                        "method": "eth_getTransactionReceipt",
                        "params": [tx_hash],
                        "id": 1,
                    })
                    receipt = receipt_data.get("result")
                    if receipt:
                        return receipt
                except Exception:
                    pass  # Retry polling
                # Progress update every 10s so user knows it's still working
                if attempt > 0 and attempt % 5 == 0:
                    print(f"    Still waiting for on-chain confirmation... ({attempt * 2}s)")
            return None

        # --- Step 1: Check current status ---

        print(f"\n{'='*50}")
        print(f"  Polymarket Approval Setup")
        print(f"  Wallet: {self._wallet_address[:10]}...{self._wallet_address[-6:]}")
        print(f"{'='*50}\n")

        print("Step 1/3: Checking which approvals are needed...")
        status = self.check_approvals(no_cache=True, include_tx_params=True)
        all_txs = get_approval_transactions()
        missing_txs = get_missing_approval_transactions(status)

        total = len(all_txs)
        skipped = total - len(missing_txs)
        set_count = 0
        failed = 0
        details = []

        if not missing_txs:
            print(f"  All {total} approvals already set. Your wallet is ready to trade!\n")
            return {"set": 0, "skipped": total, "failed": 0, "details": []}

        print(f"  {skipped}/{total} approvals already done, {len(missing_txs)} remaining.\n")

        # --- Step 2: Pre-flight checks ---

        print("Step 2/3: Pre-flight checks...")

        # Check POL balance for gas
        try:
            bal_result = _rpc_call("eth_getBalance", [self._wallet_address, "latest"])
            pol_balance_wei = int(bal_result or "0x0", 16)
            pol_balance = pol_balance_wei / 1e18
            # ~0.002 POL per approval tx at typical gas prices
            estimated_cost = len(missing_txs) * 0.002
            if pol_balance < estimated_cost:
                print(f"  WARNING: Low POL balance ({pol_balance:.4f} POL).")
                print(f"  Estimated gas needed: ~{estimated_cost:.3f} POL for {len(missing_txs)} approvals.")
                print(f"  Send POL (Polygon network) to {self._wallet_address}")
                print(f"  Continuing anyway — transactions may fail if gas runs out.\n")
            else:
                print(f"  POL balance: {pol_balance:.4f} POL (enough for gas)")
        except Exception:
            print("  Could not check POL balance — continuing anyway.")

        # Fetch fresh gas price
        try:
            gas_price = _fetch_gas_price()
            print(f"  Network gas price: {gas_price / 1e9:.1f} gwei")
        except Exception:
            gas_price = 50_000_000_000  # 50 gwei fallback
            print(f"  Could not fetch gas price, using default: {gas_price / 1e9:.0f} gwei")

        print()

        # --- Step 3: Send approval transactions ---

        print(f"Step 3/3: Sending {len(missing_txs)} approval transaction(s)...")
        print(f"  Each transaction is signed locally and relayed via Simmer.\n")

        MAX_RETRIES = 3

        for i, tx_data in enumerate(missing_txs):
            desc = tx_data.get("description", f"Approval {i + 1}")
            token = tx_data.get("token", "unknown")
            spender = tx_data.get("spender", "unknown")
            print(f"  [{i + 1}/{len(missing_txs)}] {desc}")
            print(f"       Token: {token} | Spender: {spender}")

            tx_succeeded = False

            for retry in range(MAX_RETRIES):
                try:
                    # Fresh nonce and gas price each attempt
                    nonce = _fetch_nonce()

                    if retry > 0:
                        # Re-fetch gas price on retries for fresh data
                        try:
                            gas_price = _fetch_gas_price()
                        except Exception:
                            pass  # Use previous gas_price
                        print(f"       Retry {retry}/{MAX_RETRIES - 1} — fresh nonce: {nonce}, gas: {gas_price / 1e9:.1f} gwei")

                    # On retries, bump 25% above fresh gas to replace stuck pending txs
                    bump_factor = 1.0 + (0.25 * retry)
                    max_fee, priority_fee = _calculate_fees(gas_price, bump_factor)

                    # Build transaction
                    tx_fields = {
                        "to": tx_data["to"],
                        "data": bytes.fromhex(tx_data["data"][2:] if tx_data["data"].startswith("0x") else tx_data["data"]),
                        "value": 0,
                        "chainId": 137,
                        "nonce": nonce,
                        "gas": 100000,  # Match managed wallet path; USDC.e proxy needs more than 80k
                        "maxFeePerGas": max_fee,
                        "maxPriorityFeePerGas": priority_fee,
                        "type": 2,  # EIP-1559
                    }

                    # Sign locally — private key never leaves this machine
                    signed = Account.sign_transaction(tx_fields, self._private_key)
                    signed_tx_hex = "0x" + signed.raw_transaction.hex()

                    # Broadcast via Simmer backend (Alchemy RPC)
                    result = self._request("POST", "/api/sdk/wallet/broadcast-tx", json={
                        "signed_tx": signed_tx_hex,
                    })

                    tx_hash = result.get("tx_hash")

                    if result.get("success") and tx_hash:
                        print(f"       Broadcast OK ({tx_hash[:18]}...) — waiting for confirmation...")

                        receipt = _wait_for_receipt(tx_hash, i + 1, len(missing_txs))

                        if receipt:
                            status_code = int(receipt.get("status", "0x0"), 16)
                            block_num = int(receipt.get("blockNumber", "0x0"), 16)
                            gas_used = int(receipt.get("gasUsed", "0x0"), 16)
                            if status_code == 1:
                                print(f"       Confirmed in block {block_num} (gas used: {gas_used:,})")
                                set_count += 1
                                details.append({"description": desc, "success": True, "tx_hash": tx_hash})
                                tx_succeeded = True
                            else:
                                print(f"       Transaction reverted in block {block_num}.")
                                if retry < MAX_RETRIES - 1:
                                    print(f"       Will retry with higher gas...")
                                    time.sleep(3)
                                    continue
                                failed += 1
                                details.append({"description": desc, "success": False, "tx_hash": tx_hash, "error": "reverted"})
                        else:
                            # Tx was broadcast but receipt polling timed out.
                            # The tx is likely still pending — don't count as failed.
                            print(f"       Confirmation timed out. Transaction may still be processing.")
                            print(f"       Check status: https://polygonscan.com/tx/{tx_hash}")
                            set_count += 1
                            details.append({"description": desc, "success": True, "tx_hash": tx_hash, "note": "confirmation_timeout"})
                            tx_succeeded = True
                        break  # Move to next approval (success or confirmed failure)

                    else:
                        error = result.get("error", "Unknown error")
                        if "underpriced" in error.lower() and retry < MAX_RETRIES - 1:
                            print(f"       Pending transaction in the way — retrying with higher gas...")
                            time.sleep(3)
                            continue
                        elif "already known" in error.lower():
                            # Transaction already in mempool — treat as success, wait for receipt
                            print(f"       Transaction already submitted — waiting for confirmation...")
                            # Try to get the pending tx hash from error or just move on
                            set_count += 1
                            details.append({"description": desc, "success": True, "note": "already_pending"})
                            tx_succeeded = True
                            break
                        elif "nonce too low" in error.lower():
                            # Nonce already used — approval may already be set. Re-check.
                            print(f"       Nonce already used — this approval may have been set by a previous attempt.")
                            set_count += 1
                            details.append({"description": desc, "success": True, "note": "nonce_consumed"})
                            tx_succeeded = True
                            break
                        else:
                            print(f"       Failed: {error}")
                            if retry < MAX_RETRIES - 1:
                                print(f"       Retrying in 5s...")
                                time.sleep(5)
                                continue
                            failed += 1
                            details.append({"description": desc, "success": False, "error": error})
                            break

                except Exception as e:
                    print(f"       Error: {type(e).__name__}: {e}")
                    if retry < MAX_RETRIES - 1:
                        print(f"       Retrying in 5s...")
                        time.sleep(5)
                        continue
                    failed += 1
                    details.append({"description": desc, "success": False, "error": str(e)})
                    break

            if tx_succeeded:
                print(f"       Done.\n")
            else:
                print()

        # --- Summary ---

        print(f"{'='*50}")
        print(f"  Approval Summary")
        print(f"{'='*50}")
        print(f"  Already set:  {skipped}")
        print(f"  Newly set:    {set_count}")
        if failed > 0:
            print(f"  Failed:       {failed}")
        print(f"  Total:        {skipped + set_count + failed}/{total}")
        print()

        if failed == 0 and (skipped + set_count) == total:
            print("  All approvals complete. Your wallet is ready to trade on Polymarket!")
            print(f"  Try: client.trade(market_id, 'yes', 10.0, venue='polymarket')")
        elif failed > 0:
            print(f"  {failed} approval(s) failed. You can re-run set_approvals() to retry —")
            print(f"  it will skip the ones that succeeded and only attempt the remaining.")
            if any(d.get("error") == "reverted" for d in details):
                print(f"\n  If approvals keep reverting, check:")
                print(f"    1. POL balance for gas: https://polygonscan.com/address/{self._wallet_address}")
                print(f"    2. Contact Simmer support with your wallet address.")

        print()
        return {"set": set_count, "skipped": skipped, "failed": failed, "details": details}

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
