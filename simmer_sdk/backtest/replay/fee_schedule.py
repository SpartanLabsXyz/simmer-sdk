# vendored from simmer_v3/replay/fee_schedule.py @ c243eae175a3
# DO NOT EDIT HERE — regenerate via scripts/sync_replay_engine.py
"""Polymarket DeFi coefficients for replay, selected at the fill timestamp.

Categories are explicit inputs, never inferred from market titles. The live
CLOB fee lookup remains the authority for live orders.
"""

from datetime import datetime, timezone


CUTOFF = datetime(2026, 11, 20, 23, 59, tzinfo=timezone.utc)
FEE_POLICY = "polymarket-2026-11-20"
GENERIC_FEE_RATE = 0.05

_CURRENT = {
    "crypto": 0.07,
    "sports": 0.05,
    "finance": 0.04,
    "politics": 0.04,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other": 0.05,
    "general": 0.05,
    "mentions": 0.04,
    "tech": 0.04,
    "geopolitics": 0.0,
    "combos": 0.05,
}

_NEW = {
    "crypto_btc_5m_updown": 0.05,
    "crypto_btc_15m_updown": 0.05,
    "crypto_other": 0.01,
    "sports": 0.05,
    "finance": 0.05,
    "tech": 0.05,
    "politics_elections": 0.05,
    "politics_other": 0.01,
    "economics": 0.01,
    "culture": 0.01,
    "weather": 0.01,
    "mentions": 0.01,
    "geopolitics": 0.0,
    "combos": 0.05,
    "other": 0.05,
    "general": 0.05,
}

_COARSE = {
    "crypto_btc_5m_updown": "crypto",
    "crypto_btc_15m_updown": "crypto",
    "crypto_other": "crypto",
    "politics_elections": "politics",
    "politics_other": "politics",
}


def scheduled_fee_rate(category: str, at: datetime) -> float | None:
    """Return a coefficient or None for an unknown classification.

    Coarse crypto and politics are insufficient after the cutoff. A known
    zero coefficient, such as geopolitics, is distinct from unknown.
    """
    if at.utcoffset() is None:
        raise ValueError("fee timestamp must be timezone-aware")
    if at < CUTOFF:
        return _CURRENT.get(_COARSE.get(category, category))
    return _NEW.get(category)


def maker_rebate_gaps(t0: datetime, t1: datetime) -> list[str]:
    """Describe every maker program intersecting the inclusive replay window."""
    if t0.utcoffset() is None or t1.utcoffset() is None:
        raise ValueError("replay window must be timezone-aware")
    gaps = []
    if t0 < CUTOFF:
        gaps.append(
            "no maker rebates before 2026-11-20T23:59:00Z: daily pools receive "
            "20% of crypto taker fees, 15% for sports, 25% for other eligible "
            "categories; individual pool share is unknown and not credited"
        )
    if t1 >= CUTOFF:
        gaps.append(
            "no maker rebates at or after 2026-11-20T23:59:00Z: 30-day maker "
            "weighted-volume tiers pay 0/3/7/10/15% of eligible generated taker "
            "fees; tier is unknown and no rebates are credited; combos and "
            "fee-free trades are ineligible"
        )
    return gaps
