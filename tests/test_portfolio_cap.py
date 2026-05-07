"""Tests for simmer_sdk.risk.portfolio_cap.

Covers the three required paths:
    - under-cap → allow
    - at-cap   → deny
    - would-exceed → trim

Plus boundary inputs, alternate position shapes, agent_id passthrough,
input validation, and a concurrency check that confirms the primitive is
safe to call from many threads against shared inputs.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from simmer_sdk.risk import (
    DEFAULT_TOTAL_CAP_PCT,
    PORTFOLIO_CAP_CONFIG_SCHEMA,
    PortfolioCapDecision,
    check_portfolio_cap,
    sum_open_notional,
)
from simmer_sdk.risk.portfolio_cap import _is_finite_nonneg, _position_notional


# --- Three required paths -----------------------------------------------------

def test_under_cap_allows_full_size():
    # bankroll 10k, cap 15% = 1500, open 500, candidate 200 -> fits.
    d = check_portfolio_cap(
        candidate_size=200.0,
        bankroll=10_000.0,
        current_open_notional=500.0,
        total_cap_pct=0.15,
    )
    assert d.decision == "allow"
    assert d.allowed_size == pytest.approx(200.0)
    assert d.reason == "under_cap"
    assert d.cap_notional == pytest.approx(1500.0)
    assert d.headroom == pytest.approx(1000.0)


def test_at_cap_denies():
    # bankroll 10k, cap 15% = 1500, open 1500 -> headroom 0, deny.
    d = check_portfolio_cap(
        candidate_size=100.0,
        bankroll=10_000.0,
        current_open_notional=1500.0,
        total_cap_pct=0.15,
    )
    assert d.decision == "deny"
    assert d.allowed_size == 0.0
    assert d.reason == "at_cap"
    assert d.headroom == pytest.approx(0.0, abs=1e-9)


def test_would_exceed_trims_to_headroom():
    # bankroll 10k, cap 15% = 1500, open 1400, candidate 200 -> trim to 100.
    d = check_portfolio_cap(
        candidate_size=200.0,
        bankroll=10_000.0,
        current_open_notional=1400.0,
        total_cap_pct=0.15,
    )
    assert d.decision == "trim_to"
    assert d.allowed_size == pytest.approx(100.0)
    assert d.candidate_size == pytest.approx(200.0)
    assert d.reason == "would_exceed_cap"
    assert d.headroom == pytest.approx(100.0)


# --- Over-cap and boundary -----------------------------------------------------

def test_over_cap_denies_with_over_cap_reason():
    # Already over the cap (manual override / external move). Distinct from
    # "at_cap" so observers can spot drift.
    d = check_portfolio_cap(
        candidate_size=100.0,
        bankroll=10_000.0,
        current_open_notional=1700.0,  # > 1500 cap
        total_cap_pct=0.15,
    )
    assert d.decision == "deny"
    assert d.reason == "over_cap"
    assert d.headroom < 0


def test_zero_open_allows_full_candidate():
    d = check_portfolio_cap(
        candidate_size=100.0,
        bankroll=10_000.0,
        current_open_notional=0.0,
    )
    assert d.decision == "allow"
    assert d.allowed_size == pytest.approx(100.0)
    assert d.reason == "under_cap"


def test_candidate_equal_to_headroom_allows():
    # Exact-fit candidate == headroom -> allow (epsilon tolerance applies).
    d = check_portfolio_cap(
        candidate_size=500.0,
        bankroll=10_000.0,
        current_open_notional=1000.0,
        total_cap_pct=0.15,
    )
    assert d.decision == "allow"
    assert d.allowed_size == pytest.approx(500.0)


def test_zero_candidate_allows_no_candidate():
    d = check_portfolio_cap(
        candidate_size=0.0,
        bankroll=10_000.0,
        current_open_notional=0.0,
    )
    assert d.decision == "allow"
    assert d.allowed_size == 0.0
    assert d.reason == "no_candidate"


def test_negative_candidate_treated_as_zero():
    d = check_portfolio_cap(
        candidate_size=-50.0,
        bankroll=10_000.0,
        current_open_notional=0.0,
    )
    assert d.decision == "allow"
    assert d.allowed_size == 0.0
    assert d.reason == "no_candidate"


# --- Input validation ----------------------------------------------------------

def test_invalid_bankroll_zero_denies():
    d = check_portfolio_cap(
        candidate_size=100.0,
        bankroll=0.0,
        current_open_notional=0.0,
    )
    assert d.decision == "deny"
    assert d.reason == "invalid_bankroll"
    assert d.allowed_size == 0.0


def test_invalid_bankroll_negative_denies():
    d = check_portfolio_cap(
        candidate_size=100.0,
        bankroll=-1.0,
        current_open_notional=0.0,
    )
    assert d.decision == "deny"
    assert d.reason == "invalid_bankroll"


def test_invalid_bankroll_nan_denies():
    d = check_portfolio_cap(
        candidate_size=100.0,
        bankroll=float("nan"),
        current_open_notional=0.0,
    )
    assert d.decision == "deny"
    assert d.reason == "invalid_bankroll"


def test_invalid_cap_pct_zero_denies():
    d = check_portfolio_cap(
        candidate_size=100.0,
        bankroll=10_000.0,
        current_open_notional=0.0,
        total_cap_pct=0.0,
    )
    assert d.decision == "deny"
    assert d.reason == "invalid_cap_pct"


def test_invalid_cap_pct_above_one_denies():
    d = check_portfolio_cap(
        candidate_size=100.0,
        bankroll=10_000.0,
        current_open_notional=0.0,
        total_cap_pct=1.5,
    )
    assert d.decision == "deny"
    assert d.reason == "invalid_cap_pct"


def test_cap_pct_one_is_valid_full_bankroll():
    d = check_portfolio_cap(
        candidate_size=10_000.0,
        bankroll=10_000.0,
        current_open_notional=0.0,
        total_cap_pct=1.0,
    )
    assert d.decision == "allow"
    assert d.allowed_size == pytest.approx(10_000.0)


def test_both_open_inputs_raises():
    with pytest.raises(ValueError):
        check_portfolio_cap(
            candidate_size=100.0,
            bankroll=10_000.0,
            open_positions=[],
            current_open_notional=0.0,
        )


def test_no_open_inputs_treated_as_zero():
    d = check_portfolio_cap(
        candidate_size=100.0,
        bankroll=10_000.0,
    )
    assert d.decision == "allow"
    assert d.current_open_notional == 0.0


def test_invalid_open_notional_treated_as_zero():
    d = check_portfolio_cap(
        candidate_size=100.0,
        bankroll=10_000.0,
        current_open_notional=float("nan"),
    )
    # Falls through to zero-open path.
    assert d.decision == "allow"
    assert d.current_open_notional == 0.0


# --- Position shape parsing ----------------------------------------------------

def test_open_positions_dict_with_current_value():
    positions = [
        {"market_id": "a", "current_value": 300.0, "status": "active"},
        {"market_id": "b", "current_value": 700.0, "status": "active"},
    ]
    d = check_portfolio_cap(
        candidate_size=200.0,
        bankroll=10_000.0,
        open_positions=positions,
        total_cap_pct=0.15,
    )
    assert d.current_open_notional == pytest.approx(1000.0)
    # cap = 1500, headroom = 500, candidate 200 -> allow.
    assert d.decision == "allow"


def test_open_positions_dict_with_notional_alias():
    positions = [{"notional": 250.0}, {"open_notional": 250.0}]
    assert sum_open_notional(positions) == pytest.approx(500.0)


def test_open_positions_skips_closed_dict():
    positions = [
        {"current_value": 300.0, "status": "active"},
        {"current_value": 999.0, "status": "closed"},
    ]
    assert sum_open_notional(positions) == pytest.approx(300.0)


def test_open_positions_object_duck_typed():
    class FakePosition:
        def __init__(self, current_value, status="active"):
            self.current_value = current_value
            self.status = status

    positions = [FakePosition(400.0), FakePosition(600.0), FakePosition(99.0, "closed")]
    assert sum_open_notional(positions) == pytest.approx(1000.0)


def test_sum_open_notional_handles_none_and_unknown():
    assert sum_open_notional(None) == 0.0
    assert sum_open_notional([None, object(), {}]) == 0.0


def test_sum_open_notional_passthrough_numeric():
    assert sum_open_notional([100, 200.5, 50]) == pytest.approx(350.5)


def test_sum_open_notional_rejects_negative_and_nonfinite():
    assert sum_open_notional([-100, float("inf"), float("nan"), 50]) == pytest.approx(50.0)


def test_position_notional_returns_zero_for_missing_value():
    assert _position_notional({"market_id": "x"}) == 0.0
    assert _position_notional({"current_value": None}) == 0.0
    class Bare:
        pass
    assert _position_notional(Bare()) == 0.0


# --- Real SDK Position dataclass ----------------------------------------------

def test_works_with_sdk_position_dataclass():
    """Confirms the primitive accepts the canonical Position dataclass."""
    from simmer_sdk.client import Position

    positions = [
        Position(
            market_id="m1",
            question="q",
            shares_yes=100.0,
            shares_no=0.0,
            current_value=600.0,
            pnl=0.0,
            status="active",
            venue="polymarket",
        ),
        Position(
            market_id="m2",
            question="q",
            shares_yes=0.0,
            shares_no=200.0,
            current_value=900.0,
            pnl=0.0,
            status="active",
            venue="sim",
        ),
        # Closed position should be ignored.
        Position(
            market_id="m3",
            question="q",
            shares_yes=0.0,
            shares_no=0.0,
            current_value=500.0,
            pnl=0.0,
            status="closed",
            venue="polymarket",
        ),
    ]
    d = check_portfolio_cap(
        candidate_size=100.0,
        bankroll=10_000.0,
        open_positions=positions,
        total_cap_pct=0.15,
    )
    # 600 + 900 = 1500 open (closed ignored). Cap = 1500. Already at cap.
    assert d.current_open_notional == pytest.approx(1500.0)
    assert d.decision == "deny"
    assert d.reason == "at_cap"


# --- Agent id and decision metadata -------------------------------------------

def test_agent_id_echoed_back():
    d = check_portfolio_cap(
        candidate_size=100.0,
        bankroll=10_000.0,
        current_open_notional=0.0,
        agent_id="agent_abc",
    )
    assert d.agent_id == "agent_abc"


def test_decision_is_frozen_dataclass():
    d = check_portfolio_cap(candidate_size=100.0, bankroll=10_000.0)
    with pytest.raises(Exception):
        d.decision = "deny"  # type: ignore[misc]


# --- Helpers ------------------------------------------------------------------

def test_is_finite_nonneg_basics():
    assert _is_finite_nonneg(0.0)
    assert _is_finite_nonneg(1.5)
    assert not _is_finite_nonneg(-0.1)
    assert not _is_finite_nonneg(float("nan"))
    assert not _is_finite_nonneg(float("inf"))
    assert not _is_finite_nonneg(float("-inf"))
    assert not _is_finite_nonneg("nope")  # type: ignore[arg-type]


# --- Defaults and config schema -----------------------------------------------

def test_default_cap_pct_is_15_percent():
    assert DEFAULT_TOTAL_CAP_PCT == 0.15


def test_config_schema_keys():
    assert set(PORTFOLIO_CAP_CONFIG_SCHEMA) == {
        "portfolio_cap_enabled",
        "portfolio_cap_pct",
    }
    enabled = PORTFOLIO_CAP_CONFIG_SCHEMA["portfolio_cap_enabled"]
    assert enabled["default"] is False  # opt-in per ticket
    assert enabled["type"] is bool
    pct = PORTFOLIO_CAP_CONFIG_SCHEMA["portfolio_cap_pct"]
    assert pct["default"] == DEFAULT_TOTAL_CAP_PCT


# --- Concurrency safety -------------------------------------------------------

def test_pure_function_is_thread_safe():
    """Many threads against the same inputs must all return the same decision.

    The primitive holds no mutable state, so this is really a tripwire: if
    a future change introduces a module-level cache or a shared list, this
    test will catch decision drift across workers.
    """
    positions = tuple({"current_value": v, "status": "active"} for v in (300.0, 400.0, 500.0))

    def call(_i):
        return check_portfolio_cap(
            candidate_size=200.0,
            bankroll=10_000.0,
            open_positions=positions,
            total_cap_pct=0.15,
        )

    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(call, range(256)))

    first = results[0]
    # open = 300 + 400 + 500 = 1200, cap = 1500, headroom = 300, candidate 200 -> allow.
    assert first.decision == "allow"
    assert first.current_open_notional == pytest.approx(1200.0)
    assert first.headroom == pytest.approx(300.0)
    for r in results:
        assert r.decision == first.decision
        assert r.allowed_size == first.allowed_size
        assert r.current_open_notional == first.current_open_notional
        assert r.headroom == first.headroom


def test_concurrent_at_cap_consistent_deny():
    """All workers see the same at-cap state and return deny consistently."""
    positions = ({"current_value": 1500.0, "status": "active"},)

    def call(_i):
        return check_portfolio_cap(
            candidate_size=100.0,
            bankroll=10_000.0,
            open_positions=positions,
            total_cap_pct=0.15,
        )

    with ThreadPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(call, range(128)))

    assert all(r.decision == "deny" for r in results)
    assert all(r.reason == "at_cap" for r in results)
    assert all(r.allowed_size == 0.0 for r in results)
