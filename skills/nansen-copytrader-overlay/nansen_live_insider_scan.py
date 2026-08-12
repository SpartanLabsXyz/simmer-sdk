"""
Module 3: nansen-live-insider-scan
====================================
Adapted from the resolved-market insider scan (nansen-polymarket-insider-scan
skill) for LIVE (unresolved) markets.

Key difference from the original:
    - Original scans resolved markets to find who WON suspiciously.
    - This module scans LIVE markets to find wallets making aggressive,
      potentially informed entries RIGHT NOW — before resolution.

Signal output: a structured object per suspicious wallet with
    market_id, side, confidence, edge, reason_tags

EXPERIMENTAL — dry-run only:
    This module is not launchable for live trading yet. `scan_live_markets()`
    hard-raises `LiveTradingNotSupportedError` if called with dry_run=False,
    because two things are unconfirmed against the live Nansen API:
      1. Owner/proxy wallet handling: wallets discovered here come from
         `trades_by_market` (a PM/proxy-indexed endpoint), so they are
         proxy addresses — correct for the `trades_by_address` follow-up
         call. But `historical_balances` is a PROFILER endpoint indexed by
         OWNER (signing) wallet, and this module has no owner mapping for
         scanned wallets — `wallet_age_days` may be silently wrong.
      2. `HIGH_NO_ENTRY` price semantics (see FLAG_HIGH_ENTRY_PRICE below).
    Removing the guard is a deliberate code change, not a flag flip — do it
    only after both are verified against the live API.

--live gate:
    This module NEVER places trades itself.  It emits signal objects.
    The Simmer execution wrapper (caller) checks dry_run before acting.

Usage:
    from nansen_live_insider_scan import scan_live_markets

    signals = scan_live_markets(
        market_ids=["12345", "67890"],  # or None to auto-discover top markets
        dry_run=True,                    # required — dry_run=False raises
        min_suspicion_score=3,
    )
    # signals is list[InsiderSignal]; each signal.dry_run == True.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from nansen_adapter import (
    market_screener,
    trades_by_market,
    top_holders,
    trades_by_address,
    historical_balances,
    wallet_age_days,
    NansenError,
    CreditGuard,
    CreditGuardExceeded,
    INTER_CALL_DELAY_S,
    _safe_float,
)

logger = logging.getLogger(__name__)


class LiveTradingNotSupportedError(NansenError):
    """
    Raised by scan_live_markets() when called with dry_run=False.

    This module is experimental — owner/proxy wallet handling and the
    HIGH_NO_ENTRY price-semantics assumption are unconfirmed against the
    live Nansen API (see module docstring). Do not remove this guard
    without verifying both.
    """
    pass


# ---------------------------------------------------------------------------
# Known-entity check
# ---------------------------------------------------------------------------
# Nansen's profiler `labels` endpoint costs 100-500 credits per lookup, and it
# was previously called for every scanned wallet just to apply a -2 score
# discount. A default scan (10 markets x 15 wallets) would burn 15K-75K
# credits — more than the account's entire grant. Known entities are checked
# against this local, free allowlist instead. Extend it with addresses from
# Nansen's public entity labels (or Polymarket's own docs) as they're found;
# it ships small on purpose.
KNOWN_ENTITY_ADDRESSES: set[str] = {
    # "0x...": populate with known CEX / market-maker / fund addresses
}


def _is_known_entity(address: str) -> bool:
    return address.lower() in KNOWN_ENTITY_ADDRESSES


# ---------------------------------------------------------------------------
# Signal dataclass
# ---------------------------------------------------------------------------

@dataclass
class InsiderSignal:
    """
    A live insider-scan signal for a single wallet on a single market.

    Deterministic: given the same Nansen data, same signal is always emitted.
    """
    market_id: str
    market_question: str
    wallet_address: str
    side: str                    # "yes" or "no"
    confidence: float            # 0.0–1.0
    edge: float                  # estimated informational edge (0.0–1.0)
    suspicion_score: int         # sum of flag points (see FLAG_* constants)
    reason_tags: list[str] = field(default_factory=list)

    # Context fields
    entry_price: float = 0.0     # most recent trade price on this market
    position_usd: float = 0.0   # estimated position size in USD
    wallet_age_days: Optional[float] = None
    distinct_markets_traded: int = 0
    has_known_label: bool = False

    dry_run: bool = True         # echoes caller's dry_run flag

    def as_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "market_question": self.market_question,
            "wallet_address": self.wallet_address,
            "side": self.side,
            "confidence": self.confidence,
            "edge": self.edge,
            "suspicion_score": self.suspicion_score,
            "reason_tags": self.reason_tags,
            "entry_price": self.entry_price,
            "position_usd": self.position_usd,
            "wallet_age_days": self.wallet_age_days,
            "distinct_markets_traded": self.distinct_markets_traded,
            "has_known_label": self.has_known_label,
            "dry_run": self.dry_run,
        }


# ---------------------------------------------------------------------------
# Suspicion flags — same scoring rubric as the resolved-market skill,
# adapted for live (unresolved) markets
# ---------------------------------------------------------------------------

FLAG_NEW_WALLET = 3          # wallet funded within 7 days
FLAG_YOUNG_WALLET = 1        # wallet funded 8–28 days ago
FLAG_SINGLE_MARKET = 3       # only ever traded this one market
FLAG_FEW_MARKETS = 1         # traded 2–3 distinct markets
# UNVERIFIED ASSUMPTION: HIGH_NO_ENTRY assumes `price` is quoted in YES terms
# for both sides (a NO trade at 0.25 means the market implies 75% YES odds).
# If Nansen instead returns the NO token's own market price, buying NO at
# 0.25 is a cheap longshot, not a conviction entry, and the flag's polarity
# is inverted for NO trades. Confirm against the live API before trusting
# HIGH_NO_ENTRY (and the mirrored `edge` calculation below for NO trades).
FLAG_HIGH_ENTRY_PRICE = 2    # buying YES at >= 0.75 (high conviction entry)
FLAG_LARGE_POSITION = 2      # position >= $5k USD
FLAG_RAPID_ACCUMULATION = 2  # 3+ trades in this market in last 24h
FLAG_KNOWN_ENTITY = -2       # Nansen-labeled wallet (CEX, fund, etc.)

SUSPICION_THRESHOLD = 3      # flag wallets at this score or above
HIGH_RISK_THRESHOLD = 7      # "high confidence suspicious" at this score

# Hard credit-guard ceilings — callers can pass a lower max_wallets_per_market
# / top_markets, but never a higher one than this regardless of what's asked.
HARD_MAX_WALLETS_PER_MARKET = 30
HARD_MAX_MARKETS_PER_SCAN = 25


def _score_wallet(
    address: str,
    market_id: str,
    market_trades: list[dict],   # all recent trades for this market
    dry_run: bool = True,
    guard: Optional[CreditGuard] = None,
) -> Optional[InsiderSignal]:
    """
    Score a single wallet for suspicious behaviour on a live market.

    Returns an InsiderSignal if suspicious (score >= SUSPICION_THRESHOLD),
    or None if not suspicious / on error.

    NOTE: `address` here is a proxy wallet (sourced from PM trade data), and
    is passed as-is to historical_balances(), a profiler/owner-indexed
    endpoint — see the module docstring's owner/proxy caveat. wallet_age_days
    may be wrong until that's resolved; this is part of why scan_live_markets
    hard-refuses dry_run=False.
    """
    # Filter trades for this wallet on this market
    wallet_trades = [
        t for t in market_trades
        if t.get("buyer") == address or t.get("seller") == address
    ]
    if not wallet_trades:
        return None

    # Determine dominant side (yes/no) and last entry price
    yes_vol = sum(t["usdc_value"] for t in wallet_trades
                  if t.get("side") == "yes" and t.get("buyer") == address)
    no_vol = sum(t["usdc_value"] for t in wallet_trades
                 if t.get("side") == "no" and t.get("buyer") == address)
    side = "yes" if yes_vol >= no_vol else "no"
    position_usd = yes_vol if side == "yes" else no_vol

    # No buy volume on either side (wallet only appears here as a seller in
    # this market) — not a conviction entry, and not worth the 2 downstream
    # API calls to profile further.
    if position_usd <= 0:
        return None

    # Last entry price for this side
    side_trades = [t for t in wallet_trades
                   if t.get("side") == side and t.get("buyer") == address]
    entry_price = side_trades[-1]["price"] if side_trades else 0.0

    # Recent activity (last 24h trades on this market). Computed here, before
    # the pre-gate, because the gate scores on it — and computed from data we
    # already hold, so it costs nothing.
    now = datetime.now(timezone.utc)
    recent_market_trades = []
    for t in wallet_trades:
        ts = t.get("timestamp")
        if ts:
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if (now - dt).total_seconds() < 86400:
                    recent_market_trades.append(t)
            except (ValueError, TypeError):
                pass

    # Free local pre-gate: if no local signal, skip paid API calls
    local_signal = False

    if side == "yes" and entry_price >= 0.75:
        local_signal = True
    elif side == "no" and entry_price <= 0.25:
        local_signal = True

    if position_usd >= 5000:
        local_signal = True

    if len(recent_market_trades) >= 3:
        local_signal = True

    if not local_signal:
        return None

    # Fetch wallet-level data with rate-limit pauses
    try:
        all_trades = trades_by_address(address, limit=100, guard=guard)
        time.sleep(INTER_CALL_DELAY_S)
    except CreditGuardExceeded:
        raise
    except NansenError as e:
        logger.warning("trades_by_address failed for %s: %s", address[:10], e)
        all_trades = []

    try:
        balances = historical_balances(address, chain="polygon", days=365, limit=100, guard=guard)
        time.sleep(INTER_CALL_DELAY_S)
    except CreditGuardExceeded:
        raise
    except NansenError as e:
        logger.warning("historical_balances failed for %s: %s", address[:10], e)
        balances = []

    has_known_label = _is_known_entity(address)
    age_days = wallet_age_days(balances)
    distinct_markets = len({t["market_id"] for t in all_trades if t["market_id"]})


    # ---------------------------------------------------------------------------
    # Score
    # ---------------------------------------------------------------------------
    score = 0
    tags: list[str] = []

    # Wallet age
    if age_days is not None:
        if age_days <= 7:
            score += FLAG_NEW_WALLET
            tags.append("NEW_WALLET")
        elif age_days <= 28:
            score += FLAG_YOUNG_WALLET
            tags.append("YOUNG_WALLET")
    # else: age unknown — skip age flags

    # Market focus
    if distinct_markets <= 1:
        score += FLAG_SINGLE_MARKET
        tags.append("SINGLE_MARKET")
    elif distinct_markets <= 3:
        score += FLAG_FEW_MARKETS
        tags.append("FEW_MARKETS")

    # Entry price (buying conviction near certainty)
    if side == "yes" and entry_price >= 0.75:
        score += FLAG_HIGH_ENTRY_PRICE
        tags.append("HIGH_YES_ENTRY")
    elif side == "no" and entry_price <= 0.25:
        score += FLAG_HIGH_ENTRY_PRICE
        tags.append("HIGH_NO_ENTRY")

    # Position size
    if position_usd >= 5000:
        score += FLAG_LARGE_POSITION
        tags.append("LARGE_POSITION")

    # Rapid accumulation
    if len(recent_market_trades) >= 3:
        score += FLAG_RAPID_ACCUMULATION
        tags.append("RAPID_ACCUMULATION")

    # Known entity discount
    if has_known_label:
        score += FLAG_KNOWN_ENTITY
        tags.append("KNOWN_ENTITY")

    if score < SUSPICION_THRESHOLD:
        return None

    # ---------------------------------------------------------------------------
    # Confidence + edge
    # ---------------------------------------------------------------------------
    # Confidence: scale score to [0, 1] against max possible score
    max_possible = (FLAG_NEW_WALLET + FLAG_SINGLE_MARKET + FLAG_HIGH_ENTRY_PRICE +
                    FLAG_LARGE_POSITION + FLAG_RAPID_ACCUMULATION)
    confidence = min(1.0, max(0.0, score / max_possible))

    # Edge: heuristic — higher entry price on YES suggests strong conviction
    # Edge = how far from 0.50 the entry was (normalised)
    if side == "yes":
        edge = min(1.0, abs(entry_price - 0.50) * 2)
    else:
        edge = min(1.0, abs((1.0 - entry_price) - 0.50) * 2)

    if score >= HIGH_RISK_THRESHOLD:
        tags.insert(0, "HIGH_RISK")

    market_question = wallet_trades[0].get("market_question", "") if wallet_trades else ""

    return InsiderSignal(
        market_id=market_id,
        market_question=market_question,
        wallet_address=address,
        side=side,
        confidence=round(confidence, 4),
        edge=round(edge, 4),
        suspicion_score=score,
        reason_tags=tags,
        entry_price=round(entry_price, 4),
        position_usd=round(position_usd, 2),
        wallet_age_days=round(age_days, 1) if age_days is not None else None,
        distinct_markets_traded=distinct_markets,
        has_known_label=has_known_label,
        dry_run=dry_run,
    )


def scan_market(
    market_id: str,
    dry_run: bool = True,
    min_suspicion_score: int = SUSPICION_THRESHOLD,
    max_wallets: int = 20,
    guard: Optional[CreditGuard] = None,
) -> list[InsiderSignal]:
    """
    Scan a single live market for suspicious wallets.

    Returns a list of InsiderSignal objects, sorted by suspicion_score descending.
    """
    max_wallets = min(max_wallets, HARD_MAX_WALLETS_PER_MARKET)
    signals: list[InsiderSignal] = []

    try:
        trades = trades_by_market(market_id, limit=100, guard=guard)
        time.sleep(INTER_CALL_DELAY_S)
    except CreditGuardExceeded:
        raise
    except NansenError as e:
        logger.warning("trades_by_market failed for market %s: %s", market_id, e)
        return []

    # Collect unique buyer addresses
    buyers = list(dict.fromkeys(
        t["buyer"] for t in trades if t.get("buyer") and t["buyer"] != "0x"
    ))[:max_wallets]

    for address in buyers:
        try:
            signal = _score_wallet(address, market_id, trades, dry_run=dry_run, guard=guard)
            if signal and signal.suspicion_score >= min_suspicion_score:
                signals.append(signal)
                logger.info(
                    "Suspicious: %s on market %s score=%d tags=%s dry_run=%s",
                    address[:10], market_id, signal.suspicion_score, signal.reason_tags, dry_run,
                )
        except CreditGuardExceeded:
            raise
        except Exception as e:
            logger.warning("Error scoring wallet %s: %s", address[:10], e)
            continue

    signals.sort(key=lambda s: s.suspicion_score, reverse=True)
    return signals


def scan_live_markets(
    market_ids: Optional[list[str]] = None,
    dry_run: bool = True,
    min_suspicion_score: int = SUSPICION_THRESHOLD,
    top_markets: int = 10,
    max_wallets_per_market: int = 15,
    guard: Optional[CreditGuard] = None,
) -> list[InsiderSignal]:
    """
    Main entry point: scan one or more live markets for insider patterns.

    Args:
        market_ids:           specific market IDs to scan, or None for auto-discovery
        dry_run:              must be True — this module is experimental and
                              dry-run only (see module docstring). Passing
                              dry_run=False raises LiveTradingNotSupportedError.
        min_suspicion_score:  minimum score to include in output (default 3)
        top_markets:          if market_ids is None, scan top N markets by volume
                              (hard-capped at HARD_MAX_MARKETS_PER_SCAN)
        max_wallets_per_market: max wallets to profile per market
                              (hard-capped at HARD_MAX_WALLETS_PER_MARKET)
        guard:                shared CreditGuard; a fresh one (default budget)
                              is created if not supplied, and shared across
                              every market scanned in this call.

    Returns:
        list[InsiderSignal] sorted by suspicion_score descending.

    --live gate:
        EXPERIMENTAL: dry_run=False is not supported yet (raises). Once
        owner/proxy handling and NO-side price semantics are confirmed
        against the live API and this guard is removed, the CALLER remains
        responsible for checking dry_run before submitting orders — this
        function never places trades itself.
    """
    if not dry_run:
        raise LiveTradingNotSupportedError(
            "nansen_live_insider_scan is experimental and dry-run only: "
            "owner/proxy wallet handling (historical_balances uses a proxy "
            "address with no owner mapping) and HIGH_NO_ENTRY price semantics "
            "are unconfirmed against the live Nansen API. See module docstring."
        )

    if guard is None:
        guard = CreditGuard()

    top_markets = min(top_markets, HARD_MAX_MARKETS_PER_SCAN)
    max_wallets_per_market = min(max_wallets_per_market, HARD_MAX_WALLETS_PER_MARKET)

    # Auto-discover markets if none specified
    if not market_ids:
        try:
            markets = market_screener(sort_by="volume_24hr", limit=top_markets,
                                       status="active", guard=guard)
            market_ids = [str(m.get("market_id") or m.get("id") or "") for m in markets]
            market_ids = [m for m in market_ids if m]
            logger.info("Auto-discovered %d live markets", len(market_ids))
        except NansenError as e:
            logger.error("market_screener failed: %s", e)
            return []
    else:
        if len(market_ids) > HARD_MAX_MARKETS_PER_SCAN:
            logger.warning(
                "%d market_ids exceed HARD_MAX_MARKETS_PER_SCAN=%d — scanning "
                "only the first %d, dropping %d",
                len(market_ids), HARD_MAX_MARKETS_PER_SCAN, HARD_MAX_MARKETS_PER_SCAN,
                len(market_ids) - HARD_MAX_MARKETS_PER_SCAN,
            )
            market_ids = market_ids[:HARD_MAX_MARKETS_PER_SCAN]

    all_signals: list[InsiderSignal] = []

    for i, market_id in enumerate(market_ids):
        logger.info("Scanning live market %s ...", market_id)
        try:
            signals = scan_market(
                market_id=market_id,
                dry_run=dry_run,
                min_suspicion_score=min_suspicion_score,
                max_wallets=max_wallets_per_market,
                guard=guard,
            )
        except CreditGuardExceeded:
            logger.warning(
                "Credit guard exhausted (max_calls=%d) after %d market(s) — "
                "stopping scan early, returning signals found so far",
                guard.max_calls, i,
            )
            break
        all_signals.extend(signals)
        time.sleep(INTER_CALL_DELAY_S)

    all_signals.sort(key=lambda s: s.suspicion_score, reverse=True)
    return all_signals


# ---------------------------------------------------------------------------
# CLI entry point (for standalone use)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Nansen live insider scan (EXPERIMENTAL — dry-run only)")
    parser.add_argument("--market-id", nargs="*", help="Market IDs to scan (default: auto-discover top 10)")
    parser.add_argument("--live", action="store_true",
                        help="NOT SUPPORTED YET — this module is experimental and always dry-run; "
                             "passing --live prints why and exits without scanning.")
    parser.add_argument("--min-score", type=int, default=SUSPICION_THRESHOLD,
                        help=f"Min suspicion score to report (default {SUSPICION_THRESHOLD})")
    parser.add_argument("--top-markets", type=int, default=10,
                        help=f"Auto-discover top N markets if no --market-id given (hard-capped at {HARD_MAX_MARKETS_PER_SCAN})")
    args = parser.parse_args()

    if args.live:
        print(
            "[REFUSED] --live is not supported yet: nansen_live_insider_scan is "
            "experimental and dry-run only until owner/proxy wallet handling and "
            "NO-side price semantics are confirmed against the live Nansen API. "
            "See the module docstring in nansen_live_insider_scan.py."
        )
        raise SystemExit(1)

    print("[DRY RUN] No orders will be submitted.")

    try:
        signals = scan_live_markets(
            market_ids=args.market_id or None,
            dry_run=True,
            min_suspicion_score=args.min_score,
            top_markets=args.top_markets,
        )
    except LiveTradingNotSupportedError as e:
        print(f"[REFUSED] {e}")
        raise SystemExit(1)

    if not signals:
        print("No suspicious wallets found.")
    else:
        print(f"\nFound {len(signals)} suspicious wallet(s):\n")
        for s in signals:
            print(json.dumps(s.as_dict(), indent=2))
            print()
