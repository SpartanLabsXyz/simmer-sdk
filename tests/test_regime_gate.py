"""Tests for simmer_sdk.regime — realized-vol regime gate."""

import math

import pytest

from simmer_sdk.regime import (
    DEFAULT_VOL_THRESHOLD,
    REGIME_CONFIG_SCHEMA,
    REGIME_RANGE_BOUND,
    REGIME_TRENDING,
    RegimeDecision,
    realized_volatility,
    realized_vol_gate,
)


# Canned 12-candle series used across the three required classification paths.
RANGE_BOUND_PRICES = [
    0.50, 0.51, 0.50, 0.49, 0.50, 0.51,
    0.50, 0.49, 0.50, 0.51, 0.50, 0.50,
]

# Linear up-trend, ~0.04 step per candle. Realized vol of diffs ~ 0 (constant
# diffs => stdev 0), so we use a noisier trending series below for the gate
# threshold check. Keep this for the "stdev = 0 means range_bound" edge case.
SMOOTH_TRENDING_PRICES = [
    0.30, 0.34, 0.38, 0.42, 0.46, 0.50,
    0.54, 0.58, 0.62, 0.66, 0.70, 0.74,
]

# Trending with realistic noise — diffs vary in magnitude and direction but
# net direction is up. stdev of diffs is well above the default threshold.
TRENDING_PRICES = [
    0.30, 0.36, 0.33, 0.41, 0.38, 0.47,
    0.44, 0.53, 0.50, 0.59, 0.56, 0.65,
]


# --- realized_volatility ---


def test_realized_vol_constant_prices_is_zero():
    assert realized_volatility([0.5] * 12) == 0.0


def test_realized_vol_empty_is_zero():
    assert realized_volatility([]) == 0.0


def test_realized_vol_single_price_is_zero():
    assert realized_volatility([0.5]) == 0.0


def test_realized_vol_known_series():
    # diffs: [0.1, -0.1] -> mean 0, var = (0.01 + 0.01) / 2 = 0.01, std = 0.1
    rv = realized_volatility([0.5, 0.6, 0.5])
    assert rv == pytest.approx(0.1, rel=1e-6)


def test_realized_vol_finite_for_realistic_input():
    rv = realized_volatility(RANGE_BOUND_PRICES)
    assert math.isfinite(rv)
    assert rv > 0  # non-zero oscillation


# --- realized_vol_gate: required classification paths ---


def test_gate_range_bound_strategy_in_range_bound_market_allowed():
    """Range-bound strategy + low realized vol -> allowed."""
    decision = realized_vol_gate(
        RANGE_BOUND_PRICES,
        lookback_candles=12,
        regime_strategy=REGIME_RANGE_BOUND,
        vol_threshold=DEFAULT_VOL_THRESHOLD,
    )
    assert isinstance(decision, RegimeDecision)
    assert decision.allowed is True
    assert decision.regime == REGIME_RANGE_BOUND
    assert decision.reason == "ok"
    assert decision.n_candles == 12
    assert decision.realized_vol < DEFAULT_VOL_THRESHOLD


def test_gate_range_bound_strategy_in_trending_market_blocked():
    """Range-bound strategy + high realized vol -> blocked (trending)."""
    decision = realized_vol_gate(
        TRENDING_PRICES,
        lookback_candles=12,
        regime_strategy=REGIME_RANGE_BOUND,
        vol_threshold=DEFAULT_VOL_THRESHOLD,
    )
    assert decision.allowed is False
    assert decision.regime == REGIME_TRENDING
    assert decision.reason == "regime_mismatch"
    assert decision.realized_vol >= DEFAULT_VOL_THRESHOLD


def test_gate_insufficient_lookback_fails_closed():
    """Fewer prices than lookback_candles -> allowed=False, reason=insufficient_data."""
    decision = realized_vol_gate(
        [0.5, 0.5, 0.5],
        lookback_candles=12,
        regime_strategy=REGIME_RANGE_BOUND,
    )
    assert decision.allowed is False
    assert decision.reason == "insufficient_data"
    assert decision.n_candles == 3
    # realized_vol is unset (0.0) when we cannot estimate it.
    assert decision.realized_vol == 0.0


# --- realized_vol_gate: trending-strategy mirror cases ---


def test_gate_trending_strategy_in_trending_market_allowed():
    decision = realized_vol_gate(
        TRENDING_PRICES,
        regime_strategy=REGIME_TRENDING,
        vol_threshold=DEFAULT_VOL_THRESHOLD,
    )
    assert decision.allowed is True
    assert decision.regime == REGIME_TRENDING
    assert decision.reason == "ok"


def test_gate_trending_strategy_in_range_bound_market_blocked():
    decision = realized_vol_gate(
        RANGE_BOUND_PRICES,
        regime_strategy=REGIME_TRENDING,
        vol_threshold=DEFAULT_VOL_THRESHOLD,
    )
    assert decision.allowed is False
    assert decision.regime == REGIME_RANGE_BOUND
    assert decision.reason == "regime_mismatch"


# --- realized_vol_gate: window selection + boundary behaviour ---


def test_gate_uses_most_recent_n_candles():
    """When more prices supplied than lookback, uses the most-recent window."""
    # Long history of trending data, then a quiet tail of length 12.
    history = TRENDING_PRICES + RANGE_BOUND_PRICES
    decision = realized_vol_gate(
        history,
        lookback_candles=12,
        regime_strategy=REGIME_RANGE_BOUND,
        vol_threshold=DEFAULT_VOL_THRESHOLD,
    )
    # The trailing 12 candles are the calm RANGE_BOUND_PRICES.
    assert decision.allowed is True
    assert decision.n_candles == 12


def test_gate_constant_prices_classified_as_range_bound():
    """Realized_vol = 0 < threshold -> range_bound."""
    decision = realized_vol_gate(
        [0.5] * 12,
        regime_strategy=REGIME_RANGE_BOUND,
        vol_threshold=DEFAULT_VOL_THRESHOLD,
    )
    assert decision.allowed is True
    assert decision.realized_vol == 0.0
    assert decision.regime == REGIME_RANGE_BOUND


def test_gate_smooth_trend_with_constant_diffs_is_range_bound():
    """A perfectly linear trend has stdev(diffs) = 0, so it's range_bound by
    this definition. Documents that 'trending' here means *volatile*, not
    *directional* — choose threshold and series accordingly when configuring."""
    decision = realized_vol_gate(
        SMOOTH_TRENDING_PRICES,
        regime_strategy=REGIME_RANGE_BOUND,
        vol_threshold=DEFAULT_VOL_THRESHOLD,
    )
    assert decision.realized_vol == pytest.approx(0.0, abs=1e-9)
    assert decision.regime == REGIME_RANGE_BOUND
    assert decision.allowed is True


def test_gate_threshold_at_realized_vol_classifies_trending():
    """realized_vol == threshold -> trending (>= boundary)."""
    rv = realized_volatility(RANGE_BOUND_PRICES)
    decision = realized_vol_gate(
        RANGE_BOUND_PRICES,
        regime_strategy=REGIME_TRENDING,
        vol_threshold=rv,
    )
    assert decision.regime == REGIME_TRENDING
    assert decision.allowed is True


# --- realized_vol_gate: input validation ---


def test_gate_rejects_invalid_regime_strategy():
    with pytest.raises(ValueError, match="regime_strategy"):
        realized_vol_gate([0.5] * 12, regime_strategy="sideways")


def test_gate_rejects_lookback_below_two():
    with pytest.raises(ValueError, match="lookback_candles"):
        realized_vol_gate([0.5] * 12, lookback_candles=1)


def test_gate_rejects_negative_threshold():
    with pytest.raises(ValueError, match="vol_threshold"):
        realized_vol_gate([0.5] * 12, vol_threshold=-0.01)


def test_gate_rejects_nan_price():
    decision = realized_vol_gate(
        [0.5, 0.5, float("nan")] + [0.5] * 9,
        regime_strategy=REGIME_RANGE_BOUND,
    )
    assert decision.allowed is False
    assert decision.reason == "invalid_input"


def test_gate_rejects_inf_price():
    decision = realized_vol_gate(
        [0.5] * 11 + [float("inf")],
        regime_strategy=REGIME_RANGE_BOUND,
    )
    assert decision.allowed is False
    assert decision.reason == "invalid_input"


def test_gate_rejects_non_numeric_price():
    decision = realized_vol_gate(
        [0.5] * 11 + ["not a number"],  # type: ignore[list-item]
        regime_strategy=REGIME_RANGE_BOUND,
    )
    assert decision.allowed is False
    assert decision.reason == "invalid_input"


def test_gate_accepts_asset_and_timeframe_metadata():
    """asset/timeframe are logging-only — must not affect the decision."""
    a = realized_vol_gate(RANGE_BOUND_PRICES, asset="BTC-USD", timeframe="1m")
    b = realized_vol_gate(RANGE_BOUND_PRICES)
    assert a.allowed == b.allowed
    assert a.realized_vol == pytest.approx(b.realized_vol)
    assert a.regime == b.regime


# --- determinism ---


def test_gate_is_deterministic():
    """Same input -> same decision, every call."""
    a = realized_vol_gate(TRENDING_PRICES, regime_strategy=REGIME_RANGE_BOUND)
    b = realized_vol_gate(TRENDING_PRICES, regime_strategy=REGIME_RANGE_BOUND)
    assert a == b


# --- REGIME_CONFIG_SCHEMA ---


def test_config_schema_has_required_keys():
    for key in (
        "regime_gate_enabled",
        "regime_strategy",
        "regime_lookback_candles",
        "regime_vol_threshold",
    ):
        assert key in REGIME_CONFIG_SCHEMA


def test_config_schema_defaults_match_primitive_defaults():
    assert REGIME_CONFIG_SCHEMA["regime_lookback_candles"]["default"] == 12
    assert REGIME_CONFIG_SCHEMA["regime_strategy"]["default"] == REGIME_RANGE_BOUND
    assert (
        REGIME_CONFIG_SCHEMA["regime_vol_threshold"]["default"]
        == DEFAULT_VOL_THRESHOLD
    )
    # Off-by-default: skills must opt in explicitly.
    assert REGIME_CONFIG_SCHEMA["regime_gate_enabled"]["default"] is False
