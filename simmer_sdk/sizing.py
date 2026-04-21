"""
Position sizing utilities for Simmer SDK skills.

Provides Kelly Criterion and Expected Value calculations optimized for
binary prediction markets. Based on patterns from top Polymarket traders.

Usage:
    from simmer_sdk.sizing import size_position, kelly_fraction, expected_value

    # Calculate optimal position size
    amount = size_position(
        p_win=0.70,          # Your estimated probability
        market_price=0.55,   # Current YES price
        bankroll=1000.0,     # Available capital
    )
    # amount = ~$85 (fractional Kelly, 0.25x by default)

    # Skip low-EV trades automatically
    amount = size_position(p_win=0.56, market_price=0.55, bankroll=1000, min_ev=0.05)
    # amount = 0.0 (edge of 0.01 < min_ev of 0.05, trade skipped)

Sizing methods:
    - "fractional_kelly" (default): Kelly fraction * multiplier (default 0.25x).
      Prevents overbetting while still scaling with edge. Recommended.
    - "kelly": Full Kelly criterion. Mathematically optimal for long-run growth
      but volatile — a single bad estimate can cause large drawdowns.
    - "fixed": Fixed fraction of bankroll (uses kelly_multiplier as the fraction).
      Simple but ignores edge magnitude.
"""

from typing import Optional, Sequence
import math
import statistics


def expected_value(p_win: float, market_price: float) -> float:
    """Calculate expected value (edge) per share in a binary prediction market.

    EV = p_win - market_price

    A positive EV means the market is underpriced relative to your belief.
    For example, if you believe YES has a 70% chance but the market prices
    it at 55%, your edge is +0.15 per share.

    Args:
        p_win: Your estimated probability of the outcome (0-1).
        market_price: Current market price / cost per YES share (0-1).

    Returns:
        Edge per share. Positive = favorable, negative = unfavorable.
    """
    return p_win - market_price


def kelly_fraction(p_win: float, market_price: float) -> float:
    """Calculate the Kelly Criterion bet fraction for a binary prediction market.

    For buying YES at price `c` with estimated win probability `p`:
        f* = (p - c) / (1 - c)

    This is the fraction of bankroll that maximizes long-run growth rate.

    Args:
        p_win: Your estimated probability of the outcome (0-1).
        market_price: Current market price / cost per YES share (0-1).
            Must be between 0 (exclusive) and 1 (exclusive).

    Returns:
        Optimal fraction of bankroll to wager. Negative values mean the
        bet is unfavorable (don't take it). Values > 1 are theoretically
        possible but should be capped in practice.

    Example:
        >>> kelly_fraction(0.70, 0.55)
        0.333...  # Bet 33% of bankroll
        >>> kelly_fraction(0.50, 0.55)
        -0.111... # Negative = don't bet
    """
    if market_price >= 1.0 or market_price <= 0.0:
        return 0.0
    return (p_win - market_price) / (1.0 - market_price)


def size_position(
    p_win: float,
    market_price: float,
    bankroll: float,
    method: str = "fractional_kelly",
    kelly_multiplier: float = 0.25,
    min_ev: float = 0.0,
    max_fraction: float = 0.95,
) -> float:
    """Calculate dollar amount to trade based on edge and bankroll.

    Combines EV filtering (skip low-edge trades) with Kelly-based position
    sizing (bet more when edge is larger). Default is quarter-Kelly (0.25x)
    which balances growth with drawdown protection.

    Args:
        p_win: Your estimated probability of the outcome (0-1).
        market_price: Current market price / cost per share (0-1).
            For YES trades, this is the YES price.
            For NO trades, pass (1 - yes_price) as market_price
            and (1 - p_yes) as p_win.
        bankroll: Available capital (in dollars or $SIM).
        method: Position sizing method:
            - "fractional_kelly" (default): Kelly * kelly_multiplier.
            - "kelly": Full Kelly criterion (aggressive).
            - "fixed": Fixed fraction (kelly_multiplier used as fraction).
        kelly_multiplier: Fraction of Kelly to use (default 0.25 = quarter Kelly).
            For "fixed" method, this is the fixed allocation fraction.
        min_ev: Minimum expected value (edge) to take the trade. Trades with
            edge <= min_ev are skipped (returns 0). Default 0 = any positive edge.
        max_fraction: Maximum fraction of bankroll per trade (default 0.95).
            Safety cap to prevent all-in bets even with large Kelly fractions.

    Returns:
        Dollar amount to trade. Returns 0.0 if trade should be skipped
        (negative EV, below min_ev threshold, or invalid inputs).

    Example:
        >>> size_position(0.70, 0.55, 1000.0)
        83.33  # Quarter-Kelly: 0.333 * 0.25 * 1000

        >>> size_position(0.70, 0.55, 1000.0, method="kelly")
        333.33  # Full Kelly

        >>> size_position(0.70, 0.55, 1000.0, method="fixed", kelly_multiplier=0.10)
        100.0  # Fixed 10% of bankroll

        >>> size_position(0.56, 0.55, 1000.0, min_ev=0.05)
        0.0  # Edge 0.01 < min_ev 0.05, skipped
    """
    if bankroll <= 0 or market_price <= 0 or market_price >= 1.0:
        return 0.0
    if p_win <= 0 or p_win >= 1.0:
        return 0.0

    # EV gate: skip trades below minimum edge
    ev = expected_value(p_win, market_price)
    if ev <= min_ev:
        return 0.0

    if method == "fixed":
        frac = min(kelly_multiplier, max_fraction)
        return bankroll * frac

    # Kelly-based sizing
    f = kelly_fraction(p_win, market_price)
    if f <= 0:
        return 0.0

    if method == "fractional_kelly":
        f *= kelly_multiplier

    f = min(f, max_fraction)
    return bankroll * f


def empirical_kelly(
    edge_samples: Sequence[float],
    market_price: float,
    clip: float = 0.25,
) -> float:
    """Coefficient-of-variation adjusted Kelly fraction for a binary market.

    Textbook Kelly assumes the edge is known with certainty. In practice a
    model's edge estimate is a point estimate over a distribution — the true
    edge might be half or double. Treating the point estimate as fact causes
    systematic overbetting; the standard fix is to haircut Kelly by the
    coefficient of variation (CV) of the edge distribution::

        f_empirical = f_kelly × (1 − CV_edge)
        CV_edge     = stdev(edge_samples) / mean(edge_samples)

    High model uncertainty (wide edge distribution) → aggressive haircut.
    Low uncertainty (tight distribution) → sizing approaches theoretical Kelly.

    ``edge_samples`` are typically produced by Monte Carlo resampling of a
    historical analog set (see SIM-1011 dataset-backtest-ingest skill), but
    any iterable of edge estimates (e.g. bootstrap resamples of a backtest)
    works. The function takes the raw samples so callers are not required
    to install a specific dataset.

    The Kelly conversion uses the prediction-market form consistent with
    :func:`kelly_fraction`::

        f_kelly = mean(edge_samples) / (1 − market_price)

    The result is clamped to ``[0, clip]``: negative point estimates,
    CV ≥ 1 (uncertainty swamps edge), and pathological inputs all map
    to 0. Positive values are capped at ``clip`` — an absolute cap,
    separate from the relative ``max_fraction`` used by
    :func:`size_position`. The default ``clip=0.25`` reflects the
    empirical-method stance that sizing should be conservative even when
    uncertainty is low; callers wanting a tighter cap can pass ``clip`` down.

    Args:
        edge_samples: Sequence of edge estimates (``p_win − market_price``),
            one per Monte Carlo / bootstrap resample. Must contain at least
            one sample. With a single sample ``stdev`` is undefined and CV
            is treated as 0 (falls back to point-estimate Kelly).
        market_price: Current market price / cost per YES share (0 < price < 1).
            Called ``odds`` in the source spec — in a binary prediction market
            this is the cost per share, which equals implied probability.
        clip: Absolute cap on returned fraction. Defaults to 0.25 (quarter
            of bankroll). Pass a larger value to opt out of the cap.

    Returns:
        Fraction of bankroll to wager, in ``[0, clip]``. Never negative,
        never > ``clip``. Returns 0 for invalid inputs (empty samples,
        non-positive mean edge, CV ≥ 1, invalid market_price).

    Example:
        >>> # 6% point estimate with 3–9% distribution (article worked example),
        >>> # market at 50¢. Five uniformly-spaced samples.
        >>> samples = [0.03, 0.045, 0.06, 0.075, 0.09]
        >>> round(empirical_kelly(samples, 0.50), 4)
        0.0726

        >>> # Zero variance → matches theoretical Kelly (capped at clip).
        >>> empirical_kelly([0.15, 0.15, 0.15], 0.55)  # f_kelly = 0.333, clipped
        0.25

        >>> # CV = 1 → full haircut.
        >>> round(empirical_kelly([0.0, 0.2], 0.50), 6)
        0.0
    """
    if not edge_samples:
        return 0.0
    if market_price <= 0.0 or market_price >= 1.0:
        return 0.0
    if clip <= 0.0:
        return 0.0

    samples = list(edge_samples)
    for s in samples:
        if not math.isfinite(s):
            return 0.0

    mean_edge = statistics.fmean(samples)
    if mean_edge <= 0.0:
        return 0.0

    # stdev requires n >= 2; with a single sample there is no variance
    # information, so CV defaults to 0 (use point-estimate Kelly).
    if len(samples) >= 2:
        stdev_edge = statistics.stdev(samples)
    else:
        stdev_edge = 0.0

    cv_edge = stdev_edge / mean_edge
    haircut = 1.0 - cv_edge
    # Snap floating-point epsilon around CV ≈ 1 to a hard zero so
    # "CV = 1.0 → full haircut" holds exactly.
    if haircut <= 1e-12:
        return 0.0

    f_kelly = mean_edge / (1.0 - market_price)
    f_empirical = f_kelly * haircut

    if f_empirical <= 0.0:
        return 0.0
    return min(f_empirical, clip)


# Config schema that skills can merge into their CONFIG_SCHEMA for
# position sizing settings via config.json or environment variables.
#
# Usage in a skill:
#     from simmer_sdk.sizing import SIZING_CONFIG_SCHEMA
#     CONFIG_SCHEMA = {
#         "my_skill_param": {"env": "MY_PARAM", "default": 42, "type": int},
#         **SIZING_CONFIG_SCHEMA,
#     }
SIZING_CONFIG_SCHEMA = {
    "position_sizing": {
        "env": "SIMMER_POSITION_SIZING",
        "default": "fractional_kelly",
        "type": str,
    },
    "kelly_multiplier": {
        "env": "SIMMER_KELLY_MULTIPLIER",
        "default": 0.25,
        "type": float,
    },
    "min_ev": {
        "env": "SIMMER_MIN_EV",
        "default": 0.0,
        "type": float,
    },
}
