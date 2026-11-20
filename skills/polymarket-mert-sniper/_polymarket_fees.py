# Generated from simmer_sdk/polymarket_fees.py by scripts/sync_skill_fee_helpers.py.
"""Polymarket.com DeFi schedules. Unknown is None; a known zero is fee-free.

Sources and venue units: docs/polymarket-fee-units.md. These are estimates;
match-time venue parameters take precedence over category schedules.
"""
from datetime import datetime, timezone
import math
import os
import re
from typing import NamedTuple, Optional

CUTOFF = datetime(2026, 11, 20, 23, 59, tzinfo=timezone.utc)

_CURRENT = {
    'crypto': .07, 'sports': .05, 'finance': .04, 'politics': .04,
    'economics': .05, 'culture': .05, 'weather': .05, 'other': .05,
    'mentions': .04, 'tech': .04, 'geopolitics': 0., 'combos': .05,
}
_NEW = {
    'crypto_btc_short': .05, 'crypto_other': .01, 'sports': .05,
    'finance': .05, 'tech': .05, 'politics_elections': .05,
    'politics_other': .01, 'economics': .01, 'culture': .01,
    'weather': .01, 'mentions': .01, 'geopolitics': 0.,
    'combos': .05, 'other': .05,
}
_PARENT = {
    'crypto_btc_short': 'crypto', 'crypto_other': 'crypto',
    'politics_elections': 'politics', 'politics_other': 'politics',
}
_WEIGHTS = {
    'sports': 1., 'politics': 1.3, 'finance': 1.3, 'mentions': 1.3,
    'tech': 1.3, 'economics': 1.7, 'culture': 1.7, 'weather': 1.7,
    'other': 1.7, 'crypto': 2.3, 'geopolitics': 0.,
}
_TAKER_CURRENT = (
    (0, 'none', 0.), (2_000, 'bronze', .03), (20_000, 'silver', .08),
    (200_000, 'gold', .18), (1_000_000, 'platinum', .32),
    (4_000_000, 'diamond', .44), (10_000_000, 'obsidian', .50),
)
_TAKER_NEW = (
    (0, 'none', 0.), (200_000, 'gold', .10), (1_000_000, 'platinum', .25),
    (4_000_000, 'diamond', .40), (10_000_000, 'obsidian', .50),
    (75_000_000, 'onyx', .60),
)
_MAKER_NEW = (
    (0, 'none', 0.), (200_000, 'tier 1', .03), (1_000_000, 'tier 2', .07),
    (4_000_000, 'tier 3', .10), (10_000_000, 'tier 4', .15),
)


def _new_schedule(at: datetime) -> bool:
    if at.utcoffset() is None:
        raise ValueError('schedule timestamp must include a timezone')
    return at >= CUTOFF


def fee_time(now: datetime) -> datetime:
    """Use the harness's frozen tick during replay, failing on a missing clock."""
    if os.environ.get('SIMMER_REPLAY') == '1':
        frozen = os.environ.get('SIMMER_REPLAY_NOW')
        if not frozen:
            raise ValueError('SIMMER_REPLAY_NOW is required for replay fee estimates')
        now = datetime.fromisoformat(frozen.replace('Z', '+00:00'))
    _new_schedule(now)
    return now


def classify_market(category=None, *, slug=None, asset=None, window=None,
                    up_down=None, elections=None, geopolitics=None, combo=False) -> Optional[str]:
    """Classify explicit metadata only; never infer from a question's keywords.

    Other politics requires both elections=False and geopolitics=False.
    A coarse crypto/politics category remains unresolved under the new rules.
    """
    if combo:
        return 'combos'
    category = category.strip().lower() if isinstance(category, str) else None
    category = {'general': 'other', 'other / general': 'other',
                'elections': 'politics_elections'}.get(category, category)
    match = re.fullmatch(r'(btc|eth|sol|xrp)-updown-(5m|15m|1h|4h)-\d+', slug or '')
    if match and category in (None, 'crypto'):
        if ((asset is not None and str(asset).lower() != match[1])
                or (window is not None and window != match[2]) or up_down is False):
            return None
        asset, window, up_down = match[1], match[2], True
        category = 'crypto'
    if category == 'crypto':
        if isinstance(asset, str) and asset.upper() in ('BTC', 'ETH', 'SOL', 'XRP'):
            if asset.upper() != 'BTC' or up_down is False:
                return 'crypto_other'
            if up_down is True and window in ('5m', '15m'):
                return 'crypto_btc_short'
            if up_down is True and window in ('1h', '4h', '1d'):
                return 'crypto_other'
    if category == 'politics':
        if geopolitics is True:
            return None if elections is True else 'geopolitics'
        if elections is True:
            return 'politics_elections'
        if elections is False and geopolitics is False:
            return 'politics_other'
    return category if category in _CURRENT or category in _NEW else None


def fee_rate(category: Optional[str], at: datetime) -> Optional[float]:
    if _new_schedule(at):
        return _NEW.get(category)
    return _CURRENT.get(_PARENT.get(category, category))


def fee_coefficient_from_clob(payload) -> Optional[float]:
    """Read fd.r only for the documented exponent-one curve, never base_fee.

    Missing/legacy zero is unknown. Explicit fd.r=0 with fd.e=1 is known zero.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get('fd'), dict):
        return None
    curve = payload['fd']
    rate = curve.get('r')
    if (isinstance(curve.get('e'), bool) or curve.get('e') != 1 or isinstance(rate, bool)
            or not isinstance(rate, (int, float)) or not math.isfinite(rate)
            or not 0 <= rate <= 1):
        return None
    return float(rate)


class FeeEstimate(NamedTuple):
    coefficient: float
    source: str


def estimate_fee_rate(category: Optional[str], at: datetime, venue_payload=None) -> Optional[FeeEstimate]:
    """Prefer explicit venue parameters; label a known schedule fallback.

    A returned but unsupported curve must not be replaced by exponent one.
    Legacy base_fee alone supplies no coefficient and never implies zero.
    """
    if isinstance(venue_payload, dict) and 'fd' in venue_payload:
        rate = fee_coefficient_from_clob(venue_payload)
        return FeeEstimate(rate, 'venue fd.r') if rate is not None else None
    rate = fee_rate(category, at)
    return FeeEstimate(rate, 'category schedule fallback') if rate is not None else None


def _tier_rate(rows, volume_30d, tier) -> Optional[float]:
    named = None
    if tier is not None:
        if not isinstance(tier, str):
            return None
        named = next((row for row in rows if row[1] == tier.lower()), None)
        if named is None:
            return None
    if volume_30d is None:
        return named[2] if named else None
    if (isinstance(volume_30d, bool) or not isinstance(volume_30d, (int, float))
            or not math.isfinite(volume_30d) or volume_30d < 0):
        return None
    earned = next(row for row in reversed(rows) if volume_30d >= row[0])
    if named is not None and named != earned:
        return None
    return earned[2]


def taker_rebate_rate(at: datetime, *, volume_30d=None, tier=None) -> Optional[float]:
    rows = _TAKER_NEW if _new_schedule(at) else _TAKER_CURRENT
    return _tier_rate(rows, volume_30d, tier)


def maker_pool_fraction(category: Optional[str], at: datetime) -> Optional[float]:
    """Pre-cutoff share of collected fees funding the market's daily pool.

    This is not an individual maker's rebate rate.
    """
    if _new_schedule(at):
        return None
    category = _PARENT.get(category, category)
    if category not in _CURRENT:
        return None
    return {'crypto': .20, 'sports': .15, 'geopolitics': 0., 'combos': 0.}.get(category, .25)


def maker_rebate_rate(category: Optional[str], at: datetime, *,
                      volume_30d=None, tier=None) -> Optional[float]:
    """Post-cutoff fraction of taker fees generated by eligible maker fills."""
    new = _new_schedule(at)
    if category in ('geopolitics', 'combos'):
        return 0.
    if not new or fee_rate(category, at) is None:
        return None
    return _tier_rate(_MAKER_NEW, volume_30d, tier)


def weighted_volume(shares: float, price: float, category: Optional[str],
                    at: datetime, *, fee_paying: Optional[bool],
                    role: str = 'taker', bonus: float = 1.) -> Optional[float]:
    """Contribution to the role's 30-day wV; no account tier is inferred.

    Current combo category weight is undocumented, so it remains unknown.
    Only the pre-cutoff taker program uses category and promotional multipliers.
    """
    new = _new_schedule(at)
    if role not in ('taker', 'maker'):
        raise ValueError('role must be taker or maker')
    if not math.isfinite(shares) or shares < 0 or not 0 < price < 1:
        raise ValueError('fill requires nonnegative shares and price between 0 and 1')
    if fee_paying is False or category == 'geopolitics' or (role == 'maker' and category == 'combos'):
        return 0.
    if fee_paying is not True or fee_rate(category, at) is None:
        return None
    if role == 'maker' and not new:
        return None
    if new:
        return shares * price * (1 - price)
    weight = _WEIGHTS.get(_PARENT.get(category, category))
    if weight is None:
        return None
    if not math.isfinite(bonus) or bonus < 0:
        raise ValueError('bonus must be finite and nonnegative')
    return shares * price * (1 - price) * weight * bonus
