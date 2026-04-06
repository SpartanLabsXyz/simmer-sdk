"""Tests for simmer_sdk.sizing — Kelly Criterion + EV position sizing."""

import pytest
from simmer_sdk.sizing import expected_value, kelly_fraction, size_position, SIZING_CONFIG_SCHEMA


# --- expected_value ---

def test_ev_positive_edge():
    assert expected_value(0.70, 0.55) == pytest.approx(0.15)

def test_ev_zero_edge():
    assert expected_value(0.55, 0.55) == pytest.approx(0.0)

def test_ev_negative_edge():
    assert expected_value(0.40, 0.55) == pytest.approx(-0.15)


# --- kelly_fraction ---

def test_kelly_positive_edge():
    # p=0.70, c=0.55 -> (0.70 - 0.55) / (1 - 0.55) = 0.15 / 0.45 = 0.333...
    assert kelly_fraction(0.70, 0.55) == pytest.approx(1 / 3, rel=1e-6)

def test_kelly_no_edge():
    assert kelly_fraction(0.55, 0.55) == pytest.approx(0.0)

def test_kelly_negative_edge():
    f = kelly_fraction(0.40, 0.55)
    assert f < 0  # Should not bet

def test_kelly_extreme_edge():
    # p=0.95, c=0.50 -> (0.95 - 0.50) / 0.50 = 0.90
    assert kelly_fraction(0.95, 0.50) == pytest.approx(0.90)

def test_kelly_boundary_price_zero():
    assert kelly_fraction(0.50, 0.0) == 0.0

def test_kelly_boundary_price_one():
    assert kelly_fraction(0.50, 1.0) == 0.0


# --- size_position ---

def test_size_fractional_kelly_default():
    # p=0.70, c=0.55, bankroll=1000
    # kelly = 0.333, fractional (0.25x) = 0.0833, amount = 83.33
    amount = size_position(0.70, 0.55, 1000.0)
    assert amount == pytest.approx(83.33, rel=0.01)

def test_size_full_kelly():
    amount = size_position(0.70, 0.55, 1000.0, method="kelly")
    assert amount == pytest.approx(333.33, rel=0.01)

def test_size_fixed():
    amount = size_position(0.70, 0.55, 1000.0, method="fixed", kelly_multiplier=0.10)
    assert amount == pytest.approx(100.0)

def test_size_skips_negative_ev():
    amount = size_position(0.40, 0.55, 1000.0)
    assert amount == 0.0

def test_size_min_ev_filter():
    # Edge = 0.01 (p=0.56, c=0.55), below min_ev=0.05
    amount = size_position(0.56, 0.55, 1000.0, min_ev=0.05)
    assert amount == 0.0

def test_size_min_ev_passes():
    # Edge = 0.15 (p=0.70, c=0.55), above min_ev=0.05
    amount = size_position(0.70, 0.55, 1000.0, min_ev=0.05)
    assert amount > 0

def test_size_max_fraction_cap():
    # Very high edge should be capped at max_fraction
    amount = size_position(0.99, 0.10, 1000.0, method="kelly", max_fraction=0.50)
    # Kelly would be (0.99-0.10)/0.90 = 0.988, capped to 0.50
    assert amount == pytest.approx(500.0)

def test_size_zero_bankroll():
    assert size_position(0.70, 0.55, 0.0) == 0.0

def test_size_negative_bankroll():
    assert size_position(0.70, 0.55, -100.0) == 0.0

def test_size_invalid_probability():
    assert size_position(0.0, 0.55, 1000.0) == 0.0
    assert size_position(1.0, 0.55, 1000.0) == 0.0

def test_size_invalid_price():
    assert size_position(0.70, 0.0, 1000.0) == 0.0
    assert size_position(0.70, 1.0, 1000.0) == 0.0

def test_size_custom_kelly_multiplier():
    # Half-Kelly (0.5x)
    half = size_position(0.70, 0.55, 1000.0, kelly_multiplier=0.5)
    quarter = size_position(0.70, 0.55, 1000.0, kelly_multiplier=0.25)
    assert half == pytest.approx(quarter * 2, rel=0.01)


# --- SIZING_CONFIG_SCHEMA ---

def test_config_schema_has_required_keys():
    assert "position_sizing" in SIZING_CONFIG_SCHEMA
    assert "kelly_multiplier" in SIZING_CONFIG_SCHEMA
    assert "min_ev" in SIZING_CONFIG_SCHEMA

def test_config_schema_defaults():
    assert SIZING_CONFIG_SCHEMA["position_sizing"]["default"] == "fractional_kelly"
    assert SIZING_CONFIG_SCHEMA["kelly_multiplier"]["default"] == 0.25
    assert SIZING_CONFIG_SCHEMA["min_ev"]["default"] == 0.0


# --- NO side helper pattern ---

def test_no_side_sizing():
    """Verify the documented pattern for NO-side trades:
    pass (1 - yes_price) as market_price and (1 - p_yes) as p_win."""
    p_yes = 0.30  # We think YES is unlikely
    yes_price = 0.55  # Market overprices YES

    # NO side: we think NO = 70%, NO costs 45 cents
    amount = size_position(1 - p_yes, 1 - yes_price, 1000.0)
    assert amount > 0  # Should want to buy NO
    # Kelly: (0.70 - 0.45) / (1 - 0.45) = 0.25 / 0.55 = 0.4545
    # Quarter-Kelly: 0.4545 * 0.25 * 1000 = 113.6
    assert amount == pytest.approx(113.6, rel=0.02)
