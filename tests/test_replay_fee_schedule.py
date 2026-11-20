"""Offline replay fee proof with a fixed tape and frozen timestamps."""

from datetime import timedelta

import pytest

pytest.importorskip("fastapi", reason="requires the [backtest] extra; mandatory in backtest-extra-tests")

from simmer_sdk.backtest.replay.engine import ReplayConfig, ReplayEngine
from simmer_sdk.backtest.replay.simstate import ReplayTradeError, SimState, fee_for
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
    assert config.fee_rate is None
    engine = ReplayEngine(FakeTape(), NoTrades(), config)
    fill = engine.sim.buy(engine.session.view, "fake", "yes", 50)
    report = engine.report()
    assert fill.fee == 1.25
    assert report["summary"]["fees_paid"] == 1.25
    assert report["summary"]["fee_rate"] is None
    assert report["reproducibility"]["fee_rate"] is None
    assert report["fills"][0]["fee_rate"] == 0.05
    assert report["fills"][0]["fee_source"] == "generic_estimate"


def test_simstate_default_charges_generic_fee():
    state = SimState(starting_balance=100)
    fill = state.buy(ReplayView(FakeTape(), CUTOFF), "fake", "yes", 50)
    assert fill.fee == 1.25
    assert state.cash == 48.75


def test_replay_selects_category_fee_at_each_fill_across_cutoff():
    tape = FakeTape()
    engine = ReplayEngine(tape, NoTrades(), ReplayConfig(
        t0=BEFORE, t1=CUTOFF, fee_categories={"fake": "crypto_btc_5m_updown"},
    ))
    buy = engine.sim.buy(ReplayView(tape, BEFORE), "fake", "yes", 50)
    sell = engine.sim.sell(ReplayView(tape, CUTOFF), "fake", "yes", 100)
    assert buy.fee == pytest.approx(1.75)
    assert sell.fee == pytest.approx(1.25)
    report = engine.report()
    assert report["summary"]["fees_paid"] == 3
    assert [fill["fee_rate"] for fill in report["fills"]] == [.07, .05]
    assert all(fill["fee_source"] == "schedule" for fill in report["fills"])
    assert report["reproducibility"]["fee_categories"] == {"fake": "crypto_btc_5m_updown"}


@pytest.mark.parametrize("category", ["unknown", "crypto", "politics"])
def test_unknown_replay_category_cannot_fill(category):
    state = SimState(starting_balance=100, fee_categories={"fake": category})
    with pytest.raises(ReplayTradeError, match="unknown fee category"):
        state.buy(ReplayView(FakeTape(), CUTOFF), "fake", "yes", 50)
    assert state.cash == 100
    assert state.fills == []


@pytest.mark.parametrize("rate", [0, 0.01, 0.07])
def test_explicit_replay_override_is_preserved(rate):
    config = ReplayConfig(t0=CUTOFF, t1=CUTOFF, fee_rate=rate)
    engine = ReplayEngine(FakeTape(), NoTrades(), config)
    fill = engine.sim.buy(engine.session.view, "fake", "yes", 50)
    assert fill.fee == pytest.approx(25 * rate)
    assert engine.report()["summary"]["fee_rate"] == rate


@pytest.mark.parametrize("t0,t1", [(BEFORE, BEFORE), (CUTOFF, CUTOFF), (BEFORE, CUTOFF)])
def test_replay_reports_both_rebate_eras_and_unknown_volume(t0, t1):
    engine = ReplayEngine(FakeTape(), NoTrades(), ReplayConfig(t0=t0, t1=t1))
    gaps = " ".join(engine.report()["realism_gaps"])
    assert "2026-11-20T23:59:00Z" in gaps
    assert ("daily pools" in gaps) == (t0 < CUTOFF)
    assert ("30-day" in gaps) == (t1 >= CUTOFF)
    assert "unknown" in gaps
    assert "no rebates are credited" in gaps or "not credited" in gaps
    assert engine.report()["summary"]["maker_rebate_rate"] is None
    assert engine.report()["summary"]["rebates_credited"] == 0
    assert "the 20% rebate" not in gaps
    assert "generic 0.05 estimate, not a known category rate" in gaps


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
