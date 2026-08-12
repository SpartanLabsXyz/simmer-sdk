"""
Module 2: nansen-copytrader-overlay-worldcup
=============================================
Same overlay pattern as nansen_copytrader_overlay_general — pnl-by-market
for the target market is the PRIMARY signal — tuned for the World Cup
market lifecycle:

- Filters the secondary/general Nansen PnL history to World Cup markets
  only, and blends a WC-specialist bonus into the secondary signal.
- Adds WC-specific reason tags (WC_SPECIALIST, WC_NOVICE, etc.)

World Cup market detection:
    A market is classified as WC if its question or event_title contains
    any of the WC_KEYWORDS below.  No external API call needed.

Integration point:
    Same interface as nansen_copytrader_overlay_general.enrich_leaders().
    Swap it in for WC-specific agent runs.
"""

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from nansen_adapter import (
    pnl_by_address,
    trades_by_address,
    address_summary,
    pnl_quality_score,
    compute_roi,
    get_proxy_address,
    get_owner_address,
    NansenError,
    CreditGuard,
    CreditGuardExceeded,
    INTER_CALL_DELAY_S,
)
from nansen_copytrader_overlay_general import (
    MIN_RESOLVED_MARKETS,
    NO_ADJUSTMENT_DISCOUNT,
    MARKET_SIGNAL_WEIGHT,
    GENERAL_SIGNAL_WEIGHT,
    DEFAULT_MAX_WALLETS,
    LeaderEnrichment,
    fetch_market_leaderboard,
    _compute_market_score,
    _blend_and_rank,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# WC configuration
# ---------------------------------------------------------------------------

# Bare "fifa", "knockout" and "final 2026" over-match (FIFA president
# elections, boxing knockouts, unrelated 2026 finals). "fifa" is narrowed to
# require a WC pairing; "knockout" is demoted to a weak keyword that only
# counts alongside an explicit world-cup mention; "final 2026" is dropped
# entirely — genuine WC finals still match via "quarter/semi-final" below.
WC_KEYWORDS = re.compile(
    r"world\s*cup|wc\s*202[456]|fifwc|fifa\s*(?:world\s*cup|202[456])|"
    r"group\s+[a-h]\b|quarter.final|semi.final|"
    r"\bwc\b.*\b(winner|champion|advance|goal|score)",
    re.IGNORECASE,
)

_WC_WEAK_KEYWORDS = re.compile(r"\bknockout\b", re.IGNORECASE)
_WC_CONTEXT = re.compile(r"world\s*cup|wc\s*202[456]|fifwc", re.IGNORECASE)

# WC-specific secondary-signal sub-weights (used within GENERAL_SIGNAL_WEIGHT's
# share of the overall blend — the market_score from pnl-by-market for the
# target market remains the primary signal, same as overlay-general).
WEIGHT_WIN_RATE = 0.25
WEIGHT_AVG_ROI = 0.20
WEIGHT_CONSISTENCY = 0.20
WEIGHT_CONCENTRATION_PENALTY = 0.10
WEIGHT_VOLUME = 0.10
WEIGHT_WC_SPECIALIST = 0.15   # bonus for WC-focused history


def _is_wc_market(question: str) -> bool:
    q = question or ""
    if WC_KEYWORDS.search(q):
        return True
    return bool(_WC_WEAK_KEYWORDS.search(q) and _WC_CONTEXT.search(q))


@dataclass
class WCLeaderEnrichment(LeaderEnrichment):
    """Extended enrichment with WC-specific features."""
    wc_resolved_count: int = 0
    wc_win_rate: Optional[float] = None
    wc_avg_roi: Optional[float] = None
    wc_specialist_score: float = 0.0   # 0–1: fraction of resolved PnL that is WC


def _compute_wc_quality_score(
    general_features: dict,
    wc_features: dict,
    distinct_markets: int,
) -> tuple[float, list[str]]:
    """
    Compute the SECONDARY (general + WC-specialist) quality score from broad
    wallet history. This blends into the overall score at GENERAL_SIGNAL_WEIGHT
    — the primary signal is still pnl-by-market for the target market.

    Returns (score, reason_tags).
    """
    from nansen_copytrader_overlay_general import _compute_quality_score

    tags: list[str] = []
    resolved_count = general_features.get("resolved_count", 0)

    if resolved_count < MIN_RESOLVED_MARKETS:
        tags.append(f"INSUFFICIENT_HISTORY:{resolved_count}_resolved")
        return 0.0, tags

    # --- General components (same rubric as overlay-general's secondary signal) ---
    score_general, general_tags = _compute_quality_score(general_features, distinct_markets)
    tags.extend(general_tags)

    # --- WC-specialist bonus ---
    wc_resolved = wc_features.get("wc_resolved_count", 0)
    wc_specialist_score = 0.0
    if resolved_count > 0:
        wc_specialist_score = min(1.0, wc_resolved / max(1, resolved_count))

    if wc_specialist_score >= 0.5:
        tags.append("WC_SPECIALIST")
    elif wc_specialist_score > 0 and wc_specialist_score < 0.2:
        tags.append("WC_NOVICE")

    wc_win_rate = wc_features.get("wc_win_rate")
    if wc_win_rate is not None:
        if wc_win_rate >= 0.65:
            tags.append("WC_HIGH_WIN_RATE")
        elif wc_win_rate < 0.35:
            tags.append("WC_LOW_WIN_RATE")

    specialist_component = wc_specialist_score * WEIGHT_WC_SPECIALIST
    general_weight = WEIGHT_WIN_RATE + WEIGHT_AVG_ROI + WEIGHT_CONSISTENCY + \
                     WEIGHT_CONCENTRATION_PENALTY + WEIGHT_VOLUME

    score = score_general * (general_weight / (general_weight + WEIGHT_WC_SPECIALIST)) + \
            specialist_component

    score = max(0.0, min(1.0, score))

    wc_avg_roi = wc_features.get("wc_avg_roi")
    if wc_avg_roi is not None and wc_avg_roi > 0:
        roi_bonus = min(0.05, wc_avg_roi * 0.02)  # small boost for positive WC ROI
        score = min(1.0, score + roi_bonus)
        if wc_avg_roi >= 2.0:
            tags.append("STRONG_WC_ROI")

    return round(score, 4), tags


def _fetch_wc_secondary_signal(proxy: str, owner: str, guard: CreditGuard, enrichment: WCLeaderEnrichment) -> Optional[float]:
    """
    Shared secondary-signal fetch: address_summary pre-filter, then full
    pnl_by_address/trades_by_address + WC filtering. Mutates `enrichment`
    in place with whatever features were computed; returns the WC quality
    score, or None if no usable secondary signal was obtained.
    """
    # address_summary/pnl_by_address/trades_by_address are all
    # prediction-market endpoints keyed by the PROXY address. A missing
    # owner is worth recording but must not gate this block — only profiler
    # endpoints need the owner wallet.
    if not owner:
        enrichment.reason_tags.append("MISSING_OWNER_ADDRESS")

    try:
        summary = address_summary(proxy, guard=guard)
        time.sleep(INTER_CALL_DELAY_S)

        if summary["resolved_count"] < MIN_RESOLVED_MARKETS:
            enrichment.reason_tags.append(f"INSUFFICIENT_HISTORY:{summary['resolved_count']}_resolved")
            return None

        pnl_rows = pnl_by_address(proxy, limit=100, guard=guard)
        time.sleep(INTER_CALL_DELAY_S)
        trade_rows = trades_by_address(proxy, limit=200, guard=guard)
        time.sleep(INTER_CALL_DELAY_S)

        general_features = pnl_quality_score(pnl_rows, min_resolved=MIN_RESOLVED_MARKETS)

        wc_pnl_rows = [r for r in pnl_rows if _is_wc_market(r.get("question", ""))]
        wc_resolved = [r for r in wc_pnl_rows if r.get("market_resolved")]
        wc_resolved_count = len(wc_resolved)

        wc_win_rate = None
        wc_avg_roi = None
        if wc_resolved:
            wins = sum(1 for r in wc_resolved if r["total_pnl_usd"] > 0)
            wc_win_rate = wins / len(wc_resolved)
            rois = [r["total_pnl_usd"] / r["net_buy_cost_usd"]
                    for r in wc_resolved if r["net_buy_cost_usd"] > 0]
            if rois:
                wc_avg_roi = sum(rois) / len(rois)

        wc_features = {
            "wc_resolved_count": wc_resolved_count,
            "wc_win_rate": wc_win_rate,
            "wc_avg_roi": wc_avg_roi,
        }

        distinct_markets = len({t["market_id"] for t in trade_rows if t["market_id"]})
        score, tags = _compute_wc_quality_score(general_features, wc_features, distinct_markets)

        enrichment.win_rate = general_features["win_rate"]
        enrichment.avg_roi = general_features["avg_roi"]
        enrichment.consistency = general_features["consistency"]
        enrichment.concentration = general_features["concentration"]
        enrichment.resolved_count = general_features["resolved_count"]
        enrichment.distinct_markets_traded = distinct_markets
        enrichment.wc_resolved_count = wc_resolved_count
        enrichment.wc_win_rate = wc_win_rate
        enrichment.wc_avg_roi = wc_avg_roi
        enrichment.wc_specialist_score = wc_resolved_count / max(1, general_features["resolved_count"])
        enrichment.reason_tags.extend(tags)

        if any(t.startswith("INSUFFICIENT_HISTORY") for t in tags):
            return None
        return score
    except CreditGuardExceeded:
        raise
    except NansenError as e:
        logger.warning("WC secondary fetch failed for %s: %s", proxy[:10], e)
        enrichment.reason_tags.append("NANSEN_FETCH_ERROR")
        return None


def enrich_leader_worldcup_for_market(
    leader: dict, leaderboard: Optional[dict], leaderboard_size: int, guard: CreditGuard,
) -> WCLeaderEnrichment:
    """Market-first WC enrichment — pnl-by-market for the target market is primary."""
    proxy = get_proxy_address(leader)
    enrichment = WCLeaderEnrichment(address=proxy or leader.get("wallet_address", ""))

    if not proxy:
        enrichment.error = "MISSING_PROXY_ADDRESS"
        enrichment.reason_tags = ["MISSING_PROXY_ADDRESS"]
        return enrichment

    if leaderboard is None:
        enrichment.reason_tags = ["MARKET_FETCH_ERROR"]
        return enrichment

    entry = leaderboard.get(proxy.lower())
    if entry is None:
        enrichment.reason_tags = ["NOT_IN_MARKET_LEADERBOARD"]
        return enrichment

    rank, row = entry
    # owner_address comes back on the leaderboard row we already fetched.
    owner = get_owner_address(leader, row)
    market_score, market_tags = _compute_market_score(row, rank, leaderboard_size)
    enrichment.market_score = market_score
    enrichment.market_total_pnl_usd = row.get("total_pnl_usd")
    enrichment.market_roi = compute_roi(row.get("net_buy_cost_usd", 0.0), row.get("total_pnl_usd", 0.0))
    enrichment.reason_tags = list(market_tags)
    enrichment.has_signal = True

    wc_score = _fetch_wc_secondary_signal(proxy, owner, guard, enrichment)

    if wc_score is None:
        enrichment.quality_score = market_score
    else:
        enrichment.quality_score = round(
            MARKET_SIGNAL_WEIGHT * market_score + GENERAL_SIGNAL_WEIGHT * wc_score, 4
        )

    return enrichment


def enrich_leader_worldcup(leader: dict, guard: CreditGuard) -> WCLeaderEnrichment:
    """
    Legacy (no target market) WC enrichment: broad wallet history filtered to
    WC markets only. Prefer enrich_leaders(..., market_id=...) — this path
    exists for market-agnostic WC leaderboards only.
    """
    proxy = get_proxy_address(leader)
    owner = get_owner_address(leader)
    enrichment = WCLeaderEnrichment(address=proxy or leader.get("wallet_address", ""))

    if not proxy:
        enrichment.error = "MISSING_PROXY_ADDRESS"
        enrichment.reason_tags = ["MISSING_PROXY_ADDRESS"]
        return enrichment

    score = _fetch_wc_secondary_signal(proxy, owner, guard, enrichment)
    if score is not None:
        enrichment.quality_score = score
        enrichment.has_signal = True
    return enrichment


def enrich_leaders(
    leaders: list[dict],
    market_id: Optional[str] = None,
    dry_run: bool = True,
    top_n: Optional[int] = None,
    base_score_key: str = "wallet_score",
    max_wallets: int = DEFAULT_MAX_WALLETS,
    guard: Optional[CreditGuard] = None,
) -> list[dict]:
    """
    World Cup variant of enrich_leaders — same interface as
    nansen_copytrader_overlay_general.enrich_leaders(). Pass `market_id` for
    the primary pnl-by-market signal (recommended); omitted falls back to a
    WC-filtered broad-history ranking only.
    """
    if not leaders:
        return []

    # See the same guard in the general overlay: 0 is falsy and skips the cap
    # branch entirely, a negative value becomes a slice bound. Both enrich MORE
    # wallets than the caller asked for, on the caller's own credits.
    if max_wallets < 1:
        raise ValueError(
            f"max_wallets must be >= 1, got {max_wallets}. It is a hard credit "
            "cap; 0 and negative values would enrich more wallets, not fewer."
        )

    if guard is None:
        guard = CreditGuard()

    to_enrich = leaders
    skipped: list[dict] = []
    if len(leaders) > max_wallets:
        ranked = sorted(leaders, key=lambda l: float(l.get(base_score_key) or 0.0), reverse=True)
        to_enrich, skipped = ranked[:max_wallets], ranked[max_wallets:]
        logger.warning(
            "%d leaders exceed max_wallets=%d — enriching the top %d by %s, "
            "keeping the remaining %d at their original score (CREDIT_GUARD_MAX_WALLETS)",
            len(leaders), max_wallets, max_wallets, base_score_key, len(skipped),
        )

    enriched_pairs: list[tuple[dict, WCLeaderEnrichment]] = []
    guard_exhausted = False

    if market_id is None:
        logger.warning(
            "enrich_leaders() called without market_id — falling back to WC-filtered "
            "broad wallet-history ranking, which is NOT specific to the target market. "
            "Pass market_id to use pnl-by-market, the primary copytrader signal."
        )
        for leader in to_enrich:
            proxy = get_proxy_address(leader)
            if guard_exhausted and proxy:
                enrichment = WCLeaderEnrichment(address=proxy, reason_tags=["CREDIT_GUARD_EXHAUSTED"])
            else:
                try:
                    enrichment = enrich_leader_worldcup(leader, guard)
                except CreditGuardExceeded:
                    guard_exhausted = True
                    logger.warning(
                        "Credit guard exhausted (max_calls=%d) mid-run — remaining "
                        "leaders keep their original score", guard.max_calls,
                    )
                    enrichment = WCLeaderEnrichment(
                        address=proxy or leader.get("wallet_address", ""),
                        reason_tags=["CREDIT_GUARD_EXHAUSTED"],
                    )
            enriched_pairs.append((leader, enrichment))
    else:
        leaderboard, leaderboard_size = fetch_market_leaderboard(market_id, guard)
        for leader in to_enrich:
            proxy = get_proxy_address(leader)
            if guard_exhausted:
                enrichment = WCLeaderEnrichment(
                    address=proxy or leader.get("wallet_address", ""),
                    reason_tags=["CREDIT_GUARD_EXHAUSTED"],
                )
            else:
                try:
                    enrichment = enrich_leader_worldcup_for_market(leader, leaderboard, leaderboard_size, guard)
                except CreditGuardExceeded:
                    guard_exhausted = True
                    logger.warning(
                        "Credit guard exhausted (max_calls=%d) mid-run — remaining "
                        "leaders keep their original score", guard.max_calls,
                    )
                    enrichment = WCLeaderEnrichment(
                        address=proxy or leader.get("wallet_address", ""),
                        reason_tags=["CREDIT_GUARD_EXHAUSTED"],
                    )
            enriched_pairs.append((leader, enrichment))

    for leader in skipped:
        enriched_pairs.append((leader, WCLeaderEnrichment(
            address=get_proxy_address(leader) or leader.get("wallet_address", ""),
            reason_tags=["CREDIT_GUARD_MAX_WALLETS"],
        )))

    extra_features = {
        e.address: {
            "wc_resolved_count": e.wc_resolved_count,
            "wc_win_rate": e.wc_win_rate,
            "wc_avg_roi": e.wc_avg_roi,
            "wc_specialist_score": e.wc_specialist_score,
        }
        for _, e in enriched_pairs
    }

    for leader, enrichment in enriched_pairs:
        logger.info(
            "WC leader %s: quality=%.3f wc_resolved=%d tags=%s",
            enrichment.address[:10], enrichment.quality_score,
            enrichment.wc_resolved_count, enrichment.reason_tags,
        )

    return _blend_and_rank(enriched_pairs, dry_run, top_n, base_score_key, extra_features=extra_features)
