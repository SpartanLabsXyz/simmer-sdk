"""
Module 1: nansen-copytrader-overlay-general
============================================
Enriches a leader ranking (from Simmer's copytrading system) with Nansen
PnL quality features and returns an adjusted ranking with reason codes.

Core signal — pnl-by-market first:
    The primary signal is `pnl_by_market(market_id)`: who is actually
    profitable IN THE TARGET MARKET, fetched once per call and matched
    against each leader's proxy wallet. This is the Polymarket-specific
    edge Nansen has. Broad wallet history (`pnl_by_address` /
    `trades_by_address`) is a SECONDARY signal, pulled only for wallets
    that already clear a cheap `address_summary` pre-filter, and only to
    refine — never to replace — the market-specific score.

    Nansen is a data layer here: PM PnL quality + address-summary wallet
    quality. It is not a smart-money label — Nansen's smart-money labels
    are not Polymarket-specific and this module never calls them.

Integration point:
    Called BEFORE Simmer executes copytrading rebalances.  The caller
    (e.g. copytrading_strategy.py) passes the market being copytraded and
    a list of leader dicts and gets back the same list re-ranked by
    `adjusted_score`.

Usage:
    from nansen_copytrader_overlay_general import enrich_leaders

    enriched = enrich_leaders(
        market_id="12345",           # the market you're about to copytrade into
        leaders=[
            {"proxy_address": "0xabc...", "owner_address": "0xowner...",
             "wallet_score": 0.8},
        ],
        dry_run=True,         # always True unless --live was passed
        top_n=5,
    )
    # enriched[i] has all original keys plus:
    #   nansen_features, reason_tags, adjusted_score, adjusted_rank

Proxy vs owner wallets:
    Nansen indexes Polymarket activity (pnl-by-market, pnl-by-address,
    trades-by-address) by the PROXY wallet, and profiler data
    (address-summary) by the OWNER (signing) wallet. Leader dicts must
    carry both `proxy_address` and `owner_address` explicitly — there is
    no resolution step here. `address` is accepted as a legacy alias for
    `proxy_address` only.

Credit guards:
    Every call goes through a CreditGuard (see nansen_adapter.py): a hard
    max_credits budget plus a short TTL cache so re-enriching the same
    market/wallet within a run doesn't re-spend credits. `max_wallets`
    caps how many leaders get enriched at all — the rest keep their
    original score, tagged CREDIT_GUARD_MAX_WALLETS. If the call budget
    is exhausted mid-run, remaining leaders keep their original score too
    (tagged CREDIT_GUARD_EXHAUSTED) instead of the run crashing.

Dry-run default:
    When dry_run=True (the default) no trades are emitted — just the
    enriched ranking is returned.  The --live gate in the caller controls
    whether Simmer acts on the output.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from nansen_adapter import (
    pnl_by_market,
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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

# Overall quality blend: market-specific PnL (primary) vs. general wallet
# history (secondary refinement only). Weights sum to 1.0.
MARKET_SIGNAL_WEIGHT = 0.65
GENERAL_SIGNAL_WEIGHT = 0.35

# General (secondary) quality sub-weights — unchanged from the pre-market-first
# design, just demoted to a refinement signal.
WEIGHT_WIN_RATE = 0.30
WEIGHT_AVG_ROI = 0.25
WEIGHT_CONSISTENCY = 0.20
WEIGHT_CONCENTRATION_PENALTY = 0.15   # high concentration = penalty
WEIGHT_VOLUME = 0.10                   # total resolved markets = experience proxy

MIN_RESOLVED_MARKETS = 3  # minimum resolved markets to score general quality
MAX_CONCENTRATION = 0.70  # concentration above this gets max penalty

# Applied instead of the 50/50 blend when there's no usable Nansen signal at
# all (missing proxy address, wallet absent from this market's leaderboard,
# or the market fetch itself failed) — in each case we don't have a real
# signal to blend, so the leader's original score should barely move rather
# than take a ~50% haircut.
NO_ADJUSTMENT_DISCOUNT = 0.9

DEFAULT_MAX_WALLETS = 30       # hard cap on leaders enriched per call
DEFAULT_MARKET_LEADERBOARD_LIMIT = 100


@dataclass
class LeaderEnrichment:
    """Nansen-derived features and reason tags for a single leader wallet."""
    address: str

    # Market-specific (primary) features
    market_score: Optional[float] = None
    market_total_pnl_usd: Optional[float] = None
    market_roi: Optional[float] = None

    # General/secondary quality features
    win_rate: Optional[float] = None
    avg_roi: Optional[float] = None
    consistency: Optional[float] = None
    concentration: Optional[float] = None
    resolved_count: int = 0
    distinct_markets_traded: int = 0

    # Derived
    quality_score: float = 0.0    # 0–1 composite (market + general blend)
    has_signal: bool = False      # True once a real Nansen score (market or
                                   # legacy general) was computed for this wallet
    reason_tags: list[str] = field(default_factory=list)
    error: Optional[str] = None


def _compute_quality_score(features: dict, distinct_markets: int) -> tuple[float, list[str]]:
    """
    Compute a 0–1 GENERAL (secondary) quality score from broad wallet-history
    Nansen features. This is the same scoring rubric used before market-first
    became the primary signal — now only a refinement on top of market_score.

    Returns (score, reason_tags).
    """
    tags: list[str] = []
    score = 0.0
    available_weight = 0.0

    win_rate = features.get("win_rate")
    avg_roi = features.get("avg_roi")
    consistency = features.get("consistency")
    concentration = features.get("concentration")
    resolved_count = features.get("resolved_count", 0)

    if resolved_count < MIN_RESOLVED_MARKETS:
        tags.append(f"INSUFFICIENT_HISTORY:{resolved_count}_resolved")
        return 0.0, tags

    # Win rate component
    if win_rate is not None:
        score += WEIGHT_WIN_RATE * win_rate
        available_weight += WEIGHT_WIN_RATE
        if win_rate >= 0.70:
            tags.append("HIGH_WIN_RATE")
        elif win_rate < 0.40:
            tags.append("LOW_WIN_RATE")

    # ROI component (cap at 2x = 200%, normalise to 0-1; 150% avg ROI is already excellent)
    if avg_roi is not None:
        roi_norm = min(1.0, max(0.0, avg_roi / 2.0))
        score += WEIGHT_AVG_ROI * roi_norm
        available_weight += WEIGHT_AVG_ROI
        if avg_roi >= 1.0:
            tags.append("STRONG_ROI")
        elif avg_roi < 0:
            tags.append("NEGATIVE_AVG_ROI")

    # Consistency component
    if consistency is not None:
        score += WEIGHT_CONSISTENCY * consistency
        available_weight += WEIGHT_CONSISTENCY
        if consistency >= 0.70:
            tags.append("CONSISTENT_PNL")
        elif consistency < 0.30:
            tags.append("ERRATIC_PNL")

    # Concentration penalty
    if concentration is not None:
        penalty = min(1.0, concentration / MAX_CONCENTRATION)
        score -= WEIGHT_CONCENTRATION_PENALTY * penalty
        available_weight += WEIGHT_CONCENTRATION_PENALTY
        if concentration >= MAX_CONCENTRATION:
            tags.append("HIGH_CONCENTRATION_RISK")

    # Volume / experience component (normalise to 0-1, cap at 50 markets)
    if resolved_count > 0:
        vol_norm = min(1.0, resolved_count / 50.0)
        score += WEIGHT_VOLUME * vol_norm
        available_weight += WEIGHT_VOLUME
        if resolved_count >= 20:
            tags.append("EXPERIENCED_TRADER")
        elif resolved_count < 5:
            tags.append("LIMITED_HISTORY")

    # Normalise by available weight to handle missing features
    if available_weight > 0:
        score = score / available_weight * (WEIGHT_WIN_RATE + WEIGHT_AVG_ROI +
                                             WEIGHT_CONSISTENCY + WEIGHT_CONCENTRATION_PENALTY +
                                             WEIGHT_VOLUME)

    # Clamp
    score = max(0.0, min(1.0, score))

    if distinct_markets < 3:
        tags.append("NARROW_FOCUS")

    return round(score, 4), tags


def _compute_market_score(row: dict, rank: Optional[int], leaderboard_size: int) -> tuple[float, list[str]]:
    """
    Compute a 0–1 score from this wallet's pnl_by_market row for the target
    market — the PRIMARY copytrader signal.

    Returns (score, reason_tags).
    """
    tags: list[str] = []
    total_pnl = row.get("total_pnl_usd", 0.0)
    roi = compute_roi(row.get("net_buy_cost_usd", 0.0), total_pnl)

    tags.append("MARKET_PROFITABLE" if total_pnl > 0 else "MARKET_UNPROFITABLE")

    if roi is not None:
        score = min(1.0, max(0.0, roi / 2.0))
    else:
        # No cost basis to compute ROI (e.g. a fully airdropped/gifted
        # position) — fall back to a coarse profit/loss signal.
        score = 0.6 if total_pnl > 0 else 0.0

    if rank is not None and leaderboard_size > 0:
        if (rank + 1) / leaderboard_size <= 0.2:
            tags.append("MARKET_TOP_PNL")
            score = min(1.0, score + 0.15)

    return round(score, 4), tags


# ---------------------------------------------------------------------------
# Market leaderboard fetch (shared with the World Cup overlay variant)
# ---------------------------------------------------------------------------

def fetch_market_leaderboard(
    market_id: str,
    guard: CreditGuard,
    limit: int = DEFAULT_MARKET_LEADERBOARD_LIMIT,
) -> tuple[Optional[dict], int]:
    """
    Fetch the pnl_by_market leaderboard for `market_id` and index it by
    lowercased address AND owner_address for lookup.

    Returns (leaderboard_or_None, size). leaderboard is None if the fetch
    failed outright (market-specific ranking unavailable this run) —
    CreditGuardExceeded is NOT swallowed here, it propagates so the caller
    can stop the run rather than silently proceed with no signal at all.
    """
    try:
        rows = pnl_by_market(market_id, limit=limit, guard=guard)
    except CreditGuardExceeded:
        raise
    except NansenError as e:
        logger.error(
            "pnl_by_market fetch failed for market %s: %s — market-specific "
            "ranking unavailable this run, leaders keep their original score",
            market_id, e,
        )
        return None, 0

    leaderboard: dict[str, tuple[int, dict]] = {}
    for i, row in enumerate(rows):
        for key in (row.get("address"), row.get("owner_address")):
            if key:
                leaderboard.setdefault(key.lower(), (i, row))
    return leaderboard, len(rows)


# ---------------------------------------------------------------------------
# Per-leader enrichment
# ---------------------------------------------------------------------------

def enrich_leader_for_market(
    leader: dict,
    leaderboard: Optional[dict],
    leaderboard_size: int,
    guard: CreditGuard,
) -> LeaderEnrichment:
    """
    Enrich a single leader against an already-fetched market leaderboard.

    market_score (primary) comes from the leaderboard lookup — no extra
    API call. The general/secondary quality pull only happens for wallets
    that are IN the leaderboard, gated by a cheap address_summary check
    first (so a wallet with no meaningful history doesn't cost a full
    pnl_by_address + trades_by_address pull).
    """
    proxy = get_proxy_address(leader)
    enrichment = LeaderEnrichment(address=proxy or leader.get("wallet_address", ""))

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
    # owner_address rides along on every leaderboard row, so callers don't
    # have to supply it; it is still often the "0x" placeholder, in which
    # case this resolves to "" and profiler-keyed enrichment is skipped.
    owner = get_owner_address(leader, row)
    market_score, market_tags = _compute_market_score(row, rank, leaderboard_size)
    enrichment.market_score = market_score
    enrichment.market_total_pnl_usd = row.get("total_pnl_usd")
    enrichment.market_roi = compute_roi(row.get("net_buy_cost_usd", 0.0), row.get("total_pnl_usd", 0.0))
    enrichment.reason_tags = list(market_tags)
    enrichment.has_signal = True

    general_score: Optional[float] = None
    # Every call in this block is a prediction-market endpoint, so all three
    # take the PROXY address. `owner` is only needed by profiler endpoints,
    # which this overlay doesn't call — so a missing owner is recorded but
    # must not gate the secondary layer.
    if not owner:
        enrichment.reason_tags.append("MISSING_OWNER_ADDRESS")

    try:
        summary = address_summary(proxy, guard=guard)
        time.sleep(INTER_CALL_DELAY_S)

        if summary["resolved_count"] < MIN_RESOLVED_MARKETS:
            enrichment.reason_tags.append(
                f"INSUFFICIENT_HISTORY:{summary['resolved_count']}_resolved"
            )
        else:
            pnl_rows = pnl_by_address(proxy, limit=50, guard=guard)
            time.sleep(INTER_CALL_DELAY_S)
            trade_rows = trades_by_address(proxy, limit=100, guard=guard)
            time.sleep(INTER_CALL_DELAY_S)

            features = pnl_quality_score(pnl_rows, min_resolved=MIN_RESOLVED_MARKETS)
            distinct_markets = len({t["market_id"] for t in trade_rows if t["market_id"]})
            general_score, general_tags = _compute_quality_score(features, distinct_markets)

            enrichment.win_rate = features["win_rate"]
            enrichment.avg_roi = features["avg_roi"]
            enrichment.consistency = features["consistency"]
            enrichment.concentration = features["concentration"]
            enrichment.resolved_count = features["resolved_count"]
            enrichment.distinct_markets_traded = distinct_markets
            enrichment.reason_tags.extend(general_tags)
    except CreditGuardExceeded:
        raise
    except NansenError as e:
        logger.warning("General enrichment fetch failed for %s: %s", proxy[:10], e)
        enrichment.reason_tags.append("NANSEN_FETCH_ERROR")

    if general_score is None:
        enrichment.quality_score = market_score
    else:
        enrichment.quality_score = round(
            MARKET_SIGNAL_WEIGHT * market_score + GENERAL_SIGNAL_WEIGHT * general_score, 4
        )

    return enrichment


def _has_usable_signal(enrichment: LeaderEnrichment) -> bool:
    return enrichment.error is None and enrichment.has_signal


def _blend_and_rank(
    enriched_pairs: list[tuple[dict, LeaderEnrichment]],
    dry_run: bool,
    top_n: Optional[int],
    base_score_key: str,
    extra_features: Optional[dict] = None,
) -> list[dict]:
    output = []
    for leader, enrichment in enriched_pairs:
        base = float(leader.get(base_score_key) or 0.0)

        if _has_usable_signal(enrichment):
            adjusted = 0.5 * base + 0.5 * enrichment.quality_score
        else:
            adjusted = base * NO_ADJUSTMENT_DISCOUNT

        output.append({
            **leader,
            "nansen_features": {
                "market_score": enrichment.market_score,
                "market_total_pnl_usd": enrichment.market_total_pnl_usd,
                "market_roi": enrichment.market_roi,
                "win_rate": enrichment.win_rate,
                "avg_roi": enrichment.avg_roi,
                "consistency": enrichment.consistency,
                "concentration": enrichment.concentration,
                "resolved_count": enrichment.resolved_count,
                "distinct_markets_traded": enrichment.distinct_markets_traded,
                "quality_score": enrichment.quality_score,
                "error": enrichment.error,
                **(extra_features.get(enrichment.address, {}) if extra_features else {}),
            },
            "reason_tags": enrichment.reason_tags,
            "adjusted_score": round(adjusted, 4),
            "dry_run": dry_run,
        })

    output.sort(key=lambda x: x["adjusted_score"], reverse=True)
    for i, item in enumerate(output):
        item["adjusted_rank"] = i + 1

    if top_n:
        output = output[:top_n]
    return output


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

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
    Enrich a list of leader dicts with Nansen PnL quality features.

    Args:
        leaders:         list of dicts with at least {"proxy_address": ...,
                         "owner_address": ..., base_score_key: float}
        market_id:       the Polymarket market being copytraded. When given,
                         pnl_by_market(market_id) is the PRIMARY signal
                         (recommended — this is the market-specific edge).
                         When omitted, falls back to broad wallet-history
                         ranking only, with a warning.
        dry_run:         if True (default) no trades are emitted — just return ranking
        top_n:           if set, return only top N after adjustment
        base_score_key:  key in leader dict for the original score (default "wallet_score")
        max_wallets:     hard cap on leaders enriched this call (credit guard).
                         Leaders beyond the cap keep their original score.
        guard:           shared CreditGuard; a fresh one (default budget) is
                         created if not supplied.

    Returns:
        list of leader dicts, each extended with:
            nansen_features: dict  — raw Nansen quality features
            reason_tags:     list  — human-readable reason codes
            adjusted_score:  float — blended score (original 0.5 + nansen 0.5)
            adjusted_rank:   int   — 1-based rank after adjustment
            dry_run:         bool  — echoes the dry_run flag
    """
    if not leaders:
        return []

    # max_wallets is a credit cap, so a nonpositive value must be rejected
    # rather than quietly widened: 0 is falsy and would skip the cap branch
    # entirely (enriching every leader), and a negative value becomes a
    # slice bound (-1 enriches all but the last). Both spend MORE of the
    # caller's credits than the number they passed.
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

    if market_id is None:
        logger.warning(
            "enrich_leaders() called without market_id — falling back to broad "
            "wallet-history ranking (pnl-by-address/trades-by-address), which "
            "is NOT Polymarket-market-specific. Pass market_id to use "
            "pnl-by-market, the primary copytrader signal (see README)."
        )
        enriched_pairs = _enrich_pairs_legacy(to_enrich, guard)
    else:
        enriched_pairs = _enrich_pairs_for_market(to_enrich, market_id, guard)

    for leader in skipped:
        enriched_pairs.append((leader, LeaderEnrichment(
            address=get_proxy_address(leader) or leader.get("wallet_address", ""),
            reason_tags=["CREDIT_GUARD_MAX_WALLETS"],
        )))

    return _blend_and_rank(enriched_pairs, dry_run, top_n, base_score_key)


def _enrich_pairs_for_market(
    leaders: list[dict], market_id: str, guard: CreditGuard,
) -> list[tuple[dict, LeaderEnrichment]]:
    leaderboard, leaderboard_size = fetch_market_leaderboard(market_id, guard)

    enriched_pairs: list[tuple[dict, LeaderEnrichment]] = []
    guard_exhausted = False
    for leader in leaders:
        if guard_exhausted:
            enrichment = LeaderEnrichment(
                address=get_proxy_address(leader) or leader.get("wallet_address", ""),
                reason_tags=["CREDIT_GUARD_EXHAUSTED"],
            )
        else:
            try:
                enrichment = enrich_leader_for_market(leader, leaderboard, leaderboard_size, guard)
            except CreditGuardExceeded:
                guard_exhausted = True
                logger.warning(
                    "Credit guard exhausted (max_credits=%d) mid-run — remaining "
                    "leaders keep their original score", guard.max_credits,
                )
                enrichment = LeaderEnrichment(
                    address=get_proxy_address(leader) or leader.get("wallet_address", ""),
                    reason_tags=["CREDIT_GUARD_EXHAUSTED"],
                )
        enriched_pairs.append((leader, enrichment))
        logger.info(
            "Leader %s: quality=%.3f tags=%s",
            enrichment.address[:10], enrichment.quality_score, enrichment.reason_tags,
        )
    return enriched_pairs


def _enrich_pairs_legacy(
    leaders: list[dict], guard: CreditGuard,
) -> list[tuple[dict, LeaderEnrichment]]:
    """
    Legacy path: broad wallet-history ranking with no target market. Kept
    for callers that genuinely need a market-agnostic ranking (e.g. ranking
    leaders across many markets at once); prefer passing market_id.
    """
    enriched_pairs: list[tuple[dict, LeaderEnrichment]] = []
    guard_exhausted = False
    for leader in leaders:
        proxy = get_proxy_address(leader)
        enrichment = LeaderEnrichment(address=proxy or leader.get("wallet_address", ""))

        if not proxy:
            enrichment.error = "MISSING_PROXY_ADDRESS"
            enrichment.reason_tags = ["MISSING_PROXY_ADDRESS"]
            enriched_pairs.append((leader, enrichment))
            continue

        if guard_exhausted:
            enrichment.reason_tags = ["CREDIT_GUARD_EXHAUSTED"]
            enriched_pairs.append((leader, enrichment))
            continue

        try:
            pnl_rows = pnl_by_address(proxy, limit=50, guard=guard)
            time.sleep(INTER_CALL_DELAY_S)
            trade_rows = trades_by_address(proxy, limit=100, guard=guard)
            time.sleep(INTER_CALL_DELAY_S)

            features = pnl_quality_score(pnl_rows, min_resolved=MIN_RESOLVED_MARKETS)
            distinct_markets = len({t["market_id"] for t in trade_rows if t["market_id"]})
            quality_score, reason_tags = _compute_quality_score(features, distinct_markets)

            enrichment.win_rate = features["win_rate"]
            enrichment.avg_roi = features["avg_roi"]
            enrichment.consistency = features["consistency"]
            enrichment.concentration = features["concentration"]
            enrichment.resolved_count = features["resolved_count"]
            enrichment.distinct_markets_traded = distinct_markets
            enrichment.quality_score = quality_score
            enrichment.reason_tags = reason_tags
            enrichment.has_signal = not any(
                t.startswith("INSUFFICIENT_HISTORY") for t in reason_tags
            )
        except CreditGuardExceeded:
            guard_exhausted = True
            logger.warning(
                "Credit guard exhausted (max_credits=%d) mid-run — remaining "
                "leaders keep their original score", guard.max_credits,
            )
            enrichment.reason_tags = ["CREDIT_GUARD_EXHAUSTED"]
        except NansenError as e:
            logger.warning("Nansen fetch failed for %s: %s", proxy[:10], e)
            enrichment.error = str(e)
            enrichment.reason_tags = ["NANSEN_FETCH_ERROR"]

        enriched_pairs.append((leader, enrichment))
        logger.info(
            "Leader %s: quality=%.3f tags=%s",
            enrichment.address[:10], enrichment.quality_score, enrichment.reason_tags,
        )
    return enriched_pairs
