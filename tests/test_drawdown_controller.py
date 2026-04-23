"""
Unit tests for simmer_sdk.risk.DrawdownController.

Covers: peak-climbing, drawdown computation, halt threshold (inclusive
boundary), sticky-halt behavior, operator-explicit resume, input
validation.
"""

import pytest

from simmer_sdk.risk import DrawdownController


# --- construction ---------------------------------------------------------


def test_init_sets_peak_equal_to_bankroll():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    assert dc.peak == 1000.0
    assert dc.current == 1000.0
    assert dc.halted is False
    assert dc.can_trade() is True
    assert dc.drawdown == 0.0


def test_init_default_max_drawdown_is_15_percent():
    dc = DrawdownController(bankroll=1000.0)
    assert dc.max_drawdown_pct == 0.15


def test_init_rejects_non_positive_bankroll():
    with pytest.raises(ValueError):
        DrawdownController(bankroll=0, max_drawdown_pct=0.15)
    with pytest.raises(ValueError):
        DrawdownController(bankroll=-1, max_drawdown_pct=0.15)


@pytest.mark.parametrize("bad_pct", [0, 1, -0.1, 1.5])
def test_init_rejects_out_of_range_drawdown_pct(bad_pct):
    with pytest.raises(ValueError):
        DrawdownController(bankroll=1000.0, max_drawdown_pct=bad_pct)


# --- peak climbing --------------------------------------------------------


def test_peak_climbs_on_new_high():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    dc.update(1200.0)
    assert dc.peak == 1200.0
    assert dc.drawdown == 0.0


def test_peak_does_not_drop_when_bankroll_falls():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    dc.update(1200.0)
    dc.update(1100.0)
    assert dc.peak == 1200.0


def test_peak_is_monotonic_through_mixed_sequence():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.5)
    for b in [1100, 900, 1200, 800, 1150]:
        dc.update(b)
    assert dc.peak == 1200


# --- drawdown computation -------------------------------------------------


def test_drawdown_is_fraction_of_peak():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.5)
    state = dc.update(900.0)
    assert state["drawdown"] == pytest.approx(0.10)
    assert state["halted"] is False


def test_drawdown_resets_toward_zero_on_recovery_but_halt_stays():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    dc.update(800.0)  # 20% drawdown — halts
    assert dc.halted is True
    dc.update(1000.0)  # recovered
    assert dc.drawdown == 0.0
    assert dc.halted is True  # sticky


def test_drawdown_never_negative_on_new_high():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    state = dc.update(1500.0)
    assert state["drawdown"] == 0.0


# --- halt threshold (inclusive boundary) ----------------------------------


def test_halt_triggers_exactly_at_threshold():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    state = dc.update(850.0)  # exactly 15% drawdown
    assert state["drawdown"] == pytest.approx(0.15)
    assert state["halted"] is True
    assert dc.can_trade() is False


def test_halt_does_not_trigger_just_below_threshold():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    state = dc.update(851.0)  # 14.9% drawdown
    assert state["halted"] is False
    assert dc.can_trade() is True


def test_halt_triggers_well_past_threshold():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    state = dc.update(500.0)  # 50% drawdown
    assert state["halted"] is True


def test_halt_triggers_relative_to_new_peak_not_initial_bankroll():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    dc.update(2000.0)  # peak = 2000
    state = dc.update(1700.0)  # 15% off the new peak, not off 1000
    assert state["drawdown"] == pytest.approx(0.15)
    assert state["halted"] is True


# --- sticky halt ----------------------------------------------------------


def test_halt_is_sticky_across_many_updates():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    dc.update(800.0)
    assert dc.halted is True
    for b in [900, 1000, 1100, 1200, 2000]:
        state = dc.update(b)
        assert state["halted"] is True
        assert dc.can_trade() is False


def test_halt_is_sticky_even_when_new_peak_is_set_after_halt():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    dc.update(800.0)
    assert dc.halted is True
    state = dc.update(2000.0)  # new high after halt
    assert dc.peak == 2000.0
    assert state["halted"] is True  # does not un-halt automatically


# --- resume ---------------------------------------------------------------


def test_resume_clears_halt_flag():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    dc.update(800.0)
    assert dc.halted is True
    dc.resume()
    assert dc.halted is False
    assert dc.can_trade() is True


def test_resume_does_not_reset_peak():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    dc.update(1500.0)  # peak climbs
    dc.update(1200.0)  # 20% drawdown, halts
    assert dc.halted is True
    assert dc.peak == 1500.0
    dc.resume()
    assert dc.peak == 1500.0  # peak preserved


def test_can_re_halt_after_resume():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    dc.update(800.0)
    dc.resume()
    state = dc.update(700.0)  # 30% off the 1000 peak, re-halts
    assert state["halted"] is True


def test_resume_on_non_halted_controller_is_noop():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    assert dc.halted is False
    dc.resume()  # should not raise, should not change state
    assert dc.halted is False


# --- input validation on update ------------------------------------------


def test_update_rejects_negative_bankroll():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    with pytest.raises(ValueError):
        dc.update(-1.0)


def test_update_accepts_zero_bankroll():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    state = dc.update(0.0)
    assert state["drawdown"] == pytest.approx(1.0)
    assert state["halted"] is True


# --- can_trade semantics --------------------------------------------------


def test_can_trade_tracks_halted_flag():
    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)
    assert dc.can_trade() is True
    dc.update(800.0)
    assert dc.can_trade() is False
    dc.resume()
    assert dc.can_trade() is True
