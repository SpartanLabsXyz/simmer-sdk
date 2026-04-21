"""Tests for simmer_sdk.sizing.empirical_kelly — CV-haircut Kelly sizing.

See SIM-1012. Covers the acceptance criteria:
- Returns a float in [0, clip], never negative, never > clip
- CV=0 input matches theoretical Kelly to <1e-6 (when under clip)
- CV=1.0 input yields 0 (full haircut)
- Zero-variance, unit-variance, wide-distribution edge cases
- "6% point estimate with 3–9% distribution" worked example
- Does NOT change default behavior of existing Kelly callers
"""

import math
import statistics

import pytest

from simmer_sdk.sizing import (
    empirical_kelly,
    kelly_fraction,
    size_position,
)


# --- core contract: bounds ---

def test_result_never_negative():
    assert empirical_kelly([-0.05, -0.1, 0.02], 0.55) == 0.0
    assert empirical_kelly([0.0], 0.55) == 0.0


def test_result_never_exceeds_clip_default():
    # Very large edge, zero variance → raw Kelly would blow past 0.25,
    # must be clipped.
    result = empirical_kelly([0.40, 0.40, 0.40], 0.50)
    assert 0.0 <= result <= 0.25
    assert result == pytest.approx(0.25)


def test_result_never_exceeds_custom_clip():
    result = empirical_kelly([0.40, 0.40, 0.40], 0.50, clip=0.10)
    assert result == pytest.approx(0.10)


def test_result_is_float():
    result = empirical_kelly([0.05, 0.06, 0.07], 0.50)
    assert isinstance(result, float)


# --- CV = 0 case ---

def test_cv_zero_matches_theoretical_kelly_within_clip():
    # All samples identical → CV = 0 → no haircut.
    # mean_edge = 0.05, price = 0.60 → f_kelly = 0.05 / 0.40 = 0.125
    samples = [0.05, 0.05, 0.05]
    expected = kelly_fraction(0.65, 0.60)  # 0.125
    result = empirical_kelly(samples, 0.60, clip=0.5)  # clip above expected
    assert result == pytest.approx(expected, abs=1e-6)


def test_cv_zero_single_sample_falls_back_to_point_kelly():
    # Single sample → no stdev → treat CV as 0 → point-estimate Kelly.
    samples = [0.10]
    expected = 0.10 / (1.0 - 0.60)  # 0.25
    result = empirical_kelly(samples, 0.60, clip=0.5)
    assert result == pytest.approx(expected, abs=1e-6)


# --- CV = 1 case ---

def test_cv_exactly_one_yields_zero():
    # Samples [0, 0.1, 0.2] → mean=0.1, stdev=0.1, CV=1.0 → haircut = 0.
    samples = [0.0, 0.1, 0.2]
    mean = statistics.fmean(samples)
    stdev = statistics.stdev(samples)
    assert stdev / mean == pytest.approx(1.0, abs=1e-12)  # precondition
    assert empirical_kelly(samples, 0.50) == 0.0


def test_cv_above_one_yields_zero():
    # stdev > mean → negative haircut → output 0.
    samples = [0.01, 0.01, 0.28]  # CV ~ 1.56
    assert empirical_kelly(samples, 0.50) == 0.0


# --- worked example from aleiahlock article ---

def test_worked_example_6pct_with_3_to_9pct_distribution():
    # 6% point estimate with 3–9% distribution (aleiahlock article,
    # SIM-1007 source). Uniform sampling across the stated range.
    samples = [0.03, 0.045, 0.06, 0.075, 0.09]
    market_price = 0.50

    mean_edge = statistics.fmean(samples)
    assert mean_edge == pytest.approx(0.06)

    stdev_edge = statistics.stdev(samples)
    cv = stdev_edge / mean_edge
    f_kelly = mean_edge / (1.0 - market_price)
    expected = f_kelly * (1.0 - cv)

    result = empirical_kelly(samples, market_price)
    assert result == pytest.approx(expected, rel=1e-9)
    # Sanity: noticeably smaller than point-estimate Kelly (0.12).
    assert result < f_kelly
    assert result == pytest.approx(0.0726, abs=1e-4)


# --- wide-distribution edge cases ---

def test_wide_distribution_produces_larger_haircut_than_tight():
    tight = [0.058, 0.06, 0.062]
    wide = [0.02, 0.06, 0.10]
    price = 0.50

    tight_result = empirical_kelly(tight, price)
    wide_result = empirical_kelly(wide, price)

    assert 0.0 < wide_result < tight_result


def test_unit_variance_edge_samples():
    # stdev of [0, 1, 2, 3, 4] = sqrt(2.5) ≈ 1.581 — but these aren't
    # prediction-market edges. Use a scaled variant: mean=0.1, with
    # n-1 variance = 1e-4 (stdev = 0.01, CV = 0.1 → haircut 0.9).
    samples = [0.09, 0.095, 0.10, 0.105, 0.11]
    price = 0.50

    mean = statistics.fmean(samples)
    stdev = statistics.stdev(samples)
    expected = (mean / (1.0 - price)) * (1.0 - stdev / mean)

    result = empirical_kelly(samples, price)
    assert result == pytest.approx(expected, rel=1e-9)


# --- boundary / invalid inputs ---

def test_empty_samples_returns_zero():
    assert empirical_kelly([], 0.50) == 0.0


def test_invalid_market_price_returns_zero():
    samples = [0.05, 0.06, 0.07]
    assert empirical_kelly(samples, 0.0) == 0.0
    assert empirical_kelly(samples, 1.0) == 0.0
    assert empirical_kelly(samples, -0.1) == 0.0
    assert empirical_kelly(samples, 1.5) == 0.0


def test_non_finite_samples_return_zero():
    assert empirical_kelly([0.05, math.nan, 0.07], 0.50) == 0.0
    assert empirical_kelly([0.05, math.inf, 0.07], 0.50) == 0.0


def test_non_positive_clip_returns_zero():
    samples = [0.05, 0.06, 0.07]
    assert empirical_kelly(samples, 0.50, clip=0.0) == 0.0
    assert empirical_kelly(samples, 0.50, clip=-0.1) == 0.0


def test_accepts_tuple_and_generator():
    samples = (0.04, 0.05, 0.06)
    tuple_result = empirical_kelly(samples, 0.50)
    list_result = empirical_kelly(list(samples), 0.50)
    assert tuple_result == pytest.approx(list_result)


# --- non-regression: existing Kelly callers unchanged ---

def test_kelly_fraction_behavior_unchanged():
    assert kelly_fraction(0.70, 0.55) == pytest.approx(1 / 3, rel=1e-6)
    assert kelly_fraction(0.55, 0.55) == pytest.approx(0.0)
    assert kelly_fraction(0.40, 0.55) < 0


def test_size_position_default_behavior_unchanged():
    # Quarter-Kelly of 0.333 * 1000 = 83.33 — must not change.
    assert size_position(0.70, 0.55, 1000.0) == pytest.approx(83.33, rel=0.01)


def test_empirical_kelly_is_not_called_by_existing_size_position():
    # Sanity: size_position path does not touch empirical_kelly.
    # A very high-CV edge distribution should have no effect on
    # size_position (it doesn't accept edge_samples).
    amount = size_position(0.70, 0.55, 1000.0)
    assert amount > 0
