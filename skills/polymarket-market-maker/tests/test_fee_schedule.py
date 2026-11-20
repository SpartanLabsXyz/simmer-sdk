"""Offline quote estimates across the official fee/rebate cutoff."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest


BEFORE = datetime(2026, 11, 20, 23, 58, 59, tzinfo=timezone.utc)
AFTER = datetime(2026, 11, 20, 23, 59, tzinfo=timezone.utc)


@pytest.fixture
def skill(monkeypatch):
    monkeypatch.setattr(sys.stdout, "reconfigure", lambda **kwargs: None, raising=False)
    spec = importlib.util.spec_from_file_location(
        "fee_schedule_market_maker", Path(__file__).parents[1] / "market_maker.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "get_market_mid", lambda _: 0.50)
    monkeypatch.setattr(module, "get_inventory", lambda _: (0, 0))
    return module


@pytest.mark.parametrize("price", [0.05, 0.50, 0.95])
def test_fee_equivalent_uses_notional_not_shares(skill, price):
    assert skill.fee_equivalent(100 * price, price, 0.07) == pytest.approx(
        100 * 0.07 * price * (1 - price)
    )


def quote(skill, monkeypatch, market, at, volume=None):
    monkeypatch.setattr(skill, "get_market_info", lambda _: market)
    return skill.run_market(
        market_id="fake-market", market_state={}, live=False,
        spread_pct=0.04, quote_usdc=10, max_skew_pct=0.30,
        drift_threshold=0.02, open_order_ids=set(), at=at,
        maker_volume_30d=volume,
    )


@pytest.mark.parametrize("at, coefficient, pool, rate", [
    (BEFORE, 0.07, 0.20, None),
    (AFTER, 0.05, None, 0.03),
])
def test_quote_estimate_changes_program_at_cutoff(skill, monkeypatch, at, coefficient, pool, rate):
    result = quote(skill, monkeypatch, {
        "category": "crypto", "slug": "btc-updown-5m-1795219200",
    }, at, 200_000)
    assert result["bid_placed"] and result["ask_placed"]
    assert result["quoted_fee_equiv_usd"] == pytest.approx(20 * coefficient * 0.52)
    assert result["maker_pool_fraction"] == pool
    assert result["maker_rebate_rate"] == rate


def test_unknown_category_and_tier_are_not_zero(skill, monkeypatch):
    result = quote(skill, monkeypatch, {}, AFTER)
    assert result["quoted_fee_equiv_usd"] is None
    assert result["maker_rebate_rate"] is None
    known_category = quote(skill, monkeypatch, {"category": "sports"}, AFTER)
    assert known_category["quoted_fee_equiv_usd"] == pytest.approx(0.52)
    assert known_category["maker_rebate_rate"] is None


def test_zero_volume_is_known_zero_rebate(skill, monkeypatch):
    result = quote(skill, monkeypatch, {"category": "sports"}, AFTER, 0)
    assert result["maker_rebate_rate"] == 0


def test_no_invented_taker_fee_default(skill):
    assert "taker_fee_rate" not in skill.CONFIG_SCHEMA
    assert skill.CONFIG_SCHEMA["maker_volume_30d"]["default"] is None
