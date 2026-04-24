"""Tests for simmer_sdk.execution.partial_fill — SIM-1079.

Covers the four terminal-status paths described in the ticket:

  1. FILLED           — fill reaches accept_pct
  2. PARTIAL          — early exit at partial_exit_pct after partial_exit_time_frac
  3. TIMEOUT_PARTIAL  — max_wait elapses with some fill
  4. TIMEOUT_NO_FILL  — max_wait elapses with zero fill

Plus edge cases: cancel failure, poll failure, poll return shapes, threshold
validation, 100% fill skipping remainder cancel.

All tests use a virtual clock (FakeClock) and a scripted poll function — no
time.sleep calls are actually made.
"""

from __future__ import annotations

import warnings
from typing import List, Optional

import pytest

# Function is deprecated (removal scheduled for 0.12.0). Silence the warning
# across the behavioural tests so signal stays on the assertions; the contract
# test below explicitly verifies the warning still fires on call.
pytestmark = pytest.mark.filterwarnings(
    "ignore:simmer_sdk.execution.await_fill is deprecated:DeprecationWarning"
)

from simmer_sdk.execution import (
    FillResult,
    FillStatus,
    await_fill,
    clob_poll_fn,
    clob_cancel_fn,
)


# ---- Test scaffolding ------------------------------------------------------


class FakeClock:
    """Monotonic virtual clock. `sleep` advances it."""

    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def scripted_poll(fills_over_time):
    """Return a poll callable that, on the Nth call, returns the Nth entry.
    If called more times than the list, returns the last entry forever."""
    calls = {"n": 0}

    def _poll(order_id: str):
        i = min(calls["n"], len(fills_over_time) - 1)
        calls["n"] += 1
        return fills_over_time[i]

    return _poll


def counting_cancel(result=None):
    """Return a cancel callable that records the calls made against it."""
    calls: List[str] = []

    def _cancel(order_id: str):
        calls.append(order_id)
        return result if result is not None else {"canceled": [order_id]}

    _cancel.calls = calls  # type: ignore[attr-defined]
    return _cancel


# ---- Deprecation contract --------------------------------------------------


def test_await_fill_emits_deprecation_warning():
    clock = FakeClock()
    poll = scripted_poll([100.0])
    cancel = counting_cancel()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await_fill(
            "ord",
            target_size=100.0,
            max_wait=10.0,
            poll=poll,
            cancel=cancel,
            _time=clock.time,
            _sleep=clock.sleep,
        )
    assert any(
        issubclass(w.category, DeprecationWarning)
        and "await_fill is deprecated" in str(w.message)
        for w in caught
    ), "Expected DeprecationWarning on await_fill() call"


# ---- Path 1: FILLED --------------------------------------------------------


def test_filled_reaches_accept_pct():
    clock = FakeClock()
    poll = scripted_poll([0.0, 50.0, 96.0])  # 0%, 50%, 96% of 100 target
    cancel = counting_cancel()

    result = await_fill(
        order_id="ord_1",
        target_size=100.0,
        max_wait=10.0,
        poll=poll,
        cancel=cancel,
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    assert result.status == FillStatus.FILLED
    assert result.filled_size == 96.0
    assert result.fill_ratio == pytest.approx(0.96)
    assert result.cancel_attempted is True  # 96% < 100%, remainder cancelled
    assert cancel.calls == ["ord_1"]
    assert result.elapsed < 10.0


def test_filled_full_100pct_skips_remainder_cancel():
    """If the fill hits 100%, there is no open remainder — don't cancel."""
    clock = FakeClock()
    poll = scripted_poll([0.0, 50.0])
    cancel = counting_cancel()

    result = await_fill(
        order_id="ord_full",
        target_size=50.0,
        max_wait=10.0,
        poll=poll,
        cancel=cancel,
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    assert result.status == FillStatus.FILLED
    assert result.fill_ratio == pytest.approx(1.0)
    assert result.cancel_attempted is False
    assert cancel.calls == []


def test_filled_first_poll_exits_immediately():
    """If the order is already past accept_pct at the first poll, we exit."""
    clock = FakeClock()
    poll = scripted_poll([100.0])  # already filled
    cancel = counting_cancel()

    result = await_fill(
        order_id="ord_prefilled",
        target_size=100.0,
        max_wait=30.0,
        poll=poll,
        cancel=cancel,
        poll_interval=2.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    assert result.status == FillStatus.FILLED
    assert result.polls == 1
    assert clock.now == 0.0  # never slept


# ---- Path 2: PARTIAL (early exit) -----------------------------------------


def test_partial_early_exit_after_time_frac():
    """At 70% of timeout with 60% fill, PARTIAL should trigger."""
    clock = FakeClock()
    # target=100, max_wait=10, poll_interval=1. Fills: 0 → 30 → 60 → 60 → ...
    # partial_arm_time = 7.0s
    # Poll 1 @ t=0: 0 (no exit); sleep 1 → t=1
    # Poll 2 @ t=1: 30 (no exit); sleep 1 → t=2
    # Poll 3 @ t=2: 60 (60% >= 50% but t<7 not yet armed); sleep 1 → t=3
    # ... continue with 60 ...
    # Poll @ t=7: 60 (armed AND 60% >= 50%) → PARTIAL
    poll = scripted_poll([0.0, 30.0, 60.0, 60.0, 60.0, 60.0, 60.0, 60.0])
    cancel = counting_cancel()

    result = await_fill(
        order_id="ord_partial",
        target_size=100.0,
        max_wait=10.0,
        poll=poll,
        cancel=cancel,
        partial_exit_pct=0.50,
        partial_exit_time_frac=0.70,
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    assert result.status == FillStatus.PARTIAL
    assert result.filled_size == 60.0
    assert result.fill_ratio == pytest.approx(0.60)
    assert result.elapsed == pytest.approx(7.0)
    assert result.cancel_attempted is True
    assert cancel.calls == ["ord_partial"]


def test_partial_below_pct_does_not_exit_early():
    """40% fill at t=7 (armed) is below 50% threshold → keep waiting."""
    clock = FakeClock()
    # target=100, fills stuck at 40 → timeout_partial at t=10
    poll = scripted_poll([0.0, 20.0, 40.0, 40.0, 40.0, 40.0, 40.0, 40.0, 40.0, 40.0, 40.0, 40.0])
    cancel = counting_cancel()

    result = await_fill(
        order_id="ord_slow",
        target_size=100.0,
        max_wait=10.0,
        poll=poll,
        cancel=cancel,
        partial_exit_pct=0.50,
        partial_exit_time_frac=0.70,
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    assert result.status == FillStatus.TIMEOUT_PARTIAL
    assert result.filled_size == 40.0
    assert result.elapsed >= 10.0


def test_partial_armed_but_not_reached_until_later():
    """At t=7 fill is 40% (no), grows to 55% at t=8 → PARTIAL."""
    clock = FakeClock()
    # Polls at t = 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
    # fills:       0  0  0  0  0  0  0  40 55 ...
    poll = scripted_poll([0, 0, 0, 0, 0, 0, 0, 40, 55, 55, 55])
    cancel = counting_cancel()

    result = await_fill(
        order_id="ord_late_partial",
        target_size=100.0,
        max_wait=10.0,
        poll=poll,
        cancel=cancel,
        partial_exit_pct=0.50,
        partial_exit_time_frac=0.70,
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    assert result.status == FillStatus.PARTIAL
    assert result.filled_size == 55.0
    assert result.elapsed == pytest.approx(8.0)


# ---- Path 3: TIMEOUT_PARTIAL -----------------------------------------------


def test_timeout_partial_with_small_fill():
    """Fill stays at 20% (below both accept_pct and partial_exit_pct) → TIMEOUT_PARTIAL."""
    clock = FakeClock()
    poll = scripted_poll([0.0, 10.0, 20.0] + [20.0] * 20)
    cancel = counting_cancel()

    result = await_fill(
        order_id="ord_timeout_partial",
        target_size=100.0,
        max_wait=10.0,
        poll=poll,
        cancel=cancel,
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    assert result.status == FillStatus.TIMEOUT_PARTIAL
    assert result.filled_size == 20.0
    assert result.elapsed >= 10.0
    assert result.cancel_attempted is True
    assert cancel.calls == ["ord_timeout_partial"]


# ---- Path 4: TIMEOUT_NO_FILL -----------------------------------------------


def test_timeout_no_fill():
    """Zero fill throughout → TIMEOUT_NO_FILL."""
    clock = FakeClock()
    poll = scripted_poll([0.0] * 30)
    cancel = counting_cancel()

    result = await_fill(
        order_id="ord_nofill",
        target_size=100.0,
        max_wait=5.0,
        poll=poll,
        cancel=cancel,
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    assert result.status == FillStatus.TIMEOUT_NO_FILL
    assert result.filled_size == 0.0
    assert result.fill_ratio == 0.0
    assert result.elapsed >= 5.0
    assert result.cancel_attempted is True  # still fire-and-forget cancel
    assert cancel.calls == ["ord_nofill"]


# ---- Cancel-failure handling ----------------------------------------------


def test_cancel_failure_surfaces_in_result():
    """cancel() raising → FillResult.cancel_error is populated; status unchanged."""
    clock = FakeClock()
    poll = scripted_poll([0.0, 30.0, 30.0] + [30.0] * 20)

    def bad_cancel(order_id):
        raise RuntimeError("CLOB 500")

    result = await_fill(
        order_id="ord_cancel_fail",
        target_size=100.0,
        max_wait=5.0,
        poll=poll,
        cancel=bad_cancel,
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    assert result.status == FillStatus.TIMEOUT_PARTIAL
    assert result.cancel_attempted is True
    assert result.cancel_result is None
    assert "CLOB 500" in (result.cancel_error or "")


# ---- Poll-error handling ---------------------------------------------------


def test_poll_failure_continues_but_records_error():
    """Transient poll exceptions don't abort the wait loop."""
    clock = FakeClock()
    call_count = {"n": 0}

    def flaky_poll(order_id):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise ConnectionError("transient")
        if call_count["n"] >= 4:
            return {"size_matched": "100"}
        return {"size_matched": "0"}

    cancel = counting_cancel()

    result = await_fill(
        order_id="ord_flaky",
        target_size=100.0,
        max_wait=30.0,
        poll=flaky_poll,
        cancel=cancel,
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    assert result.status == FillStatus.FILLED
    # last_poll_error should be None because the final poll succeeded
    assert result.last_poll_error is None


def test_poll_always_fails_still_terminates():
    """Even if every poll fails, the function must respect max_wait."""
    clock = FakeClock()

    def always_fail(order_id):
        raise ConnectionError("network down")

    cancel = counting_cancel()

    result = await_fill(
        order_id="ord_offline",
        target_size=100.0,
        max_wait=3.0,
        poll=always_fail,
        cancel=cancel,
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    assert result.status == FillStatus.TIMEOUT_NO_FILL
    assert result.last_poll_error == "network down"
    assert result.elapsed >= 3.0


# ---- Poll return-shape flexibility ----------------------------------------


@pytest.mark.parametrize(
    "poll_returns,expected_filled",
    [
        ([{"size_matched": "95"}], 95.0),       # CLOB dict with string (Polymarket)
        ([{"size_matched": 95.0}], 95.0),       # dict with float
        ([{"filled": 95.0}], 95.0),             # alternate key
        ([{"matched": 95}], 95.0),              # alternate key
        ([{"sizeMatched": "95"}], 95.0),        # camelCase
        ([95.0], 95.0),                         # bare float
        (["95"], 95.0),                         # bare string
        ([95], 95.0),                           # bare int
        ([None], 0.0),                          # None → 0
        ([{"status": "live"}], 0.0),            # no relevant key → 0
    ],
)
def test_poll_return_shape_flexibility(poll_returns, expected_filled):
    clock = FakeClock()
    # If the first poll already exceeds accept_pct, the function returns FILLED.
    # For the 0.0 cases, pad with more zeros so the run hits TIMEOUT_NO_FILL.
    if expected_filled >= 95.0:
        script = poll_returns
    else:
        script = poll_returns + [0.0] * 20

    cancel = counting_cancel()
    result = await_fill(
        order_id="ord_shape",
        target_size=100.0,
        max_wait=3.0,
        poll=scripted_poll(script),
        cancel=cancel,
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )
    assert result.filled_size == expected_filled


# ---- Threshold validation --------------------------------------------------


def test_rejects_accept_pct_out_of_range():
    with pytest.raises(ValueError):
        await_fill(
            order_id="x", target_size=100, max_wait=10,
            poll=lambda _: 0.0, cancel=lambda _: {},
            accept_pct=1.5,
        )


def test_rejects_partial_pct_above_accept_pct():
    with pytest.raises(ValueError):
        await_fill(
            order_id="x", target_size=100, max_wait=10,
            poll=lambda _: 0.0, cancel=lambda _: {},
            accept_pct=0.80,
            partial_exit_pct=0.90,
        )


def test_rejects_zero_target_size():
    with pytest.raises(ValueError):
        await_fill(
            order_id="x", target_size=0.0, max_wait=10,
            poll=lambda _: 0.0, cancel=lambda _: {},
        )


def test_rejects_zero_max_wait():
    with pytest.raises(ValueError):
        await_fill(
            order_id="x", target_size=100.0, max_wait=0.0,
            poll=lambda _: 0.0, cancel=lambda _: {},
        )


# ---- Monotonicity of filled_size -------------------------------------------


def test_filled_is_monotonic_even_if_poll_drops():
    """If a poll transiently returns a smaller fill (CLOB bug / pagination),
    we keep the max seen. Polymarket orders never shrink."""
    clock = FakeClock()
    poll = scripted_poll([50.0, 80.0, 40.0, 96.0])  # dip at poll 3
    cancel = counting_cancel()

    result = await_fill(
        order_id="ord_monotonic",
        target_size=100.0,
        max_wait=30.0,
        poll=poll,
        cancel=cancel,
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    assert result.status == FillStatus.FILLED
    assert result.filled_size == 96.0


# ---- clob_poll_fn / clob_cancel_fn -----------------------------------------


class _FakeClob:
    """Minimal py_clob_client stand-in."""

    def __init__(self, size_matched="50", cancel_result=None):
        self._size_matched = size_matched
        self._cancel_result = cancel_result or {"canceled": ["any"]}
        self.get_order_calls = []
        self.cancel_calls = []

    def get_order(self, order_id):
        self.get_order_calls.append(order_id)
        return {"id": order_id, "size_matched": self._size_matched, "status": "live"}

    def cancel(self, order_id):
        self.cancel_calls.append(order_id)
        return self._cancel_result


def test_clob_helpers_wire_correctly():
    clock = FakeClock()
    clob = _FakeClob(size_matched="100")

    result = await_fill(
        order_id="ord_clob",
        target_size=100.0,
        max_wait=5.0,
        poll=clob_poll_fn(clob),
        cancel=clob_cancel_fn(clob),
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    assert result.status == FillStatus.FILLED
    assert clob.get_order_calls == ["ord_clob"]
    # Full fill → no remainder cancel.
    assert clob.cancel_calls == []


def test_clob_helpers_cancel_on_partial():
    clock = FakeClock()
    clob = _FakeClob(size_matched="0")

    result = await_fill(
        order_id="ord_clob_partial",
        target_size=100.0,
        max_wait=3.0,
        poll=clob_poll_fn(clob),
        cancel=clob_cancel_fn(clob),
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    assert result.status == FillStatus.TIMEOUT_NO_FILL
    assert clob.cancel_calls == ["ord_clob_partial"]


# ---- to_dict serialisation -------------------------------------------------


def test_fill_result_to_dict_shape():
    clock = FakeClock()
    result = await_fill(
        order_id="ord_dict",
        target_size=100.0,
        max_wait=1.0,
        poll=scripted_poll([100.0]),
        cancel=counting_cancel(),
        poll_interval=1.0,
        _time=clock.time,
        _sleep=clock.sleep,
    )

    d = result.to_dict()
    assert d["status"] == "FILLED"  # string-valued for JSON
    assert d["order_id"] == "ord_dict"
    assert d["target_size"] == 100.0
    assert d["fill_ratio"] == 1.0
    # Round-trip-safe for logging
    import json
    json.dumps(d)
