"""
Regime-detection primitives for Simmer SDK skills.

A *regime gate* is a pre-sizing precondition: before a strategy decides how
much to bet, it asks "is the current market regime compatible with my edge?"
If no, the skill skips this opportunity entirely. This is distinct from the
empirical-Kelly haircut in `simmer_sdk.sizing`, which only scales position
size — a gate is trade / no-trade.

Stacy (stacyonchain) on Polymarket: a 1¢-reversal strategy that worked in
range-bound markets bled in trending markets. Gating on realized volatility
across the prior 12 candles meaningfully improved overall results — it
turned trending periods from losses into no-ops.

Example:
    from simmer_sdk.regime import realized_vol_gate

    candles = my_venue.get_recent_candles(market_id, timeframe="1m", limit=12)
    prices = [c["close"] for c in candles]

    decision = realized_vol_gate(
        prices,
        lookback_candles=12,
        regime_strategy="range_bound",
        vol_threshold=0.02,
    )
    if not decision.allowed:
        # log and skip — don't size up in the wrong regime
        return

    # ... proceed to size_position(...)

The primitive is venue-agnostic: skills fetch their own candle series and
pass a list/sequence of close prices. `asset` / `timeframe` are accepted as
optional metadata for logging only.
"""

from .gate import (
    DEFAULT_VOL_THRESHOLD,
    REGIME_CONFIG_SCHEMA,
    REGIME_RANGE_BOUND,
    REGIME_TRENDING,
    RegimeDecision,
    realized_volatility,
    realized_vol_gate,
)

__all__ = [
    "DEFAULT_VOL_THRESHOLD",
    "REGIME_CONFIG_SCHEMA",
    "REGIME_RANGE_BOUND",
    "REGIME_TRENDING",
    "RegimeDecision",
    "realized_volatility",
    "realized_vol_gate",
]
