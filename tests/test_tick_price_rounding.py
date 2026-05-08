"""Tests for price tick-grid rounding (SIM-1666).

Validates that round_price_to_tick() correctly quantises raw computed prices
to the market's tick_size before the CLOB sees them.
"""

from decimal import Decimal
import pytest

from simmer_sdk.signing import round_price_to_tick


# ---------------------------------------------------------------------------
# Basic rounding — each supported tick size
# ---------------------------------------------------------------------------

def test_tick_001_rounds_to_nearest():
    # 0.9690009... → nearest 0.001 is 0.969
    result = round_price_to_tick(0.9690009744043256, 0.001)
    assert result == pytest.approx(0.969, abs=1e-9)


def test_tick_001_rounds_up():
    # 0.9695 → rounds up to 0.970 (ROUND_HALF_UP)
    result = round_price_to_tick(0.9695, 0.001)
    assert result == pytest.approx(0.970, abs=1e-9)


def test_tick_001_rounds_to_nearest_high_side():
    # 0.9700160467... → nearest 0.001 is 0.970
    result = round_price_to_tick(0.9700160467338586, 0.001)
    assert result == pytest.approx(0.970, abs=1e-9)


def test_tick_001_rounds_price_on_grid_unchanged():
    # Already on tick — must pass through exactly
    result = round_price_to_tick(0.969, 0.001)
    assert result == pytest.approx(0.969, abs=1e-9)


def test_tick_001_rounds_error_examples_from_ticket():
    # Exact error examples from rjreyes/weather-trader999 log 2026-05-07
    assert round_price_to_tick(0.9690009744043256, 0.001) == pytest.approx(0.969, abs=1e-9)
    assert round_price_to_tick(0.8960009211897021, 0.001) == pytest.approx(0.896, abs=1e-9)


def test_tick_001_error_examples_01_tick():
    # tick=0.01 examples from same log
    assert round_price_to_tick(0.9700160467338586, 0.01) == pytest.approx(0.97, abs=1e-9)
    assert round_price_to_tick(0.9200079217903246, 0.01) == pytest.approx(0.92, abs=1e-9)
    assert round_price_to_tick(0.8600061171983232, 0.01) == pytest.approx(0.86, abs=1e-9)
    assert round_price_to_tick(0.7700039487381987, 0.01) == pytest.approx(0.77, abs=1e-9)


def test_tick_0001_passes_through_full_precision():
    # tick=0.0001 → 4dp; a 4dp price is on grid
    result = round_price_to_tick(0.4809, 0.0001)
    assert result == pytest.approx(0.4809, abs=1e-9)


def test_tick_0001_rounds_5dp_to_4dp():
    result = round_price_to_tick(0.48091, 0.0001)
    assert result == pytest.approx(0.4809, abs=1e-9)


def test_tick_01_rounds_to_nearest():
    result = round_price_to_tick(0.456789, 0.1)
    assert result == pytest.approx(0.5, abs=1e-9)


def test_tick_01_rounds_down():
    result = round_price_to_tick(0.44, 0.1)
    assert result == pytest.approx(0.4, abs=1e-9)


# ---------------------------------------------------------------------------
# Round-trip: on-grid prices must be stable (idempotent)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tick,price", [
    (0.01, 0.50),
    (0.01, 0.97),
    (0.001, 0.969),
    (0.001, 0.970),
    (0.0001, 0.4809),
    (0.1, 0.3),
])
def test_on_grid_price_unchanged(tick, price):
    result = round_price_to_tick(price, tick)
    assert Decimal(str(result)) % Decimal(str(tick)) == 0, (
        f"round_price_to_tick({price}, {tick}) = {result} is not on tick grid"
    )
    assert result == pytest.approx(price, abs=1e-9)


# ---------------------------------------------------------------------------
# Result is always on the tick grid
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tick,price", [
    (0.01, 0.9700160467338586),
    (0.01, 0.9200079217903246),
    (0.001, 0.9690009744043256),
    (0.001, 0.8960009211897021),
    (0.0001, 0.48091234),
])
def test_result_is_on_grid(tick, price):
    result = round_price_to_tick(price, tick)
    remainder = Decimal(str(result)) % Decimal(str(tick))
    assert remainder == 0, (
        f"round_price_to_tick({price}, {tick}) = {result} remainder {remainder} ≠ 0"
    )
