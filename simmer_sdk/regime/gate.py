"""
Realized-volatility regime gate.

Computes realized volatility (std-dev of per-candle price changes) over the
last N candles and returns a binary trade / no-trade decision based on the
regime the calling strategy is registered for.

Convention:
    HIGH realized vol  -> "trending"     (large directional moves)
    LOW  realized vol  -> "range_bound"  (small oscillations)

A strategy declares which regime it wants to trade in via `regime_strategy`:
    "range_bound" -> allowed when realized_vol  <  vol_threshold
    "trending"    -> allowed when realized_vol >=  vol_threshold

If fewer than `lookback_candles` price points are supplied, the gate
fails-closed (allowed=False, reason="insufficient_data") — the safe default
when we cannot estimate the regime.

The primitive is intentionally venue-agnostic: it takes a price series, not
a market id. Skills are responsible for fetching candles from whatever
venue they trade on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

# Default per-candle realized-vol threshold. Calibrated for prediction-market
# probability series (prices in [0, 1]) on minute-scale candles. Operators
# SHOULD tune this per asset / timeframe — there is no universal value.
DEFAULT_VOL_THRESHOLD = 0.02

REGIME_TRENDING = "trending"
REGIME_RANGE_BOUND = "range_bound"
_VALID_REGIMES = (REGIME_TRENDING, REGIME_RANGE_BOUND)


@dataclass(frozen=True)
class RegimeDecision:
    """Result of a regime-gate check.

    Attributes:
        allowed: True if the strategy may proceed to sizing, False if it
            must skip this opportunity.
        realized_vol: The computed realized volatility (std-dev of price
            changes). NaN-safe: returns 0.0 when undefined.
        regime: The regime classification the gate observed
            (`"trending"` or `"range_bound"`).
        reason: Short tag explaining the decision. One of:
            - "ok"                -> regime matched, allowed
            - "regime_mismatch"   -> regime did not match strategy
            - "insufficient_data" -> not enough candles supplied
            - "invalid_input"     -> non-finite or non-positive prices
        n_candles: Number of price points actually used.
    """

    allowed: bool
    realized_vol: float
    regime: str
    reason: str
    n_candles: int


def realized_volatility(prices: Sequence[float]) -> float:
    """Compute realized volatility as the std-dev of per-candle price changes.

    Uses arithmetic differences (`p[i] - p[i-1]`) rather than log returns,
    because prediction-market probabilities live in [0, 1] and can be near
    the boundaries where log returns blow up.

    Args:
        prices: Sequence of close prices, oldest first. Must contain at
            least 2 finite values.

    Returns:
        Population std-dev of consecutive price differences. Returns 0.0
        if fewer than 2 prices are supplied or all prices are identical.
    """
    if len(prices) < 2:
        return 0.0
    diffs = [float(prices[i]) - float(prices[i - 1]) for i in range(1, len(prices))]
    n = len(diffs)
    if n == 0:
        return 0.0
    mean = sum(diffs) / n
    var = sum((d - mean) ** 2 for d in diffs) / n
    return var**0.5


def _classify(realized_vol: float, vol_threshold: float) -> str:
    return REGIME_TRENDING if realized_vol >= vol_threshold else REGIME_RANGE_BOUND


def realized_vol_gate(
    prices: Sequence[float],
    *,
    lookback_candles: int = 12,
    regime_strategy: str = REGIME_RANGE_BOUND,
    vol_threshold: float = DEFAULT_VOL_THRESHOLD,
    asset: Optional[str] = None,  # noqa: ARG001  (logging-only metadata)
    timeframe: Optional[str] = None,  # noqa: ARG001  (logging-only metadata)
) -> RegimeDecision:
    """Decide whether the current realized-vol regime matches the strategy.

    This is a *precondition* to position sizing — call it before
    `simmer_sdk.sizing.size_position`. It does not haircut size; it returns
    a binary allow/skip. For the per-trade Kelly haircut driven by edge
    confidence, see SIM-1012's empirical-Kelly path.

    Args:
        prices: Sequence of close prices, oldest first. Use the last
            `lookback_candles` of your candle series; if you pass more,
            the gate uses the most-recent N. The primitive does NOT fetch
            candles itself — pass whatever your venue exposes.
        lookback_candles: Window length for the realized-vol estimate.
            Default 12 (Stacy's value). Fail-closed when fewer prices
            are supplied.
        regime_strategy: Which regime the calling strategy is registered
            for. One of `"range_bound"` (default) or `"trending"`.
        vol_threshold: Boundary between regimes. realized_vol >= threshold
            classifies as trending; below classifies as range_bound.
            Operators MUST tune this per asset / timeframe — the default
            (0.02) is a starting point, not a recommendation.
        asset: Optional asset identifier, accepted for logging / telemetry
            only. The gate's behaviour does not depend on it (venue-agnostic).
        timeframe: Optional timeframe label (e.g. "1m", "5m"), accepted for
            logging / telemetry only.

    Returns:
        A RegimeDecision. Inspect `.allowed` for the binary gate result;
        `.realized_vol` and `.regime` are useful for telemetry.

    Examples:
        Range-bound strategy in a quiet market (allowed):

        >>> prices = [0.50, 0.51, 0.50, 0.49, 0.50, 0.51,
        ...           0.50, 0.49, 0.50, 0.51, 0.50, 0.50]
        >>> d = realized_vol_gate(prices, vol_threshold=0.02)
        >>> d.allowed, d.regime
        (True, 'range_bound')

        Range-bound strategy in a trending market (blocked):

        >>> prices = [0.30, 0.34, 0.39, 0.43, 0.47, 0.51,
        ...           0.55, 0.59, 0.63, 0.67, 0.71, 0.75]
        >>> d = realized_vol_gate(prices, vol_threshold=0.02)
        >>> d.allowed, d.regime
        (False, 'trending')

        Insufficient data (fail-closed):

        >>> d = realized_vol_gate([0.5, 0.5, 0.5], lookback_candles=12)
        >>> d.allowed, d.reason
        (False, 'insufficient_data')
    """
    if regime_strategy not in _VALID_REGIMES:
        raise ValueError(
            f"regime_strategy must be one of {_VALID_REGIMES}, got {regime_strategy!r}"
        )
    if lookback_candles < 2:
        raise ValueError(f"lookback_candles must be >= 2, got {lookback_candles}")
    if vol_threshold < 0:
        raise ValueError(f"vol_threshold must be >= 0, got {vol_threshold}")

    # Reject non-finite / non-positive inputs deterministically.
    coerced: list[float] = []
    for p in prices:
        try:
            v = float(p)
        except (TypeError, ValueError):
            return RegimeDecision(
                allowed=False,
                realized_vol=0.0,
                regime=REGIME_RANGE_BOUND,
                reason="invalid_input",
                n_candles=0,
            )
        # NaN check (NaN != NaN) and finite check.
        if v != v or v in (float("inf"), float("-inf")):
            return RegimeDecision(
                allowed=False,
                realized_vol=0.0,
                regime=REGIME_RANGE_BOUND,
                reason="invalid_input",
                n_candles=0,
            )
        coerced.append(v)

    # Fail-closed if we cannot fill the lookback window.
    if len(coerced) < lookback_candles:
        return RegimeDecision(
            allowed=False,
            realized_vol=0.0,
            regime=REGIME_RANGE_BOUND,
            reason="insufficient_data",
            n_candles=len(coerced),
        )

    # Use the most-recent `lookback_candles` prices.
    window = coerced[-lookback_candles:]
    rv = realized_volatility(window)
    regime = _classify(rv, vol_threshold)

    if regime == regime_strategy:
        return RegimeDecision(
            allowed=True,
            realized_vol=rv,
            regime=regime,
            reason="ok",
            n_candles=len(window),
        )
    return RegimeDecision(
        allowed=False,
        realized_vol=rv,
        regime=regime,
        reason="regime_mismatch",
        n_candles=len(window),
    )


# Config schema skills can merge into their CONFIG_SCHEMA so operators can
# tune the gate via config.json or environment variables without code edits.
#
# Usage in a skill:
#     from simmer_sdk.regime import REGIME_CONFIG_SCHEMA
#     CONFIG_SCHEMA = {
#         **REGIME_CONFIG_SCHEMA,
#         "my_skill_param": {...},
#     }
REGIME_CONFIG_SCHEMA = {
    "regime_gate_enabled": {
        "env": "SIMMER_REGIME_GATE_ENABLED",
        "default": False,
        "type": bool,
    },
    "regime_strategy": {
        "env": "SIMMER_REGIME_STRATEGY",
        "default": REGIME_RANGE_BOUND,
        "type": str,
    },
    "regime_lookback_candles": {
        "env": "SIMMER_REGIME_LOOKBACK_CANDLES",
        "default": 12,
        "type": int,
    },
    "regime_vol_threshold": {
        "env": "SIMMER_REGIME_VOL_THRESHOLD",
        "default": DEFAULT_VOL_THRESHOLD,
        "type": float,
    },
}
