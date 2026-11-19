"""Offline replay fee proof with a fixed tape and frozen timestamps."""

from datetime import timedelta

import pytest

pytest.importorskip("fastapi", reason="requires the [backtest] extra; mandatory in backtest-extra-tests")

from simmer_sdk.backtest.replay.engine import ReplayConfig, ReplayEngine
from simmer_sdk.backtest.replay.simstate import SimState, fee_for
from simmer_sdk.backtest.replay.store import PricePoint, ReplayView, TradePrint
from simmer_sdk.polymarket_fees import CUTOFF


BEFORE = CUTOFF - timedelta(microseconds=1)


class FakeTape:
    def __init__(self, price=0.5):
        self.fill_price = price

    def markets(self, at, **filters):
        return []

    def price(self, market_id, at):
        return PricePoint(market_id, at, self.fill_price)

    def prices(self, market_id, t0, t1):
        return [self.price(market_id, t1)]

    def trades(self, market_id, t0, t1):
        return [TradePrint(market_id, t1, self.fill_price, 50, 100, "SELL")]

    def resolution(self, market_id):
        return None


class NoTrades:
    def on_tick(self, api, now):
        pass


@pytest.mark.parametrize("at", [BEFORE, CUTOFF])
@pytest.mark.parametrize("explicit_none", [False, True])
def test_generic_replay_default_uses_other_schedule(at, explicit_none):
    kwargs = {"fee_rate": None} if explicit_none else {}
    config = ReplayConfig(t0=at, t1=at, **kwargs)
    assert config.fee_rate == 0.05
    engine = ReplayEngine(FakeTape(), NoTrades(), config)
    fill = engine.sim.buy(engine.session.view, "fake", "yes", 50)
    report = engine.report()
    assert fill.fee == 1.25
    assert report["summary"]["fees_paid"] == 1.25
    assert report["summary"]["fee_rate"] == 0.05
    assert report["reproducibility"]["fee_rate"] == 0.05


@pytest.mark.parametrize("rate", [0, 0.01, 0.07])
def test_explicit_replay_override_is_preserved(rate):
    config = ReplayConfig(t0=CUTOFF, t1=CUTOFF, fee_rate=rate)
    engine = ReplayEngine(FakeTape(), NoTrades(), config)
    fill = engine.sim.buy(engine.session.view, "fake", "yes", 50)
    assert fill.fee == pytest.approx(25 * rate)
    assert engine.report()["summary"]["fee_rate"] == rate


@pytest.mark.parametrize("at", [BEFORE, CUTOFF])
def test_replay_reports_both_rebate_eras_and_unknown_volume(at):
    engine = ReplayEngine(FakeTape(), NoTrades(), ReplayConfig(t0=at, t1=at))
    gaps = " ".join(engine.report()["realism_gaps"])
    assert "2026-11-20T23:59:00Z" in gaps
    assert "daily pools" in gaps
    assert "30-day" in gaps
    assert "unknown" in gaps
    assert "conservative" in gaps
    assert "the 20% rebate" not in gaps
    assert "Other/General" in gaps


@pytest.mark.parametrize("price,fee", [(0.05, 0.2375), (0.5, 1.25), (0.95, 0.2375)])
def test_fee_is_price_dependent_for_one_hundred_shares(price, fee):
    assert fee_for(100 * price, price, 0.05) == pytest.approx(fee)


@pytest.mark.parametrize("at", [BEFORE, CUTOFF])
def test_maker_fill_never_credits_an_unknown_rebate(at):
    tape = FakeTape(price=0.6)
    state = SimState(starting_balance=100, fee_rate=0.05)
    state.buy(ReplayView(tape, at), "fake", "yes", 50, limit_price=0.5)
    tape.fill_price = 0.5
    state.advance_fills(ReplayView(tape, at + timedelta(seconds=1)), since=at)
    assert len(state.fills) == 1
    assert state.fills[0].kind == "limit"
    assert state.fills[0].fee == 0
    assert state.cash == 50
    assert state.positions["fake"].shares_yes == 100
