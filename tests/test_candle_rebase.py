"""Unit tests for the replay candle-window rebase (SIM-3070).

Candle-signal skills window candles by datetime.now(); under replay that's the
real present (after the frozen tick), so the server clamps the request to
nothing. get_candles rebases such a request to end at the frozen tick — but ONLY
when SIMMER_REPLAY_NOW is set (the replay harness sets it; production never does).
"""

from datetime import datetime, timezone

from simmer_sdk.client import SimmerClient

rebase = SimmerClient._replay_rebase_window


def test_no_op_when_env_unset(monkeypatch):
    monkeypatch.delenv("SIMMER_REPLAY_NOW", raising=False)
    assert rebase("2026-06-13T10:00:00+00:00", "2026-06-13T11:00:00+00:00") == (
        "2026-06-13T10:00:00+00:00", "2026-06-13T11:00:00+00:00")


def test_rebases_future_window_to_tick(monkeypatch):
    # tick is April; skill asks for a 1h window ending "now" in June
    monkeypatch.setenv("SIMMER_REPLAY_NOW", "2026-04-10T00:00:00+00:00")
    s, e = rebase("2026-06-13T10:00:00+00:00", "2026-06-13T11:00:00+00:00")
    # window now ends at the tick, duration preserved (1h)
    assert e == "2026-04-10T00:00:00+00:00"
    assert s == "2026-04-09T23:00:00+00:00"


def test_past_window_served_as_is(monkeypatch):
    # a window already ending at/before the tick is a valid historical request
    monkeypatch.setenv("SIMMER_REPLAY_NOW", "2026-04-10T00:00:00+00:00")
    assert rebase("2026-04-08T00:00:00+00:00", "2026-04-09T00:00:00+00:00") == (
        "2026-04-08T00:00:00+00:00", "2026-04-09T00:00:00+00:00")


def test_window_ending_exactly_at_tick_unchanged(monkeypatch):
    monkeypatch.setenv("SIMMER_REPLAY_NOW", "2026-04-10T00:00:00+00:00")
    assert rebase("2026-04-09T23:00:00+00:00", "2026-04-10T00:00:00+00:00") == (
        "2026-04-09T23:00:00+00:00", "2026-04-10T00:00:00+00:00")


def test_handles_z_suffix_and_naive(monkeypatch):
    monkeypatch.setenv("SIMMER_REPLAY_NOW", "2026-04-10T00:00:00Z")
    s, e = rebase("2026-06-13T10:00:00", "2026-06-13T11:00:00")  # naive => UTC
    assert e == datetime(2026, 4, 10, tzinfo=timezone.utc).isoformat()


def test_unparseable_input_is_no_op(monkeypatch):
    monkeypatch.setenv("SIMMER_REPLAY_NOW", "not-a-date")
    assert rebase("2026-06-13T10:00:00+00:00", "2026-06-13T11:00:00+00:00") == (
        "2026-06-13T10:00:00+00:00", "2026-06-13T11:00:00+00:00")
