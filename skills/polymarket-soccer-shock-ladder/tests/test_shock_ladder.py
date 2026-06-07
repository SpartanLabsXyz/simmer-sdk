"""Unit tests for the Shock Ladder pure strategy logic.

Runs against mock signals — no SDK, no network. Validates Roan's ladder math,
the bucket filter, exit pricing, and the signal→plan decision (including the
graceful skip paths).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shock_ladder import (  # noqa: E402
    compute_ladder,
    exit_price,
    favoritism_of,
    passes_bucket_filter,
    plan_from_signal,
    DEFAULT_LADDER_WEIGHTS,
)


def _signal(**over):
    s = {
        "type": "shock_ladder",
        "market_id": "mkt-1",
        "polymarket_token_id": "0xtok",
        "pre_price": 0.82,
        "side": "YES",
        "bucket_key": "deep|moderate|top-heavy|late|close",
        "percentile_depths": {"p50": 6.0, "p75": 9.0, "p90": 13.0, "p95": 18.0},
        "depth_source": "observed",
        "observed_count": 7,
        "ts": 1750000000.0,
        "shock_id": "mkt-1:1750000000.0",
    }
    s.update(over)
    return s


# --- ladder math ---

def test_compute_ladder_prices_and_weights():
    rungs = compute_ladder(0.82, {"p50": 6.0, "p75": 9.0, "p90": 13.0, "p95": 18.0}, 100.0)
    assert [r.label for r in rungs] == ["p50", "p75", "p90", "p95"]
    # price = pre_price - depth/100
    assert [r.price for r in rungs] == [0.76, 0.73, 0.69, 0.64]
    # weights 10/20/30/40 of 100
    assert [r.stake for r in rungs] == [10.0, 20.0, 30.0, 40.0]
    # deeper rungs carry more stake
    assert rungs[-1].stake > rungs[0].stake


def test_compute_ladder_clamps_and_drops_floor_rungs():
    # pre_price 0.10 with a 13c depth -> -0.03 -> clamps to floor -> dropped.
    rungs = compute_ladder(0.10, {"p50": 6.0, "p75": 9.0, "p90": 13.0, "p95": 18.0}, 100.0)
    labels = [r.label for r in rungs]
    assert "p90" not in labels and "p95" not in labels  # priced at/below floor
    assert "p50" in labels  # 0.10 - 0.06 = 0.04, valid


def test_compute_ladder_missing_depth_skips_rung():
    rungs = compute_ladder(0.82, {"p50": 6.0, "p90": 13.0}, 100.0)
    assert [r.label for r in rungs] == ["p50", "p90"]


def test_compute_ladder_guards():
    assert compute_ladder(0.82, {"p50": 6.0}, 0) == []      # no stake
    assert compute_ladder(None, {"p50": 6.0}, 100) == []    # no price
    assert compute_ladder(0.82, {}, 100) == []              # no depths


def test_raw_percentiles_not_pre_rounded():
    # A 6.5c depth must produce 0.755, not a rounded 0.76 — sizing precision matters.
    rungs = compute_ladder(0.82, {"p50": 6.5}, 100.0)
    assert rungs[0].price == 0.755


# --- exit pricing ---

def test_exit_price():
    assert exit_price(0.64) == 0.68          # +4c default
    assert exit_price(0.64, target_cents=2) == 0.66
    assert exit_price(0.98) == 0.999          # clamped to CLOB max


# --- bucket filter ---

def test_favoritism_of():
    assert favoritism_of("deep|moderate|top-heavy|late|close") == "moderate"
    assert favoritism_of("deep|underdog|unknown|unknown|unknown") == "underdog"
    assert favoritism_of("") == "unknown"


def test_bucket_filter_default_moderate_only():
    assert passes_bucket_filter("deep|moderate|top-heavy|late|close") is True
    assert passes_bucket_filter("deep|heavy|top-heavy|late|close") is False
    assert passes_bucket_filter("deep|underdog|deep|early|level") is False


def test_bucket_filter_empty_allowlist_passes_all():
    assert passes_bucket_filter("deep|heavy|x|y|z", allowed_favoritism=frozenset()) is True
    assert passes_bucket_filter("deep|underdog|x|y|z", allowed_favoritism=None) is True


def test_bucket_filter_custom_set():
    allow = frozenset({"moderate", "slight"})
    assert passes_bucket_filter("deep|slight|x|y|z", allow) is True
    assert passes_bucket_filter("deep|heavy|x|y|z", allow) is False


# --- plan_from_signal (the trader's entry point) ---

def test_plan_acts_on_moderate_signal():
    plan = plan_from_signal(_signal(), total_stake=100.0)
    assert plan.act is True and plan.skip_reason is None
    assert len(plan.rungs) == 4
    assert plan.rungs[0].price == 0.76


def test_plan_skips_wrong_type():
    plan = plan_from_signal(_signal(type="copytrade"), total_stake=100.0)
    assert plan.act is False and plan.skip_reason == "wrong_type"


def test_plan_skips_filtered_bucket():
    # heavy favorite — outside the default moderate-only filter.
    plan = plan_from_signal(_signal(bucket_key="deep|heavy|top-heavy|late|close"), total_stake=100.0)
    assert plan.act is False and plan.skip_reason == "filtered_bucket"


def test_plan_skips_malformed():
    assert plan_from_signal(_signal(pre_price=None), 100.0).skip_reason == "malformed"
    assert plan_from_signal(_signal(percentile_depths={}), 100.0).skip_reason == "malformed"
    assert plan_from_signal(_signal(market_id=None), 100.0).skip_reason == "malformed"


def test_plan_skips_no_rungs_when_all_clamp():
    # underdog low-price market in an allowed bucket, but depths push every rung
    # to the floor -> no_rungs (not malformed).
    sig = _signal(pre_price=0.06, bucket_key="deep|moderate|x|y|z",
                  percentile_depths={"p50": 8.0, "p75": 9.0, "p90": 13.0, "p95": 18.0})
    plan = plan_from_signal(sig, total_stake=100.0)
    assert plan.act is False and plan.skip_reason == "no_rungs"


def test_weights_sum_to_one():
    assert round(sum(DEFAULT_LADDER_WEIGHTS.values()), 6) == 1.0
