"""
Simmer SDK Client

Simple Python client for trading on Simmer prediction markets.
"""

import hashlib
import os
import sys
import time
import logging
import requests
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _detect_runtime() -> str:
    """Detect the agent runtime environment from environment variables."""
    # OpenClaw sets OPENCLAW_* vars
    if any(k.startswith("OPENCLAW_") for k in os.environ):
        return "openclaw"
    # Hermes sets HERMES_HOME or HERMES_* vars
    if "HERMES_HOME" in os.environ or any(k.startswith("HERMES_") for k in os.environ):
        return "hermes"
    # Claude Code CLI sets CLAUDE_CODE
    if "CLAUDE_CODE" in os.environ:
        return "claude-code"
    return "unknown"


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
    venue: str = "sim"  # "sim" or "polymarket"
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
    venue: str = "sim"  # "sim", "polymarket", or "kalshi"
    shares_bought: float = 0  # Actual shares filled (for Polymarket, assumes full fill if matched)
    shares_requested: float = 0  # Shares requested (for partial fill detection)
    order_status: Optional[str] = None  # Polymarket order status: "matched", "live", "delayed"
    cost: float = 0  # Cost in $SIM (simmer) or USDC (polymarket/kalshi)
    new_price: float = 0
    balance: Optional[float] = None  # Remaining $SIM balance (simmer only, None for real venues)
    error: Optional[str] = None
    simulated: bool = False  # True for paper trades (dry-run with real prices)
    skip_reason: Optional[str] = None  # Why trade was skipped (e.g. "conflicts skipped")
    fill_status: str = "unknown"  # Server fill status: "filled", "submitted", "unconfirmed", "failed"

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
        # Sim trading (default) - uses $SIM virtual currency
        client = SimmerClient(api_key="sk_live_...")
        markets = client.get_markets(limit=10)
        result = client.trade(market_id=markets[0].id, side="yes", amount=10)
        print(f"Bought {result.shares_bought} shares for ${result.cost}")

        # Real trading on Polymarket - uses real USDC (requires wallet linked in dashboard)
        client = SimmerClient(api_key="sk_live_...", venue="polymarket")
        result = client.trade(market_id=markets[0].id, side="yes", amount=10)
    """

    # Valid venue options ("simmer" is silent alias for "sim", "sandbox" is deprecated)
    VENUES = ("sim", "simmer", "sandbox", "polymarket", "kalshi")
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
        venue: str = "sim",
        private_key: Optional[str] = None,
        ows_wallet: Optional[str] = None,
        live: bool = True,
        starting_balance: float = 10_000.0
    ):
        """
        Initialize the Simmer client.

        Args:
            api_key: Your SDK API key (sk_live_...)
            base_url: API base URL (default: production)
            venue: Trading venue (default: "sim")
                - "sim": Trade on Simmer's LMSR market with $SIM (virtual currency)
                - "polymarket": Execute real trades on Polymarket CLOB with USDC
                  (requires wallet linked in dashboard + real trading enabled)
                - "kalshi": Execute real trades on Kalshi via DFlow
                  (requires SOLANA_PRIVATE_KEY env var with base58 secret key)
                Note: "simmer" is a silent alias for "sim". "sandbox" is deprecated (will be removed in 30 days).
            live: Whether to execute real trades (default: True).
                When False, trades are simulated with real market prices
                and tracked in memory for the duration of the run. All read
                endpoints (get_markets, get_context, etc.) work normally.
                For Polymarket, fills model the CLOB bid-ask spread for
                realistic P&L. Positions auto-settle when markets resolve.
            starting_balance: Virtual starting capital for paper trading
                (default: 10,000). Only used when live=False.
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
            ows_wallet: Optional OWS wallet name for Polymarket trading.
                When provided, orders are signed via OWS — your private key
                never leaves the OWS vault. Install with: pip install open-wallet-standard
                Create a wallet: ows wallet create --name my-agent

                If not provided, the SDK will auto-detect from the OWS_WALLET
                environment variable.

                Priority: ows_wallet param > OWS_WALLET env > WALLET_PRIVATE_KEY env
        """
        if venue not in self.VENUES:
            raise ValueError(f"Invalid venue '{venue}'. Must be one of: {self.VENUES}")

        # Normalize deprecated venue name
        if venue == "sandbox":
            import warnings
            warnings.warn(
                "'sandbox' venue is deprecated, use 'sim' instead. Will be removed in 30 days.",
                DeprecationWarning,
                stacklevel=2
            )
            venue = "sim"

        # Silent alias: "simmer" → "sim"
        if venue == "simmer":
            venue = "sim"

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.venue = venue
        if not os.environ.get("TRADING_VENUE"):
            logger.info(
                "TRADING_VENUE not set, using venue='%s'. "
                "Set TRADING_VENUE=sim for paper trading with $SIM.",
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
        self._market_data_cache: dict = {}  # market_id -> market data for signing
        self._ows_wallet: Optional[str] = None  # OWS wallet name
        self._agent_wallet_registered: Optional[bool] = None  # lazy: cached check whether
        # the OWS wallet is registered in user_agent_wallets (per-agent-wallets feature).
        # Used to decide whether to inject wallet_address into the trade payload — see
        # _execute_polymarket_trade(). When None, the check has not yet been performed.

        # OWS wallet: param > env var > fall through to raw key
        _ows_env = os.environ.get("OWS_WALLET")
        effective_ows = ows_wallet or _ows_env
        if effective_ows:
            try:
                from simmer_sdk.ows_utils import get_ows_wallet_address
                self._ows_wallet = effective_ows
                self._wallet_address = get_ows_wallet_address(effective_ows)
                logger.info(
                    "OWS wallet mode: wallet '%s', address %s",
                    effective_ows,
                    self._wallet_address[:10] + "..." if self._wallet_address else "unknown"
                )
            except ImportError:
                logger.warning(
                    "OWS wallet '%s' specified but open-wallet-standard not installed. "
                    "Install with: pip install open-wallet-standard",
                    effective_ows
                )
                self._ows_wallet = None
            except ValueError as e:
                raise ValueError(f"OWS wallet error: {e}")

        # EVM key: only if OWS wallet not configured
        # Check WALLET_PRIVATE_KEY first, fall back to deprecated SIMMER_PRIVATE_KEY
        if not self._ows_wallet:
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
        _py_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        _runtime = _detect_runtime()
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": f"simmer-sdk/{_sdk_version} (python/{_py_version}; runtime/{_runtime})",
        })

        # Auto-detect skill slug + version from caller's SKILL.md
        self._skill_slug = None
        self._skill_version = None
        self._skill_dir = None
        try:
            import inspect
            from pathlib import Path
            caller_file = inspect.stack()[1].filename
            skill_dir = Path(caller_file).parent
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                self._skill_slug, self._skill_version = self._parse_skill_md(skill_md)
                self._skill_dir = skill_dir
        except Exception:
            pass

        # Verify skill entrypoint integrity against published hash
        if self._skill_slug and self._skill_dir:
            try:
                self._verify_skill_integrity(self._skill_dir)
            except RuntimeError:
                raise
            except Exception:
                pass

        # Cache for auto_redeem toggle (TTL: 5 minutes)
        self._auto_redeem_enabled: bool = True
        self._auto_redeem_enabled_fetched_at: float = 0.0

        self.live = live
        self._paper_portfolio = None
        if not self.live:
            from .paper import PaperPortfolio
            self._paper_portfolio = PaperPortfolio(starting_balance=starting_balance)
            logger.info(
                "Paper trading mode enabled (venue=%s, balance=%.2f). "
                "Trades will be simulated with real market data.",
                venue, starting_balance
            )

        # Auto-process risk alerts on init (external wallets only)
        if self.live and (self._private_key or self._ows_wallet) and venue in ("polymarket",):
            try:
                self._process_risk_alerts()
            except Exception as e:
                logger.warning("Risk alert check failed: %s", e)

    def __repr__(self):
        return f"SimmerClient(venue={self.venue!r}, base_url={self.base_url!r})"

    @classmethod
    def from_env(cls, **kwargs) -> "SimmerClient":
        """Construct a client by reading SIMMER_API_KEY from the environment.

        The standard `__init__` path already auto-detects ``WALLET_PRIVATE_KEY``
        (external EVM wallet) and ``OWS_WALLET`` (OWS-managed wallet) when set,
        so this classmethod only needs to surface ``SIMMER_API_KEY`` itself.
        Calling code never has to touch ``os.environ`` directly — useful for
        skill bundles where direct env reads trip surface-area scanners.

        Args:
            **kwargs: Optional keyword arguments forwarded to ``__init__``
                (e.g. ``venue``, ``base_url``, ``live``, ``starting_balance``).
                Do not pass ``api_key`` here — use the regular constructor if
                you want to override the env var.

        Returns:
            A configured ``SimmerClient`` instance.

        Raises:
            RuntimeError: If ``SIMMER_API_KEY`` is unset or empty.

        Example:
            >>> # Sim trading (default)
            >>> client = SimmerClient.from_env()
            >>>
            >>> # Polymarket with auto-detected WALLET_PRIVATE_KEY
            >>> client = SimmerClient.from_env(venue="polymarket")
        """
        api_key = os.environ.get("SIMMER_API_KEY")
        if not api_key:
            raise RuntimeError(
                "SIMMER_API_KEY environment variable is not set. "
                "Get an API key at simmer.markets/dashboard → SDK tab, "
                "then export SIMMER_API_KEY=sk_live_... in your environment."
            )
        return cls(api_key=api_key, **kwargs)

    @classmethod
    def with_ows_wallet(
        cls,
        name: str,
        *,
        api_key: Optional[str] = None,
        **kwargs,
    ) -> "SimmerClient":
        """Construct a client routed through an OWS-managed wallet.

        OWS (Open Wallet Standard) keeps the private key in a vault — orders
        are signed by the OWS daemon, never by the SDK. The wallet ``name`` is
        passed explicitly here so callers don't have to read ``OWS_WALLET``
        from the environment themselves.

        Args:
            name: OWS wallet name (e.g. ``"my-agent-wallet"``). Created via
                ``ows wallet create --name <name>``.
            api_key: Simmer SDK API key (``sk_live_...``). If ``None``, falls
                back to ``SIMMER_API_KEY`` from the environment.
            **kwargs: Optional keyword arguments forwarded to ``__init__``
                (e.g. ``venue``, ``base_url``, ``live``). Do not pass
                ``ows_wallet`` here — it is set from ``name``.

        Returns:
            A configured ``SimmerClient`` instance with ``ows_wallet=name``.

        Raises:
            RuntimeError: If ``api_key`` is None and ``SIMMER_API_KEY`` is
                unset or empty.

        Example:
            >>> client = SimmerClient.with_ows_wallet("my-agent")
            >>> # Or with explicit api_key:
            >>> client = SimmerClient.with_ows_wallet(
            ...     "my-agent", api_key="sk_live_...", venue="polymarket"
            ... )
        """
        if api_key is None:
            api_key = os.environ.get("SIMMER_API_KEY")
        if not api_key:
            raise RuntimeError(
                "No api_key provided and SIMMER_API_KEY environment variable "
                "is not set. Get an API key at simmer.markets/dashboard → SDK "
                "tab, then either pass api_key=... or export SIMMER_API_KEY."
            )
        return cls(api_key=api_key, ows_wallet=name, **kwargs)

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
        return self._private_key is not None or self._ows_wallet is not None

    @property
    def solana_wallet_address(self) -> Optional[str]:
        """Get the Solana wallet address (only available when SOLANA_PRIVATE_KEY is set)."""
        return self._solana_wallet_address

    @property
    def has_solana_wallet(self) -> bool:
        """Check if client is configured for external Solana wallet trading (Kalshi)."""
        return self._solana_key_available

    # ==========================================
    # SKILL VERSION DETECTION
    # ==========================================

    @staticmethod
    def _parse_skill_md(path):
        """Parse name and version from SKILL.md YAML frontmatter."""
        from pathlib import Path
        try:
            text = Path(path).read_text(encoding="utf-8")
            if not text.startswith("---"):
                return None, None
            end = text.index("---", 3)
            frontmatter = text[3:end]
            slug = None
            version = None
            for line in frontmatter.split("\n"):
                stripped = line.strip()
                if stripped.startswith("name:") and slug is None:
                    slug = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                elif stripped.startswith("version:"):
                    version = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            return slug, version
        except Exception:
            return None, None

    def _verify_skill_integrity(self, skill_dir: "Path"):
        """Verify local skill entrypoint hash matches the backend-published hash.

        Raises RuntimeError on mismatch. Logs warning and allows on NULL hash
        or backend unreachable.
        """
        try:
            from pathlib import Path
            import json as _json

            clawhub_json_path = skill_dir / "clawhub.json"
            if not clawhub_json_path.exists():
                logger.debug("No clawhub.json found — skipping integrity check")
                return

            clawhub_data = _json.loads(clawhub_json_path.read_text(encoding="utf-8"))
            entrypoint = (clawhub_data.get("automaton") or {}).get("entrypoint")
            if not entrypoint:
                logger.debug("No automaton.entrypoint in clawhub.json — skipping integrity check")
                return

            entrypoint_path = skill_dir / entrypoint
            if not entrypoint_path.exists():
                logger.warning(f"Entrypoint '{entrypoint}' not found at {entrypoint_path}")
                return

            local_content = entrypoint_path.read_text(encoding="utf-8")
            local_hash = hashlib.sha256(local_content.encode("utf-8")).hexdigest()

            if not self._skill_slug:
                return

            try:
                resp = self._session.get(
                    f"{self.base_url}/api/sdk/skills",
                    timeout=5,
                )
                if resp.status_code == 200:
                    skills_data = resp.json()
                    skills_list = skills_data.get("skills", [])
                    published_hash = None
                    for s in skills_list:
                        if s.get("id") == self._skill_slug:
                            published_hash = s.get("content_hash")
                            break

                    if published_hash is None:
                        logger.warning(
                            f"Skill '{self._skill_slug}' has no published hash — "
                            "integrity not verified"
                        )
                        return

                    if local_hash != published_hash:
                        raise RuntimeError(
                            f"Skill '{self._skill_slug}' entrypoint integrity check failed. "
                            f"Local hash ({local_hash[:12]}...) does not match published hash "
                            f"({published_hash[:12]}...). The skill may have been modified or "
                            f"the publisher released an update. "
                            f"Run 'clawhub install {self._skill_slug}' to update."
                        )

                    logger.info(f"Skill '{self._skill_slug}' integrity verified")

            except RuntimeError:
                raise
            except Exception as e:
                logger.warning(
                    f"Could not verify skill integrity (backend unreachable): {e}"
                )

        except RuntimeError:
            raise
        except Exception as e:
            logger.debug(f"Skill integrity check skipped: {e}")

    # ==========================================
    # RISK ALERT AUTO-PROCESSING
    # ==========================================

    def _process_risk_alerts(self, alerts: list = None):
        """Check for and execute triggered risk exits (called on init for external wallets).

        If alerts is provided, processes those directly. Otherwise fetches from /api/sdk/risk-alerts.
        """
        if alerts is None:
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
            alert_venue = alert.get("venue")  # None for legacy alerts — falls back to client default

            try:
                # 1. Cancel open orders on this market (Polymarket only — token_id based)
                if token_id:
                    self._cancel_orders_for_token(token_id)

                # 2. Execute the sell
                result = self.trade(
                    market_id=market_id,
                    side=side,
                    shares=shares,
                    action="sell",
                    order_type="FAK",
                    venue=alert_venue,
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

    def get_briefing(self, since: str = None, process_risk_alerts: bool = True,
                     skill_versions: dict = None) -> dict:
        """Fetch the agent briefing and optionally process any triggered risk alerts.

        Args:
            since: ISO timestamp — only show changes since this time.
            process_risk_alerts: If True and triggered_risk_alerts are present,
                execute the pending SL/TP exits automatically.
            skill_versions: Optional dict of {slug: version} to check for updates.
                If not provided, uses auto-detected skill from SKILL.md.

        Returns:
            The briefing response dict.
        """
        import json as _json
        params = {}
        if since:
            params["since"] = since

        # Send skill version for upgrade check (explicit override or auto-detected)
        _sv = skill_versions
        if not _sv and self._skill_slug and self._skill_version:
            _sv = {self._skill_slug: self._skill_version}
        if _sv:
            params["skill_versions"] = _json.dumps(_sv)

        response = self._request("GET", "/api/sdk/briefing", params=params)

        # Process triggered risk alerts if present and requested
        triggered = response.get("triggered_risk_alerts")
        if process_risk_alerts and triggered and self._private_key:
            self._process_risk_alerts(alerts=triggered)

        # Log skill update nudges
        skill_updates = response.get("skill_updates")
        if skill_updates:
            for upd in skill_updates:
                print(f"[SimmerSDK] Update available: {upd['slug']} {upd['current']} -> {upd['latest']} — run: clawhub install {upd['slug']}")

        return response

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
        if not (self._ows_wallet or self._private_key) or not self._wallet_address:
            return

        # If we've already confirmed it's linked, skip
        if self._wallet_linked is True:
            return

        # Check if wallet is already linked via API. The link-status check and
        # the credential-derivation call are kept in separate try blocks so a
        # credential failure (which now propagates from _ensure_clob_credentials
        # rather than being silently swallowed) doesn't fall through to the
        # auto-link path. link_wallet() also calls _ensure_clob_credentials at
        # the end, so re-linking would just re-attempt the same derive.
        already_linked = False
        try:
            settings = self._request("GET", "/api/sdk/settings")
            linked_address = settings.get("linked_wallet_address") or settings.get("wallet_address")
            if linked_address and linked_address.lower() == self._wallet_address.lower():
                already_linked = True
        except Exception as e:
            logger.debug("Could not check wallet link status: %s", e)

        if already_linked:
            self._wallet_linked = True
            logger.debug("Wallet %s already linked", self._wallet_address[:10] + "...")
            self._ensure_clob_credentials()
            return

        # Wallet not linked - attempt to link automatically. link_wallet()
        # calls _ensure_clob_credentials() internally on success, so don't
        # call it again here.
        print(f"Auto-linking wallet {self._wallet_address[:10]}... to Simmer account...")
        try:
            result = self.link_wallet(signature_type=0)
            if result.get("success"):
                self._wallet_linked = True
                print("Wallet linked successfully")
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

        Uses OWS (preferred) or py_clob_client to derive credentials, then
        sends them to the backend for encrypted storage. One-time per wallet.
        """
        if not (self._ows_wallet or self._private_key) or not self._wallet_address:
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

        # Phase 1: derive creds locally against Polymarket. This can fail when
        # Polymarket's /auth/api-key route is Cloudflare-blocked from the
        # user's IP (commonly residential AU, SE Asia, etc.). On failure here
        # we fall through to phase 2 (proxy derive). Phase 1 and the backend
        # registration call (phase 1b) are kept in separate try-blocks so a
        # registration failure does NOT silently fall through to the proxy
        # path — that would mask a server-side bug as a CF block.
        creds = None
        try:
            if self._ows_wallet:
                from simmer_sdk.ows_utils import ows_derive_clob_creds
                creds = ows_derive_clob_creds(self._ows_wallet)
            else:
                from py_clob_client.client import ClobClient
                client = ClobClient(
                    host="https://clob.polymarket.com",
                    key=self._private_key,
                    chain_id=137,
                    signature_type=0,  # EOA
                    funder=self._wallet_address,
                )
                creds = client.create_or_derive_api_creds()
        except ImportError as e:
            raise RuntimeError(
                f"Cannot derive CLOB credentials: {e}. "
                "Install with: pip install py-clob-client"
            ) from e
        except Exception as local_derive_err:
            logger.info(
                "Local CLOB credential derivation failed (%s); falling back to proxy derive",
                local_derive_err,
            )
            try:
                self._derive_creds_via_proxy()
                return
            except Exception as proxy_err:
                raise RuntimeError(
                    f"Failed to derive CLOB credentials (local: {local_derive_err}; proxy: {proxy_err})"
                ) from proxy_err

        # Phase 1b: register the locally-derived creds with the Simmer backend.
        try:
            self._request("POST", "/api/sdk/wallet/credentials", json={
                "api_key": creds.api_key,
                "api_secret": creds.api_secret,
                "api_passphrase": creds.api_passphrase,
            })
        except Exception as register_err:
            raise RuntimeError(
                f"Locally derived CLOB credentials but failed to register with Simmer backend: {register_err}"
            ) from register_err

        self._clob_creds_registered = True
        logger.info("CLOB credentials registered for wallet %s", self._wallet_address[:10] + "...")

    def _derive_creds_via_proxy(self) -> None:
        """
        Derive CLOB credentials by forwarding locally-signed L1 headers
        through the Simmer backend.

        Used when the direct call to Polymarket's /auth/api-key fails for
        reasons unrelated to the signature itself — most commonly a Cloudflare
        block on residential IPs. The user's private key never leaves their
        machine: we build the L1 auth headers locally (signature is a one-time
        challenge bound to a timestamp + nonce, not a transaction) and POST
        only those headers to the backend, which forwards them to Polymarket
        from Railway and stores the resulting creds.
        """
        if self._ows_wallet:
            from simmer_sdk.ows_utils import _clob_level_1_headers, get_ows_wallet_address
            address = get_ows_wallet_address(self._ows_wallet)
            headers = _clob_level_1_headers(self._ows_wallet, address, nonce=0)
        else:
            from py_clob_client.signer import Signer
            from py_clob_client.headers.headers import create_level_1_headers
            signer = Signer(key=self._private_key, chain_id=137)
            headers = create_level_1_headers(signer, nonce=0)

        body = {
            "poly_address": headers["POLY_ADDRESS"],
            "poly_signature": headers["POLY_SIGNATURE"],
            "poly_timestamp": headers["POLY_TIMESTAMP"],
            "poly_nonce": headers["POLY_NONCE"],
        }

        self._request("POST", "/api/sdk/wallet/credentials/derive-via-proxy", json=body)
        self._clob_creds_registered = True
        logger.info(
            "CLOB credentials derived via proxy and registered for wallet %s",
            self._wallet_address[:10] + "..."
        )

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

    def _sign_eip1559_tx_for_broadcast(self, tx_fields: dict) -> str:
        """Sign an EIP-1559 tx via OWS (beta) or raw private key, return broadcast-ready hex.

        Used by set_approvals() and redeem(). Centralizes the signing decision so the
        OWS branch lives in one place. Caller is responsible for ensuring at least
        one signing source is configured.
        """
        if self._ows_wallet:
            from .ows_utils import ows_sign_typed_tx
            return ows_sign_typed_tx(self._ows_wallet, tx_fields)
        from eth_account import Account
        signed = Account.sign_transaction(tx_fields, self._private_key)
        return "0x" + signed.raw_transaction.hex()

    def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json: Optional[Dict] = None,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """Make an authenticated request to the API."""
        url = f"{self.base_url}{endpoint}"
        response = self._session.request(
            method=method,
            url=url,
            params=params,
            json=json,
            timeout=timeout or 30
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
        allow_rebuy: bool = False,
        signal_data: Optional[dict] = None
    ) -> TradeResult:
        """
        Execute a trade on a market.

        Args:
            market_id: Market ID to trade on
            side: 'yes' or 'no'
            amount: Amount to spend (for buys) — USDC for polymarket/kalshi, $SIM for sim
            shares: Number of shares to sell (for sells)
            action: 'buy' or 'sell' (default: 'buy')
            venue: Override client's default venue for this trade.
                - "sim": Simmer LMSR, $SIM virtual currency ("simmer" accepted as alias)
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
            signal_data: Optional structured signal data for backtest replay.
                Flat dict with string/numeric values. Common fields: edge (float),
                confidence (float 0-1), signal_source (string). Skill-specific
                fields are freeform. Example: {"edge": 0.15, "confidence": 0.8,
                "signal_source": "noaa", "forecast_temp": 35}

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

        # Validate decimal precision before submission.
        # Maker (USDC) supports max 2 decimals; shares support max 5.
        if not is_sell:
            if round(amount, 2) != amount:
                raise ValueError(
                    f"amount {amount} has too many decimal places (max 2). "
                    f"Use {round(amount, 2)} instead."
                )
        else:
            if round(shares, 5) != shares:
                raise ValueError(
                    f"shares {shares} has too many decimal places (max 5). "
                    f"Use {round(shares, 5)} instead."
                )

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
        if signal_data:
            payload["signal_data"] = signal_data
        if price is not None:
            payload["price"] = price

        # Include wallet_address only for users who explicitly opted into per-agent
        # wallet attribution by registering this OWS wallet (Elite feature).
        # Otherwise the server would reject with "Agent wallet not found" because
        # the wallet has no row in user_agent_wallets — but the user-level
        # linked_wallet_address path still works fine. Don't conflate "OWS configured"
        # with "wants per-agent isolation."
        if self._ows_wallet and self._wallet_address and self._is_agent_wallet_registered():
            payload["wallet_address"] = self._wallet_address

        # External wallet: ensure linked, check approvals, sign locally
        if (self._private_key or self._ows_wallet) and effective_venue == "polymarket":
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

        # Build TradeResult from a server response. Extracted as a closure so
        # the cred-recovery retry path below can rebuild from a second response
        # without duplicating the field mapping. Sim-venue is the only one that
        # returns a balance (in `position.sim_balance`); real venues use
        # get_portfolio() instead.
        def _build_result(d):
            _pos = d.get("position") or {}
            _bal = _pos.get("sim_balance") if effective_venue == "sim" else None
            return TradeResult(
                success=d.get("success", False),
                trade_id=d.get("trade_id"),
                market_id=d.get("market_id", market_id),
                side=d.get("side", side),
                venue=effective_venue,
                shares_bought=d.get("shares_bought", 0),
                shares_requested=d.get("shares_requested", 0),
                order_status=d.get("order_status"),
                cost=d.get("cost", 0),
                new_price=d.get("new_price", 0),
                balance=_bal,
                error=d.get("error"),
                fill_status=d.get("fill_status", "unknown"),
            )

        result = _build_result(data)

        # Auto-recover from stale CLOB creds (Polymarket rotated server-side or
        # rejected our cached creds). Only relevant for external/OWS Polymarket
        # — managed wallets re-derive server-side on the same error. The server
        # NULLs its cached creds on this same condition, so our next call to
        # _ensure_clob_credentials() finds has_credentials=False and triggers a
        # local re-derive + register. We bypass the SDK's own one-shot cache
        # (`_clob_creds_registered`) by resetting it. Single retry only — if
        # the retry also fails, surface the original style of error.
        if (not result.success and result.error
                and effective_venue == "polymarket"
                and (self._private_key or self._ows_wallet)):
            err_lower = result.error.lower()
            if "unauthorized" in err_lower or "invalid api key" in err_lower:
                logger.warning(
                    "Polymarket rejected CLOB creds — re-deriving and retrying once"
                )
                try:
                    self._clob_creds_registered = False
                    self._ensure_clob_credentials()
                    retry_data = self._request(
                        "POST", "/api/sdk/trade", json=payload
                    )
                    result = _build_result(retry_data)
                    if result.success:
                        logger.info("Trade succeeded after cred re-derive")
                except Exception as retry_err:
                    logger.warning(
                        "Cred re-derive + retry failed: %s", retry_err
                    )

        if result.success and self._held_markets_cache is not None:
            if action == "buy":
                # Update cache locally instead of nuking — avoids a fresh GET /positions
                # on the next trade() call in a loop
                existing = self._held_markets_cache.get(market_id, [])
                if source and source not in existing:
                    existing = existing + [source]
                self._held_markets_cache[market_id] = existing
            elif action == "sell":
                # Remove from cache so subsequent buys aren't blocked
                self._held_markets_cache.pop(market_id, None)
        elif not result.success and result.error:
            # Surface failures to bots that don't check result.success themselves.
            # A silent loop of failing trades (e.g. upstream creds rejected) can
            # otherwise run for hours unnoticed — this gives any bot using stdlib
            # logging a stderr signal at WARNING level on the first failure.
            logger.warning(
                "Trade failed on %s: %s", effective_venue, result.error
            )
        return result

    # Default half-spread for Polymarket paper trades (in probability units).
    # Real Polymarket spreads are typically 1-3 cents; 1 cent per side is
    # conservative.  Overridden when the market returns ``spread_cents``.
    _POLY_PAPER_DEFAULT_HALF_SPREAD = 0.01

    def _paper_trade(self, market_id, side, amount, shares, action, venue):
        """Simulate a trade using real market prices.

        For Polymarket venues, models the CLOB bid-ask spread so paper P&L
        is closer to what a live FAK order would experience.  Buys fill at
        the ask (mid + half-spread), sells at the bid (mid - half-spread).
        """
        import time as _time

        # Auto-settle any resolved paper positions before trading
        self._settle_paper_positions()

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
        mid_price = float(market.get("external_price_yes") or market.get("current_probability") or 0.5)
        if side == "no":
            mid_price = 1.0 - mid_price
        mid_price = max(mid_price, 0.001)  # Floor to avoid division by zero

        # Polymarket CLOB spread modeling — buys fill at ask, sells at bid
        is_polymarket = venue in ("polymarket",)
        if is_polymarket:
            spread_raw = market.get("spread_cents")
            if spread_raw is not None:
                half_spread = float(spread_raw) / 100.0 / 2.0  # cents → probability
            else:
                half_spread = self._POLY_PAPER_DEFAULT_HALF_SPREAD
            if action == "buy":
                fill_price = min(mid_price + half_spread, 0.999)
            else:
                fill_price = max(mid_price - half_spread, 0.001)
        else:
            fill_price = mid_price

        if action == "buy":
            cost = amount
            # Check paper balance
            if cost > self._paper_portfolio.balance:
                return TradeResult(
                    success=False, market_id=market_id, side=side,
                    error=f"Insufficient paper balance ({self._paper_portfolio.balance:.2f} < {cost:.2f})",
                    simulated=True,
                    balance=round(self._paper_portfolio.balance, 4),
                )
            shares_filled = amount / fill_price
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
            cost = shares_filled * fill_price

        self._paper_portfolio.log_trade(market_id, side, action, shares_filled, cost, fill_price, venue=venue)

        return TradeResult(
            success=True,
            trade_id=f"paper_{int(_time.time())}",
            market_id=market_id,
            side=side,
            venue=venue,
            shares_bought=round(shares_filled, 6),
            shares_requested=round(shares_filled, 6),
            order_status="simulated",
            cost=round(cost, 4),
            new_price=mid_price,
            simulated=True,
            balance=round(self._paper_portfolio.balance, 4),
            fill_status="filled",
        )

    # ==========================================
    # PAPER TRADING HELPERS
    # ==========================================

    def _get_paper_positions(self) -> List[Position]:
        """Build Position list from in-memory paper portfolio."""
        positions = []
        for mid, pos in self._paper_portfolio.positions.items():
            if pos.shares_yes <= 0 and pos.shares_no <= 0:
                continue
            # Fetch live price for current value estimate
            price_yes = 0.5
            question = mid
            try:
                ctx = self.get_market_context(mid)
                if ctx and "market" in ctx:
                    m = ctx["market"]
                    price_yes = float(
                        m.get("external_price_yes")
                        or m.get("current_probability")
                        or 0.5
                    )
                    question = m.get("question", mid)
            except Exception:
                pass
            current_value = (pos.shares_yes * price_yes) + (pos.shares_no * (1 - price_yes))
            pnl = current_value - pos.total_cost
            positions.append(Position(
                market_id=mid,
                question=question,
                shares_yes=pos.shares_yes,
                shares_no=pos.shares_no,
                current_value=round(current_value, 4),
                pnl=round(pnl, 4),
                status="active",
                venue=pos.venue,
                cost_basis=round(pos.total_cost, 4),
                current_price=price_yes,
            ))
        return positions

    def _settle_paper_positions(self):
        """Check open paper positions for resolved markets and settle them."""
        if self._paper_portfolio is None:
            return
        open_ids = self._paper_portfolio.get_open_market_ids()
        if not open_ids:
            return
        for mid in open_ids:
            try:
                ctx = self.get_market_context(mid)
                if not ctx or "market" not in ctx:
                    continue
                m = ctx["market"]
                status = m.get("status", "")
                if status != "resolved":
                    continue
                # Determine outcome from resolved market data
                outcome_raw = m.get("outcome")
                if outcome_raw is True or outcome_raw == "yes" or outcome_raw == "Yes":
                    outcome = "yes"
                elif outcome_raw is False or outcome_raw == "no" or outcome_raw == "No":
                    outcome = "no"
                else:
                    # Infer from probability (1.0 = yes, 0.0 = no)
                    prob = float(m.get("current_probability", 0.5))
                    if prob >= 0.99:
                        outcome = "yes"
                    elif prob <= 0.01:
                        outcome = "no"
                    else:
                        continue  # Not clearly resolved
                self._paper_portfolio.settle(mid, outcome)
            except Exception as e:
                logger.debug("Could not check resolution for %s: %s", mid, e)

    def get_paper_summary(self) -> Optional[dict]:
        """Return paper portfolio summary, or None if not in paper mode.

        Returns:
            Dict with starting_balance, balance, total_pnl, open_positions,
            settled_positions, and per-market position details.
        """
        if self._paper_portfolio is None:
            return None
        self._settle_paper_positions()
        return self._paper_portfolio.summary()

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
            amount: USDC amount to spend

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

        In paper mode, returns simulated positions from the in-memory
        portfolio and auto-settles any markets that have resolved.

        Args:
            venue: Filter by venue ("sim" or "polymarket"). If None, returns both.
            source: Filter by trade source (e.g., "weather", "copytrading"). Partial match.

        Returns:
            List of Position objects with P&L info
        """
        # Paper mode: return in-memory positions (auto-settle first)
        if not self.live and self._paper_portfolio is not None:
            self._settle_paper_positions()
            return self._get_paper_positions()

        params = {}
        if venue:
            params["venue"] = venue
        if source:
            params["source"] = source

        data = self._request("GET", "/api/sdk/positions", params=params if params else None)

        positions = []
        for p in data.get("positions", []):
            pos_venue = p.get("venue", "sim")
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
        if not self.live and self._paper_portfolio is not None:
            self._settle_paper_positions()
            return self._paper_portfolio.total_pnl
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
            Dict with market_id, question, status (imported/already_exists/resolved),
            and import details. Inspect `status` before assuming the market is tradeable.

        Rate Limits:
            - Free tier:  10/day, 10/minute per agent
            - Pro tier:   100/day per agent
            - Elite tier: 250/day per agent
            - On 429, response includes `x402_url` — pay $0.005/import in USDC
              on Base for unlimited overflow.
            - Requires claimed agent.

        Tip:
            Pre-flight with `check_market_exists(url=...)` to avoid wasting
            quota on already-imported markets — that endpoint is free.

        Example:
            # Avoid wasting quota on already-imported markets
            check = client.check_market_exists(url="https://polymarket.com/event/will-x-happen")
            if check["exists"]:
                market_id = check["market_id"]
            else:
                result = client.import_market("https://polymarket.com/event/will-x-happen")
                market_id = result["market_id"]

            # Trade on it (simmer - $SIM)
            client.trade(market_id=market_id, side="yes", amount=10)

            # Or trade real money
            client.trade(market_id=market_id, side="yes", amount=50, venue="polymarket")
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
            Dict with market_id, question, kalshi_ticker, status
            (imported/already_exists/resolved), and import details.

        Rate Limits:
            - Free tier:  10/day, 10/minute per agent
            - Pro tier:   100/day per agent
            - Elite tier: 250/day per agent
            - On 429, response includes `x402_url` — pay $0.005/import in USDC
              on Base for unlimited overflow.
            - Requires claimed agent.

        Tip:
            Pre-flight with `check_market_exists(ticker=...)` to avoid wasting
            quota on already-imported markets — that endpoint is free.

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

    def import_kalshi_event(self, event_ticker: str) -> Dict[str, Any]:
        """
        Import all outcomes of a Kalshi event at once.

        Instead of importing each binary contract one by one, this imports the
        entire event (e.g. all temperature brackets for a weather market) in a
        single call. Counts as **1 daily import** regardless of outcome count.

        Accepts an event ticker (e.g. "kxhightnola-26apr01") or a full Kalshi
        URL containing an event ticker.

        After importing, each outcome is a separate Simmer market that can be
        traded independently.

        Args:
            event_ticker: Kalshi event ticker or full Kalshi event URL
                Examples:
                    "kxhightnola-26apr01"
                    "https://kalshi.com/markets/kxhightnola-26apr01"

        Returns:
            Dict with:
                - event_id: Simmer event ID
                - event_name: Event title
                - markets: List of imported market dicts (market_id, question, kalshi_ticker, current_probability)
                - markets_imported: Count of newly imported markets
                - markets_skipped: Count of skipped markets (closed, extreme prices)
                - status: "imported" or "already_exists"

        Rate Limits:
            - Counts as 1 import toward daily limit (10/day, 50 for pro)

        Example:
            result = client.import_kalshi_event("kxhightnola-26apr01")
            print(f"Imported {result['markets_imported']} markets from {result['event_name']}")
            for m in result['markets']:
                print(f"  {m['kalshi_ticker']}: {m['question']}")
        """
        data = self._request(
            "POST",
            "/api/sdk/markets/import/kalshi/event",
            json={"event_ticker": event_ticker}
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

    def check_market_exists(
        self,
        url: Optional[str] = None,
        condition_id: Optional[str] = None,
        ticker: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Check if a market has already been imported to Simmer.
        Does not consume import quota.

        Args:
            url: Polymarket or Kalshi market URL
            condition_id: Polymarket condition ID
            ticker: Kalshi market ticker

        Returns:
            Dict with 'exists' (bool), and if exists: 'market_id', 'question', 'status'

        Example:
            result = client.check_market_exists(url="https://polymarket.com/event/bitcoin-100k")
            if not result["exists"]:
                client.import_market("https://polymarket.com/event/bitcoin-100k")
        """
        params = {}
        if url:
            params["url"] = url
        if condition_id:
            params["condition_id"] = condition_id
        if ticker:
            params["ticker"] = ticker
        return self._request("GET", "/api/sdk/markets/check", params=params)

    def get_trades(
        self,
        venue: str = "all",
        limit: int = 50,
        offset: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """
        Get trade history across venues.

        Args:
            venue: Venue filter. One of 'all' (default), 'sim', 'polymarket',
                or 'kalshi'. The default 'all' returns merged sim + polymarket
                + kalshi trades sorted by timestamp; pass a specific venue to
                filter. Each row in the response is tagged with `venue`.
            limit: Max trades to return (1-200, default 50)
            offset: Pagination offset (default 0)

        Returns:
            Dict containing:
            - trades: List of trade rows, each tagged with `venue`
            - total_count: Total matching trades across all filtered venues

        Example:
            # Cross-venue (default):
            history = client.get_trades(limit=20)
            for t in history['trades']:
                print(f"{t['venue']}: {t['side']} {t['shares']}")

            # Single venue:
            poly_trades = client.get_trades(venue="polymarket")
        """
        return self._request(
            "GET",
            "/api/sdk/trades",
            params={"venue": venue, "limit": limit, "offset": offset},
        )

    def get_portfolio(self, venue: str = "all") -> Optional[Dict[str, Any]]:
        """
        Get portfolio summary with per-venue buckets + legacy flat fields.

        Args:
            venue: Venue filter. One of 'all' (default), 'sim', 'polymarket',
                or 'kalshi'. The default 'all' returns every venue bucket;
                pass a specific venue to compute only that bucket.

        Returns:
            Dict containing (all fields optional):
            - sim, polymarket, kalshi: per-venue buckets with
              {balance, pnl, positions_count, total_exposure}
            - total: {positions_count, total_exposure} summed across venues
            - balance_usdc, sim_balance, sim_pnl, positions_count, total_exposure:
              legacy flat fields (deprecated but still populated)
            - by_source: Breakdown by trade source (polymarket only)

        Example:
            # Cross-venue (default):
            portfolio = client.get_portfolio()
            for v in ('sim', 'polymarket', 'kalshi'):
                b = portfolio.get(v) or {}
                print(f"{v}: {b.get('positions_count', 0)} positions")

            # Single venue:
            poly = client.get_portfolio(venue="polymarket")
            print(f"Polymarket exposure: ${poly['polymarket']['total_exposure']}")
        """
        return self._request(
            "GET", "/api/sdk/portfolio", params={"venue": venue}
        )

    def ensure_can_trade(
        self,
        min_usd: float = 1.0,
        venue: Optional[str] = None,
        safety_buffer: float = 0.02,
    ) -> Dict[str, Any]:
        """
        Pre-flight balance check for trading skills.

        One status fetch replaces many failed trade round-trips when a wallet
        is underfunded. Skills should call this once per run before discovering
        markets / placing orders. Result is collateral-agnostic — `balance`
        reflects the active collateral token (pUSD on V2, USDC.e on V1) per
        the server's `exchange_version`.

        Args:
            min_usd: Minimum viable trade size in active collateral. If wallet
                balance is below this, returns `ok=False, reason="insufficient_balance"`
                so the skill can skip cleanly instead of looping on rejected orders.
            venue: Venue to check. Defaults to client's venue. Only "polymarket"
                does a balance check today; other venues short-circuit to ok=True.
            safety_buffer: Fraction of balance to keep as fee buffer. Default 0.02
                (2%). `max_safe_size = balance * (1 - safety_buffer)` is the
                largest order size the skill should place to leave room for
                Polymarket fees + price slippage.

        Returns:
            Dict containing:
            - ok (bool): True if balance >= min_usd (or non-polymarket venue)
            - balance (float): Active collateral balance in USD-equivalent units
            - collateral (str): "pUSD" (V2), "USDC.e" (V1), or "" (non-polymarket)
            - exchange_version (str): "v1" or "v2" — matches server-side flag
            - reason (str): "ok" | "insufficient_balance" | "no_wallet" |
              "balance_unavailable" | "skipped_non_polymarket"
            - max_safe_size (float): balance * (1 - safety_buffer); 0 when not ok

        Example:
            preflight = client.ensure_can_trade(min_usd=2.0)
            if not preflight["ok"]:
                print(f"Skip: {preflight['reason']} (balance ${preflight['balance']:.2f})")
                return  # emit automaton skip and exit
            order_size = min(MY_MAX_BET, preflight["max_safe_size"])
        """
        effective_venue = venue or self.venue

        # Non-polymarket venues: paper trading or kalshi. Skip the check —
        # caller's existing flow handles balance differently (sim is virtual,
        # kalshi uses Solana balance which has its own preflight elsewhere).
        if effective_venue != "polymarket":
            return {
                "ok": True,
                "balance": 0.0,
                "collateral": "",
                "exchange_version": "",
                "reason": "skipped_non_polymarket",
                "max_safe_size": float("inf"),
            }

        # Active collateral label — matches server-side exchange_version flag.
        # Imported lazily so the SDK doesn't pay the cost on every import.
        try:
            from .polymarket_contracts import exchange_version_str
            ev = exchange_version_str()
        except Exception:
            ev = "v2"  # safe default post-cutover (2026-04-28)
        collateral_label = "pUSD" if ev == "v2" else "USDC.e"

        try:
            portfolio = self.get_portfolio(venue="polymarket")
        except Exception as e:
            return {
                "ok": False,
                "balance": 0.0,
                "collateral": collateral_label,
                "exchange_version": ev,
                "reason": "balance_unavailable",
                "max_safe_size": 0.0,
            }

        if not portfolio:
            return {
                "ok": False,
                "balance": 0.0,
                "collateral": collateral_label,
                "exchange_version": ev,
                "reason": "balance_unavailable",
                "max_safe_size": 0.0,
            }

        poly_bucket = portfolio.get("polymarket") or {}
        # Per-venue bucket is the source of truth; balance_usdc is the legacy
        # mirror but per-venue lets us distinguish "no wallet linked" cleanly.
        raw_balance = poly_bucket.get("balance")
        if raw_balance is None:
            # bucket present but balance None = wallet not linked OR RPC failed.
            # Server returns null balance when balance fetch fails (RPC outage)
            # vs 0.0 when wallet is genuinely empty. Distinguish:
            balance_usdc = portfolio.get("balance_usdc")
            if balance_usdc is None:
                # Try to detect "no wallet" vs RPC failure: check warnings list
                # is one heuristic, but simplest is to treat null as RPC issue.
                # No wallet → polymarket bucket itself is None (handled above).
                return {
                    "ok": False,
                    "balance": 0.0,
                    "collateral": collateral_label,
                    "exchange_version": ev,
                    "reason": "balance_unavailable",
                    "max_safe_size": 0.0,
                }
            balance = float(balance_usdc)
        else:
            balance = float(raw_balance)

        max_safe = round(balance * (1.0 - safety_buffer), 2)
        if balance < min_usd:
            return {
                "ok": False,
                "balance": balance,
                "collateral": collateral_label,
                "exchange_version": ev,
                "reason": "insufficient_balance",
                "max_safe_size": 0.0,
            }

        return {
            "ok": True,
            "balance": balance,
            "collateral": collateral_label,
            "exchange_version": ev,
            "reason": "ok",
            "max_safe_size": max_safe,
        }

    def get_market_context(
        self,
        market_id: str,
        venue: str = "all",
        my_probability: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get market context with per-venue positions + trading safeguards.

        Args:
            market_id: Market ID to get context for
            venue: Venue filter. One of 'all' (default), 'sim', 'polymarket',
                or 'kalshi'. The default 'all' returns positions across every
                venue simultaneously; pass a specific venue to filter.
            my_probability: Your probability estimate (0-1) for edge calculation.

        Returns:
            Dict containing:
            - market: Market details (question, prices, resolution criteria)
            - positions: Per-venue container {sim, polymarket, kalshi} — each
              field is None or a position object
            - position: Legacy flat field mirroring a single venue (deprecated)
            - discipline: Trading discipline info (flip-flop detection)
            - slippage: Estimated execution costs
            - edge: Edge analysis (if my_probability provided)
            - warnings: List of warnings

        Example:
            # Cross-venue (default):
            ctx = client.get_market_context(market_id)
            for v in ('sim', 'polymarket', 'kalshi'):
                pos = ctx['positions'].get(v)
                if pos and pos['has_position']:
                    print(f"{v}: {pos['side']} {pos['shares']}")

            # Single venue:
            ctx = client.get_market_context(market_id, venue="sim")
            sim_pos = ctx['positions']['sim']
            if sim_pos and sim_pos['has_position']:
                print(f"Holding {sim_pos['shares']} shares on sim")
        """
        params: Dict[str, Any] = {"venue": venue}
        if my_probability is not None:
            params["my_probability"] = my_probability
        return self._request(
            "GET", f"/api/sdk/context/{market_id}", params=params
        )

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
        For OWS wallets: cancels via CLOB using OWS-derived credentials.
        For managed wallets: cancels via server endpoint.

        Args:
            order_id: The order ID to cancel

        Returns:
            Dict with cancellation result
        """
        if self._ows_wallet:
            try:
                from simmer_sdk.ows_utils import ows_cancel_order
                result = ows_cancel_order(self._ows_wallet, order_id)
                return {"success": True, "order_id": order_id, "result": result}
            except Exception as e:
                return {"success": False, "error": str(e)}
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
        if self._ows_wallet:
            # OWS: cancel all orders (CLOB doesn't support per-market cancel without token_id iteration)
            try:
                from simmer_sdk.ows_utils import ows_cancel_all_orders
                result = ows_cancel_all_orders(self._ows_wallet)
                return {"success": True, "market_id": market_id, "result": result}
            except Exception as e:
                return {"success": False, "error": str(e)}
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
        if self._ows_wallet:
            try:
                from simmer_sdk.ows_utils import ows_cancel_all_orders
                result = ows_cancel_all_orders(self._ows_wallet)
                return {"success": True, "result": result}
            except Exception as e:
                return {"success": False, "error": str(e)}
        if self._private_key:
            return self._cancel_all_local()
        return self._request("DELETE", "/api/sdk/orders")

    def _cancel_order_local(self, order_id: str) -> Dict[str, Any]:
        """Cancel a single order via local py_clob_client.

        Checks the CLOB response to distinguish between a successful cancel
        and a no-op (order was already filled/matched before the cancel arrived).
        """
        try:
            client = self._get_clob_client()
            result = client.cancel(order_id)

            # Polymarket CLOB returns {"canceled": [...]} or {"not_canceled": [...]}
            # If the order wasn't in the canceled list, it was already filled/matched
            not_canceled = []
            canceled = []
            if isinstance(result, dict):
                not_canceled = result.get("not_canceled", [])
                canceled = result.get("canceled", [])

            if not_canceled:
                print(f"[SimmerSDK] ⚠️ Cancel returned not_canceled for {order_id} — order was likely already filled")
                return {
                    "success": False,
                    "order_id": order_id,
                    "result": result,
                    "warning": "Order was not cancelled — it was likely already filled/matched before the cancel reached the CLOB. Check your positions.",
                }

            if canceled:
                print(f"[SimmerSDK] ✓ Order {order_id} cancelled successfully")
                return {"success": True, "order_id": order_id, "result": result}

            # Fallthrough: CLOB returned something unexpected (no canceled/not_canceled keys)
            # Treat as success but log a warning so it's visible
            if isinstance(result, dict) and not canceled and not not_canceled:
                print(f"[SimmerSDK] Cancel for {order_id} returned unexpected response: {result}")

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

        # External / OWS wallet — sign locally (or via OWS vault) and broadcast
        if not self._private_key and not self._ows_wallet:
            raise ValueError(
                "Redemption requires signing. Set WALLET_PRIVATE_KEY env var, pass private_key to constructor, "
                "or configure an OWS wallet (OWS_WALLET env var or ows_wallet constructor arg)."
            )

        try:
            import eth_account  # noqa: F401 — early dep check; signing happens in _sign_eip1559_tx_for_broadcast
        except ImportError:
            raise ImportError(
                "eth-account is required for external/OWS wallet redemption. "
                "Install with: pip install eth-account"
            )

        unsigned_tx = result["unsigned_tx"]

        # Validate unsigned tx before signing.
        #
        # SIM-1389/1421 (server-side, 2026-05-03): redemption now routes through
        # the new Polymarket collateral adapters by default — they pay out in
        # pUSD instead of USDC.e. Both adapters expose the same selector
        # (`0x01b7037c` — `redeemPositions(address,bytes32,bytes32,uint256[])`)
        # as the legacy CTF, so we add their addresses with the same expected
        # selector. The legacy CTF + legacy NegRiskAdapter entries stay so old
        # server versions and any still-USDC.e-bound paths continue to verify.
        _REDEEM_CONTRACT_WHITELIST = {
            "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045".lower(): "0x01b7037c",   # CTF: redeemPositions(address,bytes32,bytes32,uint256[])
            "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296".lower(): "0xdbeccb23",   # NegRiskAdapter: redeemPositions(bytes32,uint256[])
            "0xAdA100Db00Ca00073811820692005400218FcE1f".lower(): "0x01b7037c",   # CtfCollateralAdapter (SIM-1389): pays pUSD instead of USDC.e
            "0xadA2005600Dec949baf300f4C6120000bDB6eAab".lower(): "0x01b7037c",   # NegRiskCtfCollateralAdapter (SIM-1421): same selector + ABI as binary
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

        print(f"  Signing redemption transaction locally...")

        # Use Simmer's RPC proxy for chain queries
        def _rpc_call(method: str, params: list) -> Any:
            resp = self._request("POST", "/api/rpc/polygon", json={
                "jsonrpc": "2.0", "method": method, "params": params, "id": 1,
            })
            return resp.get("result")

        # Estimate gas via RPC (server no longer sends gas in unsigned tx)
        est_result = _rpc_call("eth_estimateGas", [{"from": self._wallet_address, "to": tx_to, "data": tx_data}])
        if est_result:
            tx_gas = int(int(est_result, 16) * 1.3)
        else:
            tx_gas = int(unsigned_tx.get("gas", 300_000))

        # Cap gas limit to prevent POL drain.
        #
        # 2026-05-04: raised from 500k to 1.5M. The new pUSD collateral
        # adapters (added to whitelist in 0.13.3) do extra on-chain work —
        # USDC.e wrap → CTF burn → pUSD mint — that legitimately consumes
        # 500-1000k gas. weather-trader999 reported a wave of "Gas limit
        # too high" failures with eth_estimateGas-derived budgets of
        # 502k–956k for adapter redemptions; all were honest estimates
        # the old cap was clipping. 1.5M gives ~50% headroom for the
        # heaviest adapter calls while still guarding against pathological
        # estimates (1.5M @ ~30 gwei ≈ 0.045 POL ≈ $0.01 worst case).
        if tx_gas > 1_500_000:
            return {"success": False, "error": f"Gas limit too high ({tx_gas}), max 1500000"}

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

        # Sign via OWS or raw key (centralized in helper)
        signed_tx_hex = self._sign_eip1559_tx_for_broadcast(tx_fields)

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
    # SKILL CONFIG (remote overrides)
    # ==========================================

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
            amount: USDC amount (for buys)
            shares: Number of shares (for sells)
            action: 'buy' or 'sell'
            order_type: Order type ('FAK', 'GTC', etc.)
            price: Optional limit price (0.001-0.999). If None, uses current market price.
        """
        if not (self._ows_wallet or self._private_key) or not self._wallet_address:
            return None

        try:
            from .signing import build_and_sign_order, build_and_sign_order_ows
        except ImportError:
            raise ImportError(
                "Local signing requires py_order_utils. "
                "Install with: pip install py-order-utils py-clob-client eth-account"
            )

        is_sell = action == "sell"

        # Get market data to find token IDs, price, and tick_size
        # Cache per session — token IDs, neg_risk, tick_size don't change mid-session.
        # Price may change but is only used as fallback when caller doesn't provide one.
        if market_id in self._market_data_cache:
            market_data = self._market_data_cache[market_id]
        else:
            markets_resp = self._request("GET", f"/api/sdk/markets/{market_id}")
            market_data = markets_resp.get("market") if isinstance(markets_resp, dict) else None
            if not market_data:
                raise ValueError(f"Market {market_id} not found")
            self._market_data_cache[market_id] = market_data

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
        # For V2 FAK/FOK BUY, pass the original USDC `amount` so the
        # market-order builder rounds maker to 2 decimals on a value the
        # caller asked for, not a derived size*price that may shave a cent.
        amount_usdc = float(amount) if (not is_sell and amount > 0) else None
        if self._ows_wallet:
            signed = build_and_sign_order_ows(
                ows_wallet=self._ows_wallet,
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
        else:
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
                amount_usdc=amount_usdc,
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
            amount: USDC amount (for buys)
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
                venue="kalshi",
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
                venue="kalshi",
                error=f"Solana signing not available: {e}"
            )

        is_sell = action == "sell"

        # Auto-register Solana wallet if not yet linked on the server
        if self._solana_wallet_address and not getattr(self, '_solana_wallet_registered', False):
            try:
                settings = self._request("GET", "/api/sdk/user/settings")
                server_wallet = settings.get("solana_wallet_address")
                if server_wallet != self._solana_wallet_address:
                    self._request("PATCH", "/api/sdk/user/settings",
                                  json={"bot_solana_wallet": self._solana_wallet_address})
                    logger.info("Auto-registered Solana wallet %s", self._solana_wallet_address[:10] + "...")
                self._solana_wallet_registered = True
            except Exception as e:
                logger.warning("Could not auto-register Solana wallet: %s", e)

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
                venue="kalshi",
                error=f"Failed to get quote: {e}"
            )

        if not quote.get("success"):
            return TradeResult(
                success=False,
                market_id=market_id,
                side=side,
                venue="kalshi",
                error=quote.get("error", "Failed to get quote from Simmer")
            )

        unsigned_tx = quote.get("transaction")
        if not unsigned_tx:
            return TradeResult(
                success=False,
                market_id=market_id,
                side=side,
                venue="kalshi",
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
                venue="kalshi",
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
                venue="kalshi",
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
        if result.success and self._held_markets_cache is not None:
            if action == "buy":
                existing = self._held_markets_cache.get(market_id, [])
                if source and source not in existing:
                    existing = existing + [source]
                self._held_markets_cache[market_id] = existing
            elif action == "sell":
                self._held_markets_cache.pop(market_id, None)
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
            Dict with success status and wallet info. When `success` is True,
            the dict additionally contains:
              - `clob_credentials_registered` (bool): whether Polymarket CLOB
                API credentials were derived and stored after the link. False
                means trading will fail until creds are derived (the SDK
                retries on the next `trade()` call automatically).
              - `clob_credentials_error` (str, optional): the underlying
                exception message when `clob_credentials_registered` is False.

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
                if not result.get("clob_credentials_registered", True):
                    print(f"Note: creds will derive on first trade")
        """
        if not (self._ows_wallet or self._private_key) or not self._wallet_address:
            raise ValueError(
                "private_key or ows_wallet required for wallet linking. "
                "Initialize client with private_key or ows_wallet parameter."
            )

        if signature_type not in (0, 1, 2):
            raise ValueError(
                f"Invalid signature_type {signature_type}. "
                "Must be 0 (EOA), 1 (Polymarket proxy), or 2 (Gnosis Safe)"
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
        if self._ows_wallet:
            from simmer_sdk.ows_utils import ows_sign_message
            signature = ows_sign_message(self._ows_wallet, message)
        else:
            try:
                from .signing import sign_message
            except ImportError:
                raise ImportError(
                    "Wallet linking requires eth_account. "
                    "Install with: pip install eth-account"
                )
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

        # After link (or re-link reporting "already linked"), ensure CLOB creds
        # are registered. Server-side managed→external migration nulls
        # polymarket_api_creds_encrypted, so the post-migration first link
        # needs a fresh derive — without this, link_wallet() returning success
        # leaves the user with has_credentials=false and trades will fail.
        #
        # The link itself either succeeded or it didn't — that's what
        # `result["success"]` reports. Credential registration is a separate
        # concern: it can fail independently (network, CF block on local
        # derive AND on proxy fallback) without invalidating the wallet link.
        # We surface the credential state in two extra fields so direct
        # callers of link_wallet() can tell which step failed; we don't
        # raise, because that would obscure a successful link.
        if result.get("success"):
            self._wallet_linked = True
            # Force re-derive even if a stale flag from earlier in this session
            # would short-circuit the check.
            self._clob_creds_registered = False
            try:
                self._ensure_clob_credentials()
                result["clob_credentials_registered"] = True
            except Exception as e:
                result["clob_credentials_registered"] = False
                result["clob_credentials_error"] = str(e)
                logger.warning(
                    "Wallet linked, but CLOB credential registration failed: %s. "
                    "Trades will fail until credentials are derived. "
                    "Inspect `result['clob_credentials_error']` or retry by calling "
                    "`client.trade(...)`, which re-attempts derivation.",
                    e
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
        if not self._wallet_address:
            raise ValueError(
                "No wallet configured. Set WALLET_PRIVATE_KEY env var or pass private_key to constructor "
                "(or set OWS_WALLET / pass ows_wallet for OWS-managed signing)."
            )
        if not self._private_key and not self._ows_wallet:
            raise ValueError(
                "No signing key available. Set WALLET_PRIVATE_KEY env var, pass private_key to constructor, "
                "or configure an OWS wallet (OWS_WALLET env var or ows_wallet constructor arg)."
            )

        try:
            import eth_account  # noqa: F401 — early dep check; signing happens in _sign_eip1559_tx_for_broadcast
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

                    # Sign via OWS or raw key (centralized in helper)
                    signed_tx_hex = self._sign_eip1559_tx_for_broadcast(tx_fields)

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

    def register_agent_wallet(self, ows_wallet_name: str) -> dict:
        """Register an OWS wallet for this agent. Elite-only (beta).

        Creates a per-agent wallet record on the server. After registration,
        set on-chain approvals via set_approvals(), then call
        update_agent_wallet_creds() to cache CLOB credentials server-side.

        Args:
            ows_wallet_name: Name of the OWS wallet (e.g. "agent-mybot")

        Returns:
            dict with wallet record (id, agent_id, wallet_address, approvals_set)
        """
        from simmer_sdk.ows_utils import get_ows_wallet_address
        wallet_address = get_ows_wallet_address(ows_wallet_name)

        resp = self._request("POST", "/api/sdk/agent-wallet/register", json={
            "agent_id": self._agent_id,
            "ows_wallet_name": ows_wallet_name,
            "wallet_address": wallet_address,
        })
        return resp

    def get_agent_wallets(self) -> list:
        """List all agent wallets for the authenticated user.

        Returns:
            list of wallet dicts (id, agent_id, wallet_address, approvals_set, agent_name)
        """
        resp = self._request("GET", "/api/sdk/agent-wallets")
        return resp.get("wallets", [])

    def _is_agent_wallet_registered(self) -> bool:
        """Return True if `self._wallet_address` has a row in user_agent_wallets.

        Cached for the lifetime of the client. Used by trade() to decide whether
        to inject `wallet_address` into the payload — only registered wallets
        take the per-agent-wallet route on the server. Unregistered OWS wallets
        fall through to the user-level `linked_wallet_address` path so trading
        works without requiring Elite-tier registration.
        """
        cached = getattr(self, "_agent_wallet_registered", None)
        if cached is not None:
            return cached
        if not self._wallet_address:
            self._agent_wallet_registered = False
            return False
        try:
            wallets = self.get_agent_wallets()
            target = self._wallet_address.lower()
            self._agent_wallet_registered = any(
                (w.get("wallet_address") or "").lower() == target for w in wallets
            )
        except Exception as e:
            # On error, default to False so we take the safer fallback path.
            logger.debug("agent-wallet registration check failed: %s — falling back to user-level path", e)
            self._agent_wallet_registered = False
        return self._agent_wallet_registered

    def get_agent_wallet_pnl(self, agent_id: str = None) -> dict:
        """Get P&L for an agent's dedicated wallet.

        Args:
            agent_id: SDK agent ID. Defaults to this client's agent_id.

        Returns:
            dict with realized_pnl, unrealized_pnl (nullable), total_cost, positions.

            .. deprecated::
                ``unrealized_pnl`` may be ``None`` and will be removed in a
                future version.  Use ``total_pnl`` from ``/v1/trader``
                instead.
        """
        aid = agent_id or self._agent_id
        resp = self._request("GET", f"/api/sdk/agent-wallet/{aid}/pnl")
        # Defensive: coerce None → 0 so callers doing arithmetic don't crash.
        # Deprecated — will be removed in a future version. Use total_pnl from /v1/trader instead.
        if resp.get("unrealized_pnl") is None:
            resp["unrealized_pnl"] = 0.0
        return resp

    def update_agent_wallet_creds(self, ows_wallet_name: str) -> dict:
        """Derive CLOB credentials via OWS and cache them server-side.

        Call this after setting on-chain approvals for an agent wallet.
        Derives Polymarket CLOB API credentials using OWS signing (no private
        key needed) and uploads them encrypted to the server.

        Args:
            ows_wallet_name: Name of the OWS wallet

        Returns:
            dict with updated wallet record
        """
        from simmer_sdk.ows_utils import get_ows_wallet_address, ows_derive_clob_creds
        wallet_address = get_ows_wallet_address(ows_wallet_name)
        creds = ows_derive_clob_creds(ows_wallet_name)

        return self._request("POST", "/api/sdk/agent-wallet/update-creds", json={
            "wallet_address": wallet_address,
            "clob_api_creds": {
                "api_key": creds.api_key,
                "api_secret": creds.api_secret,
                "api_passphrase": creds.api_passphrase,
            },
            "approvals_set": True,
        })

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
