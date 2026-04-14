#!/usr/bin/env python3
"""
Simmer Copytrading Skill

Mirrors positions from target Polymarket wallets via Simmer SDK.
Uses the existing copytrading_strategy.py logic server-side.

By default, runs in "buy only" mode - only buys to match whale positions,
never sells existing positions. This prevents conflicts with other strategies
(weather, etc.) that may have opened positions.

Exit handling:
- Whale exit detection is ON by default (sell when whales exit)
- --no-whale-exits: Disable whale exit detection (buy-only, never sell)
- SDK Risk Management: Stop-loss/take-profit (server-side, auto-set on every buy)

Usage:
    python copytrading_trader.py              # Dry run (show what would trade)
    python copytrading_trader.py --live       # Execute real trades
    python copytrading_trader.py --positions  # Show current positions
    python copytrading_trader.py --config     # Show configuration
    python copytrading_trader.py --wallets 0x... # Override wallets for this run
    python copytrading_trader.py --no-whale-exits # Disable whale exit detection
    python copytrading_trader.py --rebalance  # Full rebalance mode (buy + sell)
"""

import os
import sys
import json
import argparse
from typing import Optional
from datetime import datetime

# Force line-buffered stdout so output is visible in non-TTY environments (cron, Docker, OpenClaw)
sys.stdout.reconfigure(line_buffering=True)

# Optional: Trade Journal integration for tracking
try:
    from tradejournal import log_trade
    JOURNAL_AVAILABLE = True
except ImportError:
    try:
        # Try relative import within skills package
        from skills.tradejournal import log_trade
        JOURNAL_AVAILABLE = True
    except ImportError:
        JOURNAL_AVAILABLE = False
        def log_trade(*args, **kwargs):
            pass  # No-op if tradejournal not installed

# Source tag for tracking
TRADE_SOURCE = "sdk:copytrading"
REACTOR_TRADE_SOURCE = "sdk:copytrading:reactor"
SKILL_SLUG = "polymarket-copytrading"
_automaton_reported = False


# =============================================================================
# Configuration (config.json > env vars > defaults)
# =============================================================================

from simmer_sdk.skill import load_config, update_config, get_config_path

# Configuration schema
CONFIG_SCHEMA = {
    "wallets": {"env": "SIMMER_COPYTRADING_WALLETS", "default": "", "type": str},
    "top_n": {"env": "SIMMER_COPYTRADING_TOP_N", "default": "", "type": str},  # Empty = auto
    "max_usd": {"env": "SIMMER_COPYTRADING_MAX_USD", "default": 50.0, "type": float},
    "max_trades_per_run": {"env": "SIMMER_COPYTRADING_MAX_TRADES", "default": 10, "type": int},
    "venue": {"env": "TRADING_VENUE", "default": "", "type": str},  # sim or polymarket
    "order_type": {"env": "SIMMER_COPYTRADING_ORDER_TYPE", "default": "GTC", "type": str},
}

# Load configuration
_config = load_config(CONFIG_SCHEMA, __file__, slug="polymarket-copytrading")

# SimmerClient singleton
_client = None

def get_client():
    """Lazy-init SimmerClient singleton."""
    global _client
    if _client is None:
        try:
            from simmer_sdk import SimmerClient
        except ImportError:
            print("Error: simmer-sdk not installed. Run: pip install simmer-sdk")
            sys.exit(1)
        api_key = os.environ.get("SIMMER_API_KEY")
        if not api_key:
            print("Error: SIMMER_API_KEY environment variable not set")
            print("Get your API key from: simmer.markets/dashboard -> SDK tab")
            sys.exit(1)
        venue = _config.get("venue") or os.environ.get("TRADING_VENUE") or "polymarket"
        _client = SimmerClient(api_key=api_key, venue=venue)
    return _client

# Polymarket constraints
MIN_SHARES_PER_ORDER = 5.0  # Polymarket requires minimum 5 shares
MIN_TICK_SIZE = 0.01        # Minimum price increment

# Copytrading settings - from config
COPYTRADING_WALLETS = _config["wallets"]
COPYTRADING_TOP_N = _config["top_n"]
COPYTRADING_MAX_USD = _config["max_usd"]
_automaton_max = os.environ.get("AUTOMATON_MAX_BET")
if _automaton_max:
    COPYTRADING_MAX_USD = min(COPYTRADING_MAX_USD, float(_automaton_max))
MAX_TRADES_PER_RUN = _config["max_trades_per_run"]
ORDER_TYPE = (_config.get("order_type") or "GTC").upper()

# Reactor settings — used only by --reactor mode.
# The relay writes signals with TTL=60s (tunable server-side via
# REACTOR_SIGNAL_TTL_SECONDS); harness polling cadence must stay well under
# that to avoid processing stale data. Default 2s gives ~1-3s pre-confirmation
# edge after the relay → Redis → poll → trade round-trip.
#
# Rate-limit math: /api/sdk/reactor/pending falls through to the default SDK
# rate limit (30/min, 90/min for Pro). At 2s polling that's 30 req/min = 33%
# of the Pro cap, leaving ample headroom for the circuit-check + reaction POST
# per signal. If you bump this lower than 2s, verify with the backend first.
REACTOR_POLL_INTERVAL_SECONDS = float(os.environ.get("REACTOR_POLL_INTERVAL_SECONDS", "2"))

# Circuit breaker: if the last N reactor_reactions are all 'failed', pause
# processing — typically indicates the user's wallet is broken (no USDC, no
# allowances, etc.) and every new trade will fail the same way. The circuit
# state lives server-side in reactor_reactions, so it survives harness
# restarts and behaves identically in loop mode and --once mode. When the
# circuit is tripped we neither delete nor log skipped signals; we let the
# relay's 60s TTL clear them naturally so the reactions table stays clean
# during an outage. Users fix their wallet, new signals flow normally, old
# ones expired on their own.
REACTOR_CONSECUTIVE_FAILURE_LIMIT = 5
REACTOR_REACTIONS_CHECK_LIMIT = 10

# Price buffer: fraction added to (buys) or subtracted from (sells) the whale's
# fill price. Compensates for the whale clearing book liquidity at their fill
# level — without a buffer, FAK orders fail with "no liquidity at this price."
# Fetched from reactor config at startup; overridden by env var for testing.
# Range: 0.0 (exact whale price) to 0.2 (20% buffer). Default 0.02 (2%).
_reactor_price_buffer: float = float(os.environ.get("REACTOR_PRICE_BUFFER", "0.02"))


def get_config() -> dict:
    """Get current configuration."""
    wallets = [w.strip() for w in COPYTRADING_WALLETS.split(",") if w.strip()]
    top_n = int(COPYTRADING_TOP_N) if COPYTRADING_TOP_N else None

    return {
        "api_key_set": bool(os.environ.get("SIMMER_API_KEY")),
        "wallets": wallets,
        "top_n": top_n,
        "top_n_mode": "auto" if top_n is None else "manual",
        "max_position_usd": COPYTRADING_MAX_USD,
    }


def print_config():
    """Print current configuration."""
    config = get_config()
    config_path = get_config_path(__file__)

    print("\n🐋 Simmer Copytrading Configuration")
    print("=" * 40)
    print(f"API Key: {'✅ Set' if config['api_key_set'] else '❌ Not set'}")
    print(f"\nTarget Wallets ({len(config['wallets'])}):")
    for i, wallet in enumerate(config['wallets'], 1):
        print(f"  {i}. {wallet[:10]}...{wallet[-6:]}")
    if not config['wallets']:
        print("  (none configured)")

    print(f"\nSettings:")
    print(f"  Top N: {config['top_n'] if config['top_n'] else 'auto (based on balance)'}")
    print(f"  Max per position: ${config['max_position_usd']:.2f}")
    print(f"\nConfig file: {config_path}")
    print(f"Config exists: {'Yes' if config_path.exists() else 'No'}")
    print("\nTo change settings:")
    print("  --set wallets=0x123...,0x456...")
    print("  --set max_usd=100")
    print("  --set top_n=10")
    print()


# =============================================================================
# API Helpers
# =============================================================================

def get_positions() -> dict:
    """Get current SDK positions as raw dict, filtered by venue."""
    client = get_client()
    return client._request("GET", f"/api/sdk/positions?venue={client.venue}")


def set_risk_monitor(market_id: str, side: str,
                     stop_loss_pct: float = 0.20, take_profit_pct: float = 0.50) -> dict:
    """Set stop-loss and take-profit for a position."""
    try:
        return get_client().set_monitor(market_id, side,
                                        stop_loss_pct=stop_loss_pct,
                                        take_profit_pct=take_profit_pct)
    except Exception as e:
        return {"error": str(e)}


def get_risk_monitors() -> dict:
    """List all active risk monitors."""
    try:
        return get_client().list_monitors()
    except Exception as e:
        return {"error": str(e)}


def remove_risk_monitor(market_id: str, side: str) -> dict:
    """Remove risk monitor for a position."""
    try:
        return get_client().delete_monitor(market_id, side)
    except Exception as e:
        return {"error": str(e)}


def get_markets() -> list:
    """Get available markets."""
    result = get_client()._request("GET", "/api/sdk/markets")
    return result.get("markets", [])


def get_context(market_id: str) -> dict:
    """Get market context (position, trades, slippage)."""
    return get_client().get_market_context(market_id)


def execute_trade(market_id: str, side: str, action: str, amount_usd: float = None, shares: float = None) -> dict:
    """Execute a trade via SDK."""
    try:
        result = get_client().trade(
            market_id=market_id, side=side, action=action,
            amount=amount_usd or 0, shares=shares or 0,
            source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
        )
        return {
            "success": result.success, "trade_id": result.trade_id,
            "shares_bought": result.shares_bought, "error": result.error,
        }
    except Exception as e:
        raise ValueError(str(e))


# =============================================================================
# Copytrading Logic
# =============================================================================



def execute_copytrading(wallets: list, top_n: int = None, max_usd: float = 50.0, dry_run: bool = True, buy_only: bool = True, detect_whale_exits: bool = True, max_trades: int = None, venue: str = None) -> dict:
    """
    Execute copytrading via Simmer SDK.

    Uses dry_run=True to get the trade plan from the server, then executes
    each trade client-side via client.trade(). This ensures signing works
    for both managed (server-side) and external (client-side) wallets.

    The server handles: fetching whale positions, calculating allocations,
    conflict detection, Top N filtering, auto-import, rebalance calculation.
    The client handles: trade execution with proper wallet signing.

    Venue:
    - 'sim': Execute on Simmer LMSR with $SIM (paper trading)
    - 'polymarket': Execute on Polymarket with real USDC
    - None: Fall back to TRADING_VENUE env var, then server auto-detect
    """
    # Default to TRADING_VENUE env var so automaton/cron venue choice propagates
    if venue is None:
        venue = os.environ.get("TRADING_VENUE")

    data = {
        "wallets": wallets,
        "max_usd_per_position": max_usd,
        "dry_run": True,  # Always get trade plan from server
        "buy_only": buy_only,
        "detect_whale_exits": detect_whale_exits,
    }

    if top_n is not None:
        data["top_n"] = top_n

    if max_trades is not None:
        data["max_trades"] = max_trades

    if venue is not None:
        data["venue"] = venue

    result = get_client()._request("POST", "/api/sdk/copytrading/execute", json=data, timeout=60)

    # If caller wants dry_run, return the plan as-is
    if dry_run:
        return result

    # Execute each trade client-side via client.trade() (handles signing for all wallet types)
    trades = result.get("trades", [])
    executed = 0
    for t in trades:
        market_id = t.get("market_id")
        action = t.get("action", "buy")
        side = t.get("side", "yes")
        shares = t.get("shares", 0)
        estimated_cost = t.get("estimated_cost", 0)

        try:
            market_title = t.get("market_title", market_id[:20])
            _whale_wallet = t.get("whale_wallet", t.get("source_wallet", ""))
            _signal_data = {
                "edge": round(t.get("edge", 0.05), 4),
                "confidence": round(t.get("confidence", 0.6), 2),
                "signal_source": "whale_copytrading",
                "whale_wallet": _whale_wallet[:10] if _whale_wallet else "",
                "whale_size": round(t.get("whale_position_usd", t.get("estimated_cost", 0)), 2),
            }
            trade_result = get_client().trade(
                market_id=market_id,
                side=side,
                action=action,
                amount=estimated_cost if action == "buy" else 0,
                shares=shares if action == "sell" else 0,
                order_type=ORDER_TYPE,
                reasoning=f"Copytrading: {action} {shares:.1f} {side} to mirror whale positions on {market_title}",
                source=TRADE_SOURCE,
                skill_slug=SKILL_SLUG,
                signal_data=_signal_data,
            )
            t["success"] = trade_result.success
            t["error"] = trade_result.error if not trade_result.success else None
            t["trade_id"] = trade_result.trade_id
            if trade_result.success:
                executed += 1
        except Exception as e:
            t["success"] = False
            t["error"] = str(e)

    result["trades_executed"] = executed
    result["dry_run"] = False
    return result


def run_copytrading(wallets: list, top_n: int = None, max_usd: float = 50.0, dry_run: bool = True, buy_only: bool = True, detect_whale_exits: bool = True, venue: str = None):
    """
    Run copytrading scan and execute trades.

    Calls the Simmer SDK copytrading endpoint which handles:
    - Fetching positions from target wallets via Dome API
    - Size-weighted aggregation (larger wallets = more influence)
    - Conflict detection (skips markets where wallets disagree)
    - Top N concentration (focus on highest-conviction positions)
    - Auto-import of missing markets
    - Rebalance trade calculation and execution
    - Whale exit detection (sells positions whales no longer hold)

    By default, only BUY trades are executed (buy_only=True). This prevents
    copytrading from selling positions opened by other strategies (weather, etc.)

    Venue: 'sim' for $SIM paper trading, 'polymarket' for real USDC, None for auto-detect.
    """
    print("\n🐋 Starting Copytrading Scan...")
    print("=" * 50)

    if not wallets:
        print("❌ No wallets specified.")
        print("   Use --wallets 0x123...,0x456... to specify wallets")
        print("   Or set SIMMER_COPYTRADING_WALLETS env var for recurring scans")
        return

    # Show configuration
    print("\n⚙️ Configuration:")
    print(f"  Wallets: {len(wallets)}")
    for w in wallets:
        print(f"    • {w[:10]}...{w[-6:]}")
    print(f"  Top N: {top_n if top_n else 'auto (based on balance)'}")
    print(f"  Max per position: ${max_usd:.2f}")
    print(f"  Max trades/run:  {MAX_TRADES_PER_RUN}")
    venue_label = venue or "auto-detect"
    print(f"  Venue: {venue_label}")
    print(f"  Mode: {'Buy only (accumulate)' if buy_only else 'Full rebalance (buy + sell)'}")
    print(f"  Whale exits: {'Enabled (sell when whale exits)' if detect_whale_exits else 'Disabled'}")

    if dry_run:
        print("\n  [DRY RUN] Trades will be simulated server-side. Use --live for real trades.")

    # Redeem any winning positions before starting the cycle
    try:
        redeemed = get_client().auto_redeem()
        for r in redeemed:
            if r.get("success"):
                print(f"  💰 Redeemed {r['market_id'][:8]}... ({r.get('side', '?')})")
    except Exception:
        pass  # Non-critical — don't block trading

    # Execute copytrading via SDK
    print("\n📡 Calling Simmer API...")
    try:
        result = execute_copytrading(wallets, top_n, max_usd, dry_run, buy_only, detect_whale_exits, MAX_TRADES_PER_RUN, venue=venue)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return

    # Display results
    print(f"\n📊 Analysis Results:")
    print(f"  Wallets analyzed: {result.get('wallets_analyzed', 0)}")
    print(f"  Positions found: {result.get('positions_found', 0)}")
    print(f"  Conflicts skipped: {result.get('conflicts_skipped', 0)}")
    print(f"  Top N used: {result.get('top_n_used', 0)}")
    whale_exits = result.get('whale_exits_detected', 0)
    if whale_exits > 0:
        print(f"  Whale exits detected: {whale_exits}")

    trades = result.get('trades', [])
    trades_needed = result.get('trades_needed', 0)
    trades_executed = result.get('trades_executed', 0)

    if trades:
        print(f"\n📈 Trades ({trades_executed}/{trades_needed} executed):")
        for t in trades:
            action = t.get('action', '?').upper()
            side = t.get('side', '?').upper()
            shares = t.get('shares', 0)
            price = t.get('estimated_price', 0)
            cost = t.get('estimated_cost', 0)
            title = t.get('market_title', 'Unknown')[:40]
            success = t.get('success', False)
            error = t.get('error')

            status = "✅" if success else "⏸️"
            if error and "dry_run" in error:
                status = "🔒"

            print(f"  {status} {action} {shares:.1f} {side} @ ${price:.3f} (${cost:.2f})")
            print(f"     {title}...")
            if error and "dry_run" not in error:
                print(f"     ⚠️ {error}")

    # Show errors
    errors = result.get('errors', [])
    if errors:
        print(f"\n⚠️ Warnings:")
        for err in errors:
            print(f"  • {err}")

    # Summary
    summary = result.get('summary', 'Complete')
    print(f"\n{'─' * 50}")
    print(f"📋 {summary}")

    if not result.get('success'):
        print("\n❌ Copytrading failed. Check errors above.")
    elif dry_run:
        print("\n💡 Remove --dry-run to execute trades")
    elif trades_executed > 0:
        print(f"\n✅ Successfully mirrored positions!")

        # Log successful trades to journal
        # Risk monitors are now auto-set via SDK settings (dashboard)
        for t in trades:
            if t.get('success'):
                trade_id = t.get('trade_id')
                action = t.get('action', 'buy')
                side = t.get('side', 'yes')
                shares = t.get('shares', 0)
                price = t.get('estimated_price', 0)

                # Log trade context for journal
                if trade_id and JOURNAL_AVAILABLE:
                    log_trade(
                        trade_id=trade_id,
                        source=TRADE_SOURCE, skill_slug=SKILL_SLUG,
                        thesis=f"Copytrading: {action.upper()} {shares:.1f} {side.upper()} "
                               f"@ ${price:.3f} to mirror whale positions",
                        action=action,
                        wallets_count=len(wallets),
                    )
    else:
        print("\n✅ Scan complete")

    # Structured report for automaton
    if os.environ.get("AUTOMATON_MANAGED"):
        global _automaton_reported
        positions_found = result.get('positions_found', 0) if result else 0
        _trades_needed = result.get('trades_needed', 0) if result else 0
        _trades_exec = result.get('trades_executed', 0) if result else 0
        _total_cost = sum(t.get('estimated_cost', 0) for t in (result.get('trades', []) if result else []) if t.get('success'))
        report = {"signals": positions_found, "trades_attempted": _trades_needed, "trades_executed": _trades_exec, "amount_usd": round(_total_cost, 2)}
        if positions_found > 0 and _trades_exec == 0:
            # Derive skip reasons from server response
            skip_reasons = []
            conflicts = result.get('conflicts_skipped', 0) if result else 0
            if conflicts > 0:
                skip_reasons.append(f"{conflicts} conflicts skipped")
            errors = result.get('errors', []) if result else []
            for err in errors:
                skip_reasons.append(str(err)[:80])
            if not result.get('success'):
                skip_reasons.append("copytrading failed")
            if skip_reasons:
                report["skip_reason"] = ", ".join(dict.fromkeys(skip_reasons))
        # Collect execution errors from failed trades
        execution_errors = []
        for t in (result.get('trades', []) if result else []):
            if not t.get('success') and t.get('error') and 'dry_run' not in str(t.get('error', '')):
                execution_errors.append(str(t['error'])[:120])
        if execution_errors:
            report["execution_errors"] = execution_errors
        print(json.dumps({"automaton": report}))
        _automaton_reported = True


# =============================================================================
# Reactor mode — consumes pre-resolved signals from /api/sdk/reactor/pending
#
# Architecture: the Simmer scheduler runs a PolyNode firehose (see
# simmer_v3/reactor_relay.py) that matches whale trades against each Pro
# user's reactor_configs row, pre-resolves the market, computes the mirror
# size, and writes the result to Redis with a 60s TTL. This skill polls
# that endpoint and executes the signal via client.trade(), which handles
# managed, external, and (future) OWS wallet backends transparently.
#
# Two invocation shapes:
#   python copytrading_trader.py --reactor          # loop forever, 2s cadence
#   python copytrading_trader.py --reactor --once   # single poll, exit (cron)
#
# Loop mode is recommended: supervised process (launchctl, systemd, tmux,
# Procfile, OpenClaw) gives the full ~1-3s pre-confirmation edge. --once
# mode supports cron-style runtimes at a degraded edge (whatever the cron
# cadence is — 60s on stock Unix cron means you miss most signals because
# of the 60s TTL, so prefer loop mode where possible).
# =============================================================================


def _is_reactor_circuit_tripped(client) -> bool:
    """
    Server-side circuit check: query the last N reactions; if the most
    recent REACTOR_CONSECUTIVE_FAILURE_LIMIT are all 'failed', circuit is
    tripped. Fails open on API errors so a transient blip doesn't block
    execution. Caller MUST only invoke this when there are pending signals
    to process — empty polls should skip this check entirely.
    """
    try:
        resp = client._request(
            "GET",
            "/api/sdk/reactor/reactions",
            params={"limit": REACTOR_REACTIONS_CHECK_LIMIT},
        )
    except Exception as e:
        print(f"[reactor] circuit check failed ({type(e).__name__}: {e}), proceeding anyway")
        return False
    reactions = resp.get("reactions", []) if isinstance(resp, dict) else []
    streak = 0
    for r in reactions:  # ORDER BY id DESC — newest first
        if r.get("decision") == "failed":
            streak += 1
        else:
            break
    return streak >= REACTOR_CONSECUTIVE_FAILURE_LIMIT


# Reaction POST is non-fatal by design: if the reactions endpoint is down
# while trades are failing, the circuit breaker (which queries reactions)
# won't see the failures and the skill will keep retrying. Accepted risk —
# double-failure of (reactions POST + trade) is rare, and carrying a
# client-side backup counter would violate the stateless-client principle
# the reactor architecture is built on. See
# simmer/_dev/active/_polymarket-reactor/NEXT.md § "architecture pivot".
def _post_reactor_reaction(client, signal: dict, decision: str,
                            trade_id: Optional[str] = None,
                            reason: Optional[str] = None) -> None:
    """
    POST a reaction row so the dashboard + circuit breaker have a record.
    Non-fatal: logs but does not raise. Without this POST, the circuit
    breaker has no data to count from and never trips.
    """
    whale = signal.get("whale") or {}
    body = {
        "event_tx_hash": signal.get("tx_hash") or "",
        "taker_wallet": (whale.get("wallet") or ""),
        "taker_side": whale.get("side") or "",
        "taker_token": whale.get("token") or "",
        "taker_price": float(whale.get("price") or 0),
        "taker_size": float(whale.get("size") or 0),
        "condition_id": whale.get("condition_id"),
        "market_title": whale.get("market_title"),
        "market_slug": whale.get("market_slug"),
        "outcome": whale.get("outcome"),
        "decision": decision,
        "trade_id": trade_id,
        "reason": reason,
    }
    try:
        client._request("POST", "/api/sdk/reactor/reactions", json=body)
    except Exception as e:
        print(f"[reactor] reaction POST warn ({decision}): {type(e).__name__}: {e}")


def _process_reactor_signal(client, signal: dict) -> bool:
    """
    Execute one pre-resolved reactor signal. Returns True on successful
    trade (signal gets deleted), False on failure (signal stays in Redis
    to TTL out). Always POSTs a reactions row regardless of outcome so
    the circuit breaker has data.
    """
    tx_hash = signal.get("tx_hash") or ""
    market_id = signal.get("market_id")
    side = signal.get("side")
    action = signal.get("action", "buy")
    amount = float(signal.get("amount") or 0)
    venue = signal.get("venue")
    whale = signal.get("whale") or {}

    tx_short = tx_hash[:12] if tx_hash else "<no-tx>"

    if not (tx_hash and market_id and side and amount > 0):
        reason = "malformed_signal"
        print(f"[reactor] ❌ {tx_short}... {reason}")
        _post_reactor_reaction(client, signal, decision="failed", reason=reason)
        return False

    reasoning = (
        f"reactor mirror: whale {str(whale.get('wallet') or '')[:10]}... "
        f"{whale.get('side') or '?'} {float(whale.get('size') or 0):.0f} shares @ "
        f"{float(whale.get('price') or 0):.3f} on "
        f"'{whale.get('market_title') or whale.get('market_slug') or 'unknown'}'"
    )
    signal_data = {
        "signal_source": "reactor_copytrading",
        "tx_hash": tx_hash,
        "whale_wallet": str(whale.get("wallet") or "")[:10],
        "whale_side": whale.get("side"),
        "whale_size": round(float(whale.get("size") or 0), 2),
        "whale_price": round(float(whale.get("price") or 0), 4),
    }

    # Compute a buffered price from the whale's fill price. Without this,
    # FAK orders fail on thin books because the whale already cleared liquidity
    # at their fill level. The buffer (default 2%) bids slightly above for buys,
    # slightly below for sells — still FAK (no hanging orders), but tolerates
    # post-whale spread.
    whale_price = float(whale.get("price") or 0)
    trade_price = None
    if whale_price > 0 and venue == "polymarket":
        buf = _reactor_price_buffer
        if action == "buy":
            trade_price = min(round(whale_price * (1 + buf), 4), 0.999)
        else:
            trade_price = max(round(whale_price * (1 - buf), 4), 0.001)

    try:
        trade_kwargs = dict(
            market_id=market_id,
            side=side,
            action=action,
            amount=amount,
            venue=venue,
            order_type=ORDER_TYPE,
            allow_rebuy=True,  # reactor signals are discrete events; allow re-entry
            source=REACTOR_TRADE_SOURCE,
            skill_slug=SKILL_SLUG,
            reasoning=reasoning,
            signal_data=signal_data,
        )
        if trade_price is not None:
            trade_kwargs["price"] = trade_price
        result = client.trade(**trade_kwargs)
    except Exception as e:
        reason = f"trade_error: {type(e).__name__}: {e}"
        print(f"[reactor] ❌ {tx_short}... {reason}")
        _post_reactor_reaction(client, signal, decision="failed", reason=reason)
        return False

    if not getattr(result, "success", False):
        err = getattr(result, "error", None) or getattr(result, "skip_reason", None) or "unknown"
        reason = f"trade_rejected: {err}"
        print(f"[reactor] ❌ {tx_short}... {reason}")
        _post_reactor_reaction(client, signal, decision="failed", reason=reason)
        return False

    trade_id = getattr(result, "trade_id", None)
    shares = getattr(result, "shares_bought", None)
    print(f"[reactor] ✅ {tx_short}... mirrored {amount:.2f} USD"
          f"{f' ({shares:.1f} shares)' if shares else ''} trade_id={trade_id}")

    # Record the success before the DELETE so the circuit-breaker counter
    # reflects reality even if the delete request blips.
    _post_reactor_reaction(
        client, signal, decision="mirrored",
        trade_id=str(trade_id) if trade_id else None,
        reason=None,
    )

    # Delete the signal to prevent reprocessing on the next poll. Non-fatal:
    # if the delete fails, the 60s TTL will clean it up; worst case we'd
    # re-process the same tx_hash once, which client.trade() handles via
    # idempotency on the server-side trade path.
    try:
        client._request("DELETE", f"/api/sdk/reactor/pending/{tx_hash}")
    except Exception as e:
        print(f"[reactor] delete warn for {tx_short}...: {type(e).__name__}: {e}")

    return True


class _ConnectionError(Exception):
    """Raised by _poll_reactor_once when the poll fails due to a connection
    issue (SSL, reset, timeout). The loop uses this to decide when to
    recycle the HTTP session."""
    pass


def _poll_reactor_once(client) -> int:
    """
    Single poll iteration: fetch pending signals, check circuit if any
    exist, process each. Returns the number of signals processed (success
    or failure). Used directly by --once mode and once per tick by the
    loop mode. Raises _ConnectionError on transport-level failures so the
    loop can recycle the session.
    """
    try:
        resp = client._request("GET", "/api/sdk/reactor/pending")
    except (ConnectionError, OSError) as e:
        print(f"[reactor] poll failed: {type(e).__name__}: {e}")
        raise _ConnectionError(str(e)) from e
    except Exception as e:
        if "SSL" in type(e).__name__ or "SSL" in str(e):
            print(f"[reactor] poll failed: {type(e).__name__}: {e}")
            raise _ConnectionError(str(e)) from e
        print(f"[reactor] poll failed: {type(e).__name__}: {e}")
        return 0

    signals = resp.get("reactor_signals", []) if isinstance(resp, dict) else []
    if not signals:
        print("[reactor] 0 pending signals")
        return 0

    print(f"[reactor] {len(signals)} pending signal(s)")

    if _is_reactor_circuit_tripped(client):
        print(f"[reactor] ⚠️  circuit tripped "
              f"({REACTOR_CONSECUTIVE_FAILURE_LIMIT}+ consecutive failures), "
              f"skipping {len(signals)} signal(s) — TTL will clear")
        return 0

    processed = 0
    for signal in signals:
        try:
            if _process_reactor_signal(client, signal):
                processed += 1
            else:
                processed += 1  # counted either way — "seen + acted"
        except Exception as e:
            tx_short = (signal.get("tx_hash") or "")[:12] or "<no-tx>"
            print(f"[reactor] unexpected error on {tx_short}...: {type(e).__name__}: {e}")
    return processed


def run_reactor(once: bool = False) -> None:
    """
    Reactor entry point. `once=True` polls once and exits (cron-friendly);
    `once=False` runs a forever loop polling every REACTOR_POLL_INTERVAL_SECONDS.
    """
    global _reactor_price_buffer
    client = get_client()

    # Fetch reactor config to pick up user's price_buffer setting.
    # Falls back to env var / default if the API call fails.
    try:
        cfg = client._request("GET", "/api/sdk/reactor/config")
        if isinstance(cfg, dict) and "price_buffer" in cfg:
            _reactor_price_buffer = float(cfg["price_buffer"])
            print(f"[reactor] price_buffer={_reactor_price_buffer:.3f} (from config)")
    except Exception:
        print(f"[reactor] price_buffer={_reactor_price_buffer:.3f} (default — config fetch failed)")

    if once:
        print(f"[reactor] --once: single poll against /api/sdk/reactor/pending")
        _poll_reactor_once(client)
        return

    import time as _time
    interval = REACTOR_POLL_INTERVAL_SECONDS
    print(f"[reactor] loop mode: polling /api/sdk/reactor/pending every {interval}s")
    print(f"[reactor] set REACTOR_POLL_INTERVAL_SECONDS to tune; circuit trips after "
          f"{REACTOR_CONSECUTIVE_FAILURE_LIMIT} consecutive failures")
    consecutive_conn_errors = 0
    SESSION_RECYCLE_THRESHOLD = 3

    while True:
        try:
            _poll_reactor_once(client)
            consecutive_conn_errors = 0  # reset on any successful poll
        except KeyboardInterrupt:
            print("[reactor] keyboard interrupt — exiting loop")
            return
        except _ConnectionError:
            consecutive_conn_errors += 1
            if consecutive_conn_errors >= SESSION_RECYCLE_THRESHOLD:
                print(f"[reactor] {consecutive_conn_errors} consecutive connection errors "
                      f"— recycling HTTP session")
                import requests as _req
                client._session.close()
                client._session = _req.Session()
                client._session.headers.update({
                    "Authorization": f"Bearer {client.api_key}",
                    "Content-Type": "application/json",
                })
                consecutive_conn_errors = 0
        except Exception as e:
            print(f"[reactor] tick error ({type(e).__name__}): {e} — continuing")
            consecutive_conn_errors = 0
        try:
            _time.sleep(interval)
        except KeyboardInterrupt:
            print("[reactor] keyboard interrupt during sleep — exiting loop")
            return


def show_positions():
    """Show current SDK positions."""
    print("\n📊 Your Polymarket Positions")
    print("=" * 50)

    try:
        data = get_positions()
        positions = data.get("positions", [])

        # Filter to active venue positions
        active_venue = os.environ.get("TRADING_VENUE", "polymarket")
        if active_venue == "simmer":
            active_venue = "sim"
        venue_positions = [p for p in positions if p.get("venue") == active_venue]

        if not venue_positions:
            print(f"No {active_venue} positions found.")
            print("\nTo start copytrading:")
            print("1. Configure target wallets in SIMMER_COPYTRADING_WALLETS")
            print("2. Run: python copytrading_trader.py")
            return

        total_value = 0
        total_pnl = 0

        for i, pos in enumerate(venue_positions, 1):
            question = pos.get("question", "Unknown market")[:50]
            shares_yes = pos.get("shares_yes", 0)
            shares_no = pos.get("shares_no", 0)
            value = pos.get("current_value", 0)
            pnl = pos.get("pnl", 0)
            pnl_pct = (pnl / pos.get("cost_basis", 1)) * 100 if pos.get("cost_basis") else 0

            total_value += value
            total_pnl += pnl

            # Determine side
            if shares_yes > shares_no:
                side = f"{shares_yes:.1f} YES"
            else:
                side = f"{shares_no:.1f} NO"

            pnl_color = "+" if pnl >= 0 else ""
            print(f"\n{i}. {question}...")
            print(f"   Position: {side}")
            print(f"   Value: ${value:.2f} | P&L: {pnl_color}${pnl:.2f} ({pnl_color}{pnl_pct:.1f}%)")

        print(f"\n{'─' * 50}")
        pnl_color = "+" if total_pnl >= 0 else ""
        print(f"Total Value: ${total_value:.2f}")
        print(f"Total P&L: {pnl_color}${total_pnl:.2f}")
        print(f"Positions: {len(venue_positions)}")

    except Exception as e:
        print(f"❌ Error fetching positions: {e}")


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Simmer Copytrading - Mirror positions from Polymarket whales"
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Execute real trades (default is dry-run)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="(Default) Show what would trade without executing"
    )
    parser.add_argument(
        "--positions",
        action="store_true",
        help="Show current positions only"
    )
    parser.add_argument(
        "--config",
        action="store_true",
        help="Show current configuration"
    )
    parser.add_argument(
        "--wallets",
        type=str,
        help="Comma-separated wallet addresses (overrides env var)"
    )
    parser.add_argument(
        "--top-n",
        type=int,
        help="Number of top positions to mirror (overrides env var)"
    )
    parser.add_argument(
        "--max-usd",
        type=float,
        help="Max USD per position (overrides env var)"
    )
    parser.add_argument(
        "--rebalance",
        action="store_true",
        help="Full rebalance mode: buy AND sell to match targets (default: buy-only)"
    )
    parser.add_argument(
        "--no-whale-exits",
        action="store_true",
        help="Disable whale exit detection (default: whale exits are detected and sold)"
    )
    parser.add_argument(
        "--venue",
        type=str,
        choices=["sim", "polymarket"],
        help="Trading venue: 'sim' for $SIM paper trading, 'polymarket' for real USDC (default: auto-detect)"
    )
    parser.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help="Set config value (e.g., --set wallets=0x123,0x456 --set max_usd=100)"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Only output on trades/errors"
    )
    parser.add_argument(
        "--reactor",
        action="store_true",
        help="Reactor mode: poll /api/sdk/reactor/pending and mirror pre-resolved whale signals (Pro only)"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="With --reactor: single poll and exit (for cron-style invocation). Ignored otherwise."
    )
    args = parser.parse_args()

    # Handle --set config updates
    if args.set:
        updates = {}
        for item in args.set:
            if "=" in item:
                key, value = item.split("=", 1)
                if key in CONFIG_SCHEMA:
                    type_fn = CONFIG_SCHEMA[key].get("type", str)
                    try:
                        value = type_fn(value)
                    except (ValueError, TypeError):
                        pass
                updates[key] = value
        if updates:
            updated = update_config(updates, __file__)
            print(f"✅ Config updated: {updates}")
            print(f"   Saved to: {get_config_path(__file__)}")
            # Reload config into module-level _config
            reloaded = load_config(CONFIG_SCHEMA, __file__, slug="polymarket-copytrading")
            globals()["_config"] = reloaded
            globals()["COPYTRADING_WALLETS"] = reloaded["wallets"]
            globals()["COPYTRADING_TOP_N"] = reloaded["top_n"]
            reloaded_max_usd = reloaded["max_usd"]
            _automaton_max = os.environ.get("AUTOMATON_MAX_BET")
            if _automaton_max:
                reloaded_max_usd = min(reloaded_max_usd, float(_automaton_max))
            globals()["COPYTRADING_MAX_USD"] = reloaded_max_usd
            globals()["MAX_TRADES_PER_RUN"] = reloaded["max_trades_per_run"]

    # Show config
    if args.config:
        print_config()
        return

    # Show positions
    if args.positions:
        show_positions()
        return

    # Default to dry-run unless --live is explicitly passed
    dry_run = not args.live

    # Reactor mode short-circuit: polls Redis signals from the scheduler-side
    # PolyNode firehose and mirrors them via client.trade(). Always live —
    # --dry-run is ignored in reactor mode because the signals themselves are
    # discrete, deduped by tx_hash, and TTL-bounded at 60s server-side. If
    # you want dry behavior, use the polling default mode instead.
    if args.reactor:
        if dry_run and not args.live:
            print("[reactor] note: reactor mode executes live trades — "
                  "the default dry-run flag does not apply here")
        run_reactor(once=args.once)
        return

    # Validate API key by initializing client
    get_client()

    # Get wallets (from args or env)
    if args.wallets:
        wallets = [w.strip() for w in args.wallets.split(",") if w.strip()]
    else:
        wallets = [w.strip() for w in COPYTRADING_WALLETS.split(",") if w.strip()]

    # Get top_n (from args or env)
    top_n = args.top_n
    if top_n is None and COPYTRADING_TOP_N:
        top_n = int(COPYTRADING_TOP_N)

    # Get max_usd (from args or env)
    max_usd = args.max_usd if args.max_usd else COPYTRADING_MAX_USD

    # Determine venue: CLI flag > config.json > TRADING_VENUE env var > None (server auto-detect)
    venue = args.venue or _config.get("venue") or None

    # Run copytrading
    run_copytrading(
        wallets=wallets,
        top_n=top_n,
        max_usd=max_usd,
        dry_run=dry_run,
        buy_only=not args.rebalance,  # Default buy_only=True, --rebalance sets it to False
        detect_whale_exits=not args.no_whale_exits,  # Default ON, --no-whale-exits disables
        venue=venue
    )

    # Fallback report for automaton if the strategy returned early (no signal)
    if os.environ.get("AUTOMATON_MANAGED") and not _automaton_reported:
        print(json.dumps({"automaton": {"signals": 0, "trades_attempted": 0, "trades_executed": 0, "skip_reason": "no_signal"}}))


if __name__ == "__main__":
    main()
