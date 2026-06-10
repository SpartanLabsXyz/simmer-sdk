"""Trader I/O tests: state persistence, locking, and fake-client execution."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import parlay_roller_trader as trader
from parlay_roller import RollerConfig, StreakState

UTC = timezone.utc
NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)

# Captured pre-patch so test_combo_compare_degrades can exercise the real function.
ORIG_FETCH_COMBO = trader.fetch_combo_comparison


@pytest.fixture(autouse=True)
def _no_combo_network(monkeypatch):
    """Keep ticks offline: the streak-start combo lookup must never hit the API."""
    monkeypatch.setattr(trader, "fetch_combo_comparison", lambda market_ids: None)


def example_config_dict(start=None):
    start_dt = start or datetime(2026, 6, 12, 15, 0, tzinfo=UTC)
    later = datetime(2026, 6, 12, 19, 0, tzinfo=UTC)
    return {
        "stake_usd": 25.0,
        "legs": [
            {
                "market_id": "m0",
                "side": "yes",
                "label": "Mexico beat South Africa",
                "resolution_note": "Mexico WIN only - a draw loses this leg",
                "kickoff": start_dt.isoformat(),
                "expected_end": None,
            },
            {
                "market_id": "m1",
                "side": "yes",
                "label": "USA beat Paraguay",
                "resolution_note": "USA WIN only - a draw loses this leg",
                "kickoff": later.isoformat(),
                "expected_end": None,
            },
        ],
    }


def test_load_config_validates(tmp_path):
    path = tmp_path / "roller_config.json"
    path.write_text(json.dumps(example_config_dict()))
    cfg = trader.load_config(str(path), now=NOW)
    assert isinstance(cfg, RollerConfig)
    assert len(cfg.legs) == 2


def test_load_config_rejects_invalid(tmp_path):
    bad = example_config_dict()
    bad["stake_usd"] = -5
    path = tmp_path / "roller_config.json"
    path.write_text(json.dumps(bad))
    with pytest.raises(SystemExit):
        trader.load_config(str(path), now=NOW)


def test_state_save_load_roundtrip(tmp_path):
    state_path = tmp_path / "roller_state.json"
    cfg = RollerConfig.from_dict(example_config_dict())
    state = StreakState.fresh(cfg)
    trader.save_state(state, str(state_path))
    loaded = trader.load_state(str(state_path))
    assert loaded.phase == "CONFIGURED"
    assert loaded.cash == 25.0


def test_lock_prevents_concurrent_ticks(tmp_path):
    lock = tmp_path / "roller.lock"
    with trader.tick_lock(str(lock)):
        with pytest.raises(SystemExit):
            with trader.tick_lock(str(lock)):
                pass
    with trader.tick_lock(str(lock)):
        pass


class FakeMarket:
    def __init__(self, mid=0.5, bid=None, ask=None, status="active", resolved_yes=None):
        self.current_probability = mid
        self.best_bid = bid if bid is not None else (mid - 0.01 if mid is not None else None)
        self.best_ask = ask if ask is not None else (mid + 0.01 if mid is not None else None)
        self.status = status
        self.resolved_yes = resolved_yes


class FakeResult:
    def __init__(self, success=True, order_id="ord-1", shares_bought=0.0, shares_sold=0.0):
        self.success = success
        self.order_id = order_id
        self.shares_bought = shares_bought
        self.shares_sold = shares_sold
        self.error = None


class FakeClient:
    def __init__(self, market=None):
        self.market = market or FakeMarket()
        self.trades = []
        self.cancelled = []
        self.open_orders = {"orders": []}
        self.positions = []

    def get_market_by_id(self, market_id):
        return self.market

    def trade(self, **kw):
        self.trades.append(kw)
        if kw.get("action") == "buy":
            return FakeResult(shares_bought=round(kw["amount"] / kw["price"], 2))
        return FakeResult(shares_sold=kw.get("shares", 0.0))

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"success": True}

    def get_open_orders(self):
        return self.open_orders

    def get_positions(self, venue=None, source=None):
        return self.positions


def run_tick(tmp_path, client, cfg_dict=None, state=None, live=True, at=None):
    cfg_path = tmp_path / "roller_config.json"
    cfg_path.write_text(json.dumps(cfg_dict or example_config_dict()))
    state_path = tmp_path / "roller_state.json"
    if state is not None:
        trader.save_state(state, str(state_path))
    return trader.tick(
        client,
        str(cfg_path),
        str(state_path),
        str(tmp_path / "roller.lock"),
        live=live,
        now=at or NOW,
    )


def test_snap_market_yes_side_passthrough():
    client = FakeClient(FakeMarket(mid=0.80, bid=0.79, ask=0.81))
    s = trader.snap_market(client, "m0", "yes")
    assert s.mid == pytest.approx(0.80)
    assert s.best_bid == pytest.approx(0.79)
    assert s.best_ask == pytest.approx(0.81)


def test_snap_market_no_side_converts_quotes():
    client = FakeClient(FakeMarket(mid=0.80, bid=0.79, ask=0.81))
    s = trader.snap_market(client, "m0", "no")
    assert s.mid == pytest.approx(0.20)
    assert s.best_bid == pytest.approx(0.19)  # 1 - yes_ask
    assert s.best_ask == pytest.approx(0.21)  # 1 - yes_bid


def test_snap_market_no_side_propagates_none():
    client = FakeClient(FakeMarket(mid=0.80, bid=0.79))
    client.market.best_ask = None
    s = trader.snap_market(client, "m0", "no")
    assert s.best_bid is None  # missing yes_ask -> missing no_bid
    assert s.best_ask == pytest.approx(0.21)


def test_snap_market_no_side_keeps_resolved_yes_absolute():
    client = FakeClient(FakeMarket(mid=0.995, status="resolved"))
    s = trader.snap_market(client, "m0", "no")
    assert s.resolved_yes is True


def no_leg_config_dict():
    cfg_dict = example_config_dict()
    for leg in cfg_dict["legs"]:
        leg["side"] = "no"
    return cfg_dict


def test_no_leg_enters_at_no_side_price(tmp_path):
    client = FakeClient(FakeMarket(mid=0.80, bid=0.79, ask=0.81))
    state = run_tick(tmp_path, client, cfg_dict=no_leg_config_dict())
    trade = client.trades[0]
    assert trade["action"] == "buy"
    assert trade["side"] == "no"
    assert trade["price"] == pytest.approx(0.21)  # no mid 0.20 + tolerance
    assert state.shares > 0


def test_no_leg_post_match_winning_sells(tmp_path):
    cfg_dict = no_leg_config_dict()
    cfg = RollerConfig.from_dict(cfg_dict)
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.cash = 0.0
    state.shares = 50.0
    at = cfg.legs[0].expected_end + timedelta(minutes=10)
    client = FakeClient(FakeMarket(mid=0.015, bid=0.01, ask=0.02))
    new_state = run_tick(tmp_path, client, cfg_dict=cfg_dict, state=state, at=at)
    sells = [trade for trade in client.trades if trade["action"] == "sell"]
    assert sells and sells[0]["price"] == pytest.approx(0.98)  # 1 - yes_ask 0.02
    assert new_state.leg_index == 1


def test_no_leg_post_match_losing_busts(tmp_path):
    cfg_dict = no_leg_config_dict()
    cfg = RollerConfig.from_dict(cfg_dict)
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.cash = 0.0
    state.shares = 50.0
    at = cfg.legs[0].expected_end + timedelta(minutes=10)
    client = FakeClient(FakeMarket(mid=0.985, bid=0.97, ask=0.99))
    new_state = run_tick(tmp_path, client, cfg_dict=cfg_dict, state=state, at=at)
    assert new_state.phase == "BUSTED"  # no-side bid 0.01 < loss floor
    assert client.trades == []


def test_first_tick_places_leg1_entry(tmp_path):
    client = FakeClient(FakeMarket(mid=0.40))
    state = run_tick(tmp_path, client)
    trade = client.trades[0]
    assert trade["action"] == "buy"
    assert trade["order_type"] == "GTC"
    assert trade["price"] == pytest.approx(0.41)
    assert trade["market_id"] == "m0"
    assert trade["source"] == trader.TRADE_SOURCE
    assert state.shares > 0
    assert state.phase == "LEG_OPEN"


def test_dry_run_places_nothing(tmp_path):
    client = FakeClient(FakeMarket(mid=0.40))
    state = run_tick(tmp_path, client, live=False)
    assert client.trades == []
    assert state.phase in ("CONFIGURED", "LEG_OPEN")


def test_post_match_sell_and_roll(tmp_path):
    cfg_dict = example_config_dict()
    cfg = RollerConfig.from_dict(cfg_dict)
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.cash = 0.0
    state.shares = 50.0
    at = cfg.legs[0].expected_end + timedelta(minutes=10)
    client = FakeClient(FakeMarket(mid=0.98, bid=0.975))
    new_state = run_tick(tmp_path, client, cfg_dict=cfg_dict, state=state, at=at)
    sells = [trade for trade in client.trades if trade["action"] == "sell"]
    assert sells and sells[0]["shares"] == pytest.approx(50.0)
    assert new_state.leg_index == 1
    assert new_state.cash == pytest.approx(50.0 * 0.975)


def test_busted_terminal(tmp_path):
    cfg_dict = example_config_dict()
    cfg = RollerConfig.from_dict(cfg_dict)
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.cash = 0.0
    state.shares = 50.0
    at = cfg.legs[0].expected_end + timedelta(minutes=10)
    client = FakeClient(FakeMarket(mid=0.02, bid=0.01))
    new_state = run_tick(tmp_path, client, cfg_dict=cfg_dict, state=state, at=at)
    assert new_state.phase == "BUSTED"
    assert client.trades == []


def resting_entry_state(cfg, order_id="ord-1", price=0.41, amount=25.0, placed_at=None):
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.entry_order_id = order_id
    state.entry_placed_at = placed_at or NOW
    state.entry_price = price
    state.entry_amount = amount
    return state


def test_reconcile_treats_absent_entry_order_as_filled(tmp_path):
    """Resting entry gone from the book -> filled: shares held, cash debited once."""
    cfg = RollerConfig.from_dict(example_config_dict())
    state = resting_entry_state(cfg)
    client = FakeClient(FakeMarket(mid=0.40))
    client.open_orders = {"orders": []}  # ord-1 left the book
    new_state = run_tick(tmp_path, client, state=state)
    assert new_state.shares == pytest.approx(round(25.0 / 0.41, 2))
    assert new_state.cash == pytest.approx(0.0)
    assert new_state.entry_order_id is None
    assert client.trades == []  # no second buy


def test_reconcile_leaves_open_entry_order_alone(tmp_path):
    cfg = RollerConfig.from_dict(example_config_dict())
    state = resting_entry_state(cfg)
    client = FakeClient(FakeMarket(mid=0.40))
    client.open_orders = {"orders": [{"order_id": "ord-1"}]}
    new_state = run_tick(tmp_path, client, state=state)
    assert new_state.shares == 0.0
    assert new_state.entry_order_id == "ord-1"
    assert client.trades == []


def test_reconcile_skipped_when_get_open_orders_raises(tmp_path):
    cfg = RollerConfig.from_dict(example_config_dict())
    state = resting_entry_state(cfg)

    class RaisingClient(FakeClient):
        def get_open_orders(self):
            raise OSError("api down")

    client = RaisingClient(FakeMarket(mid=0.40))
    new_state = run_tick(tmp_path, client, state=state)
    assert new_state.shares == 0.0  # no fill assumed
    assert new_state.entry_order_id == "ord-1"
    assert client.trades == []


def test_cancel_order_failure_retains_entry_order_id(tmp_path):
    cfg = RollerConfig.from_dict(example_config_dict())
    # placed long enough ago that decide() wants to cancel
    state = resting_entry_state(cfg, placed_at=NOW - timedelta(seconds=300))

    class CancelFailClient(FakeClient):
        def cancel_order(self, order_id):
            raise OSError("cancel failed")

    client = CancelFailClient(FakeMarket(mid=0.40))
    client.open_orders = {"orders": [{"order_id": "ord-1"}]}  # still on the book
    new_state = run_tick(tmp_path, client, state=state)
    assert new_state.entry_order_id == "ord-1"  # kept for next tick's reconcile
    assert client.trades == []  # must not re-enter while order may be live


def test_cancel_nonsuccess_result_retains_entry_order_id(tmp_path):
    """cancel_order returning {"success": False} (no exception) is NOT a confirmed cancel."""
    cfg = RollerConfig.from_dict(example_config_dict())
    state = resting_entry_state(cfg, placed_at=NOW - timedelta(seconds=300))

    class CancelNotOkClient(FakeClient):
        def cancel_order(self, order_id):
            self.cancelled.append(order_id)
            return {"success": False, "warning": "order likely already filled"}

    client = CancelNotOkClient(FakeMarket(mid=0.40))
    client.open_orders = {"orders": [{"order_id": "ord-1"}]}  # still on the book
    new_state = run_tick(tmp_path, client, state=state)
    assert new_state.entry_order_id == "ord-1"  # kept for reconcile
    assert new_state.cancel_failures == 1
    assert new_state.phase == "LEG_OPEN"  # not paused yet
    assert client.trades == []  # must not re-enter while order may be live


def test_cancel_unconfirmed_three_strikes_pauses(tmp_path):
    cfg = RollerConfig.from_dict(example_config_dict())
    state = resting_entry_state(cfg, placed_at=NOW - timedelta(seconds=300))
    state.cancel_failures = 2  # two prior consecutive failures

    class CancelNotOkClient(FakeClient):
        def cancel_order(self, order_id):
            return {"success": False}

    client = CancelNotOkClient(FakeMarket(mid=0.40))
    client.open_orders = {"orders": [{"order_id": "ord-1"}]}
    new_state = run_tick(tmp_path, client, state=state)
    assert new_state.cancel_failures == 3
    assert new_state.phase == "PAUSED"
    assert new_state.entry_order_id == "ord-1"  # order may still be live; keep it
    assert client.trades == []


def test_confirmed_cancel_resets_cancel_failures(tmp_path):
    cfg = RollerConfig.from_dict(example_config_dict())
    state = resting_entry_state(cfg, placed_at=NOW - timedelta(seconds=300))
    state.cancel_failures = 2
    client = FakeClient(FakeMarket(mid=0.40))  # cancel_order returns {"success": True}
    client.open_orders = {"orders": [{"order_id": "ord-1"}]}
    new_state = run_tick(tmp_path, client, state=state)
    assert new_state.cancel_failures == 0
    assert new_state.phase != "PAUSED"
    assert "ord-1" in client.cancelled


def test_cancel_exception_increments_cancel_failures(tmp_path):
    cfg = RollerConfig.from_dict(example_config_dict())
    state = resting_entry_state(cfg, placed_at=NOW - timedelta(seconds=300))

    class CancelFailClient(FakeClient):
        def cancel_order(self, order_id):
            raise OSError("cancel failed")

    client = CancelFailClient(FakeMarket(mid=0.40))
    client.open_orders = {"orders": [{"order_id": "ord-1"}]}
    new_state = run_tick(tmp_path, client, state=state)
    assert new_state.cancel_failures == 1
    assert new_state.entry_order_id == "ord-1"


def test_reconciled_entry_fill_resets_cancel_failures(tmp_path):
    cfg = RollerConfig.from_dict(example_config_dict())
    state = resting_entry_state(cfg)
    state.cancel_failures = 2
    client = FakeClient(FakeMarket(mid=0.40))
    client.open_orders = {"orders": []}  # ord-1 left the book -> reconciled fill
    new_state = run_tick(tmp_path, client, state=state)
    assert new_state.shares > 0
    assert new_state.cancel_failures == 0


def test_reconcile_treats_absent_exit_order_as_filled(tmp_path):
    cfg_dict = example_config_dict()
    cfg = RollerConfig.from_dict(cfg_dict)
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.cash = 0.0
    state.shares = 50.0
    state.exit_order_id = "ord-9"
    state.exit_price = 0.975
    at = cfg.legs[0].expected_end + timedelta(minutes=10)
    client = FakeClient(FakeMarket(mid=None, bid=None, ask=None))
    client.open_orders = {"orders": []}  # ord-9 left the book
    new_state = run_tick(tmp_path, client, cfg_dict=cfg_dict, state=state, at=at)
    assert new_state.leg_index == 1  # proceeds applied, leg advanced
    assert new_state.cash == pytest.approx(round(50.0 * 0.975, 6))
    assert new_state.shares == 0.0
    assert new_state.exit_order_id is None
    assert client.trades == []


def test_resting_exit_is_not_replaced(tmp_path):
    """Working exit order + bid still above threshold -> no second sell."""
    cfg_dict = example_config_dict()
    cfg = RollerConfig.from_dict(cfg_dict)
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.cash = 0.0
    state.shares = 50.0
    state.exit_order_id = "ord-9"
    state.exit_price = 0.975
    at = cfg.legs[0].expected_end + timedelta(minutes=10)
    client = FakeClient(FakeMarket(mid=0.98, bid=0.975))
    client.open_orders = {"orders": [{"order_id": "ord-9"}]}  # still working
    new_state = run_tick(tmp_path, client, cfg_dict=cfg_dict, state=state, at=at)
    assert client.trades == []  # decide waits; reconcile owns the fill
    assert new_state.exit_order_id == "ord-9"
    assert new_state.leg_index == 0


def test_partial_exit_fill_accumulates_and_keeps_settling(tmp_path):
    """0 < sold < requested: reduce shares, bank proceeds, do NOT advance the leg."""
    cfg_dict = example_config_dict()
    cfg = RollerConfig.from_dict(cfg_dict)
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.cash = 0.0
    state.shares = 50.0
    at = cfg.legs[0].expected_end + timedelta(minutes=10)

    class PartialFillClient(FakeClient):
        def trade(self, **kw):
            self.trades.append(kw)
            if kw.get("action") == "sell":
                return FakeResult(order_id="ord-7", shares_sold=20.0)
            return FakeResult(shares_bought=round(kw["amount"] / kw["price"], 2))

    client = PartialFillClient(FakeMarket(mid=0.98, bid=0.975))
    new_state = run_tick(tmp_path, client, cfg_dict=cfg_dict, state=state, at=at)
    assert new_state.shares == pytest.approx(30.0)
    assert new_state.cash == pytest.approx(20.0 * 0.975)
    assert new_state.leg_index == 0  # remainder still settling
    assert new_state.exit_order_id == "ord-7"  # remainder rests; reconcile owns it
    assert new_state.exit_price == pytest.approx(0.975)


def test_followup_full_fill_advances_with_total_proceeds(tmp_path):
    """After a partial, the resting remainder filling advances with TOTAL proceeds."""
    cfg_dict = example_config_dict()
    cfg = RollerConfig.from_dict(cfg_dict)
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.cash = round(20.0 * 0.975, 6)  # accumulated partial proceeds
    state.shares = 30.0
    state.exit_order_id = "ord-7"
    state.exit_price = 0.975
    at = cfg.legs[0].expected_end + timedelta(minutes=20)
    client = FakeClient(FakeMarket(mid=0.98, bid=0.975))
    client.open_orders = {"orders": []}  # ord-7 left the book -> remainder filled
    new_state = run_tick(tmp_path, client, cfg_dict=cfg_dict, state=state, at=at)
    assert new_state.leg_index == 1
    assert new_state.exit_order_id is None
    # The same tick then enters leg 2, so TOTAL proceeds show up as the entry size.
    buys = [trade for trade in client.trades if trade["action"] == "buy"]
    assert buys and buys[0]["amount"] == pytest.approx(round(50.0 * 0.975, 2))
    assert buys[0]["market_id"] == "m1"


def won_resolution_setup(cfg_dict=None):
    """Held leg 1, market resolved in our favor."""
    cfg = RollerConfig.from_dict(cfg_dict or example_config_dict())
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.cash = 0.0
    state.shares = 50.0
    at = cfg.legs[0].expected_end + timedelta(minutes=10)
    client = FakeClient(FakeMarket(mid=None, bid=None, ask=None, status="resolved", resolved_yes=True))
    return cfg, state, client, at


def test_settle_won_waits_while_position_unredeemed(tmp_path, capsys):
    """Resolution alone must not credit cash - the venue still shows the position."""
    cfg, state, client, at = won_resolution_setup()
    client.positions = [{"market_id": "m0", "shares_yes": 50.0, "shares_no": 0.0}]
    new_state = run_tick(tmp_path, client, state=state, at=at)
    assert new_state.cash == pytest.approx(0.0)  # no phantom credit
    assert new_state.shares == pytest.approx(50.0)
    assert new_state.leg_index == 0
    assert new_state.phase == "LEG_OPEN"
    assert "awaiting redemption" in capsys.readouterr().out


def test_settle_won_credits_once_position_redeemed(tmp_path):
    cfg, state, client, at = won_resolution_setup()
    client.positions = []  # position gone -> redeemed
    new_state = run_tick(tmp_path, client, state=state, at=at)
    assert new_state.cash == pytest.approx(50.0)  # shares * 1.0
    assert new_state.shares == 0.0
    assert new_state.leg_index == 1
    assert new_state.phase == "LEG_OPEN"


def test_settle_won_checks_held_side_shares(tmp_path):
    """A NO leg: shares_yes lingering at 0 but shares_no still held -> wait."""
    cfg_dict = no_leg_config_dict()
    cfg, state, client, at = won_resolution_setup(cfg_dict)
    client.market.resolved_yes = False  # NO leg won
    client.positions = [{"market_id": "m0", "shares_yes": 0.0, "shares_no": 50.0}]
    new_state = run_tick(tmp_path, client, cfg_dict=cfg_dict, state=state, at=at)
    assert new_state.cash == pytest.approx(0.0)
    assert new_state.leg_index == 0


def test_settle_won_waits_when_get_positions_raises(tmp_path, capsys):
    cfg, state, client, at = won_resolution_setup()

    class RaisingPositionsClient(FakeClient):
        def get_positions(self, venue=None, source=None):
            raise OSError("positions api down")

    client = RaisingPositionsClient(
        FakeMarket(mid=None, bid=None, ask=None, status="resolved", resolved_yes=True)
    )
    new_state = run_tick(tmp_path, client, state=state, at=at)
    assert new_state.cash == pytest.approx(0.0)
    assert new_state.shares == pytest.approx(50.0)
    assert new_state.leg_index == 0
    assert "warn" in capsys.readouterr().out


def test_settle_won_unredeemed_stale_pauses(tmp_path):
    """Resolved in our favor but unredeemed >STALE_RESOLUTION_HOURS -> PAUSED, not forever-wait."""
    cfg, state, client, at = won_resolution_setup()
    client.positions = [{"market_id": "m0", "shares_yes": 50.0, "shares_no": 0.0}]
    stale_at = cfg.legs[0].expected_end + timedelta(hours=6, minutes=1)
    new_state = run_tick(tmp_path, client, state=state, at=stale_at)
    assert new_state.phase == "PAUSED"
    assert new_state.cash == pytest.approx(0.0)  # still no phantom credit
    assert new_state.shares == pytest.approx(50.0)


def test_tick_guards_leg_index_out_of_range(tmp_path, capsys):
    cfg = RollerConfig.from_dict(example_config_dict())
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.leg_index = 5  # beyond the 2-leg config
    client = FakeClient(FakeMarket(mid=0.40))
    new_state = run_tick(tmp_path, client, state=state)  # must not traceback
    assert new_state.leg_index == 5
    assert client.trades == []
    assert "leg_index" in capsys.readouterr().out


def test_combo_compare_degrades(monkeypatch):
    monkeypatch.setattr(
        trader.requests,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(OSError("down")),
    )
    assert ORIG_FETCH_COMBO(["m0", "m1"]) is None


def test_first_tick_prints_combo_implied_price(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(trader, "fetch_combo_comparison", lambda market_ids: 0.18)
    client = FakeClient(FakeMarket(mid=0.40))
    run_tick(tmp_path, client, live=False)
    out = capsys.readouterr().out
    assert "combo-implied" in out
    assert "0.18" in out


def test_first_tick_prints_product_when_combo_unavailable(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(trader, "fetch_combo_comparison", lambda market_ids: None)
    client = FakeClient(FakeMarket(mid=0.40))
    run_tick(tmp_path, client, live=False)
    out = capsys.readouterr().out
    assert "combo comparison unavailable" in out
    assert "0.16" in out  # 0.40 * 0.40 leg-product


def test_combo_comparison_runs_only_at_streak_start(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(trader, "fetch_combo_comparison", lambda market_ids: calls.append(1) or None)
    client = FakeClient(FakeMarket(mid=0.40))
    run_tick(tmp_path, client, live=False)
    assert len(calls) == 1
    # second tick: state file exists now, no re-fetch
    run_tick(tmp_path, client, live=False)
    assert len(calls) == 1


def test_combo_comparison_never_breaks_the_tick(tmp_path, monkeypatch):
    def boom(market_ids):
        raise OSError("combo api down")

    monkeypatch.setattr(trader, "fetch_combo_comparison", boom)
    client = FakeClient(FakeMarket(mid=0.40))
    state = run_tick(tmp_path, client, live=False)  # must not raise
    assert state is not None


def test_status_prints_history(tmp_path, capsys):
    cfg = RollerConfig.from_dict(example_config_dict())
    state = StreakState.fresh(cfg)
    state.log("hello-marker")
    trader.save_state(state, str(tmp_path / "roller_state.json"))
    trader.print_status(str(tmp_path / "roller_state.json"))
    out = capsys.readouterr().out
    assert "CONFIGURED" in out and "hello-marker" in out


def test_abort_cancels_and_sells(tmp_path):
    cfg_dict = example_config_dict()
    cfg = RollerConfig.from_dict(cfg_dict)
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.shares = 40.0
    state.cash = 0.0
    state.entry_order_id = "ord-x"
    trader.save_state(state, str(tmp_path / "roller_state.json"))
    cfg_path = tmp_path / "roller_config.json"
    cfg_path.write_text(json.dumps(cfg_dict))
    client = FakeClient(FakeMarket(mid=0.6, bid=0.58))
    new_state = trader.abort(
        client, str(cfg_path), str(tmp_path / "roller_state.json"), live=True, venue="polymarket"
    )
    assert "ord-x" in client.cancelled
    sells = [trade for trade in client.trades if trade["action"] == "sell"]
    assert sells and sells[0]["price"] == pytest.approx(0.58)
    assert new_state.phase == "BANKED"


def test_abort_dry_run_does_not_terminalize_state(tmp_path):
    cfg_dict = example_config_dict()
    cfg = RollerConfig.from_dict(cfg_dict)
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.shares = 40.0
    state.cash = 0.0
    state.entry_order_id = "ord-x"
    state_path = tmp_path / "roller_state.json"
    trader.save_state(state, str(state_path))
    before = state_path.read_text()
    cfg_path = tmp_path / "roller_config.json"
    cfg_path.write_text(json.dumps(cfg_dict))

    class CountingClient(FakeClient):
        def __init__(self, market=None):
            super().__init__(market)
            self.market_lookups = 0

        def get_market_by_id(self, market_id):
            self.market_lookups += 1
            return super().get_market_by_id(market_id)

    client = CountingClient(FakeMarket(mid=0.6, bid=0.58))
    new_state = trader.abort(
        client, str(cfg_path), str(state_path), live=False, venue="polymarket"
    )
    assert new_state.phase == "LEG_OPEN"  # not mutated
    assert new_state.entry_order_id == "ord-x"
    assert state_path.read_text() == before  # state file untouched
    assert client.trades == []
    assert client.cancelled == []
    assert client.market_lookups == 0


def abort_state(cfg, entry_order_id="ord-x", exit_order_id=None, shares=40.0):
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.shares = shares
    state.cash = 0.0
    state.entry_order_id = entry_order_id
    state.exit_order_id = exit_order_id
    return state


def run_abort(tmp_path, client, state, cfg_dict=None):
    cfg_dict = cfg_dict or example_config_dict()
    state_path = tmp_path / "roller_state.json"
    trader.save_state(state, str(state_path))
    cfg_path = tmp_path / "roller_config.json"
    cfg_path.write_text(json.dumps(cfg_dict))
    return trader.abort(client, str(cfg_path), str(state_path), live=True, venue="polymarket")


def test_abort_cancel_exception_pauses_and_skips_sell(tmp_path):
    """A raising cancel must not terminalize to BANKED nor sell held shares."""
    cfg = RollerConfig.from_dict(example_config_dict())

    class CancelFailClient(FakeClient):
        def cancel_order(self, order_id):
            raise OSError("cancel api down")

    client = CancelFailClient(FakeMarket(mid=0.6, bid=0.58))
    new_state = run_abort(tmp_path, client, abort_state(cfg))
    assert new_state.phase == "PAUSED"
    assert new_state.entry_order_id == "ord-x"  # kept: order may still be live
    assert new_state.shares == pytest.approx(40.0)
    assert client.trades == []  # no sell while an unknown order is live


def test_abort_cancel_nonsuccess_result_pauses_and_skips_sell(tmp_path):
    """cancel_order returning success=False (no exception) is also a failed cancel."""
    cfg = RollerConfig.from_dict(example_config_dict())

    class CancelNotOkClient(FakeClient):
        def cancel_order(self, order_id):
            return {"success": False, "warning": "not_canceled"}

    client = CancelNotOkClient(FakeMarket(mid=0.6, bid=0.58))
    new_state = run_abort(tmp_path, client, abort_state(cfg))
    assert new_state.phase == "PAUSED"
    assert new_state.entry_order_id == "ord-x"
    assert client.trades == []


def test_abort_partial_cancel_failure_keeps_only_failed_order(tmp_path):
    """Entry cancel confirmed, exit cancel failed -> entry id cleared, exit kept, PAUSED."""
    cfg = RollerConfig.from_dict(example_config_dict())

    class ExitCancelFailClient(FakeClient):
        def cancel_order(self, order_id):
            self.cancelled.append(order_id)
            if order_id == "ord-exit":
                return {"success": False}
            return {"success": True}

    client = ExitCancelFailClient(FakeMarket(mid=0.6, bid=0.58))
    state = abort_state(cfg, entry_order_id="ord-x", exit_order_id="ord-exit")
    new_state = run_abort(tmp_path, client, state)
    assert new_state.phase == "PAUSED"
    assert new_state.entry_order_id is None  # confirmed cancelled
    assert new_state.exit_order_id == "ord-exit"  # unconfirmed: kept
    assert client.trades == []


def test_abort_uses_cli_venue(tmp_path):
    cfg_dict = example_config_dict()
    cfg = RollerConfig.from_dict(cfg_dict)
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.shares = 40.0
    state.cash = 0.0
    trader.save_state(state, str(tmp_path / "roller_state.json"))
    cfg_path = tmp_path / "roller_config.json"
    cfg_path.write_text(json.dumps(cfg_dict))
    client = FakeClient(FakeMarket(mid=0.6, bid=0.58))
    trader.abort(
        client, str(cfg_path), str(tmp_path / "roller_state.json"), live=True, venue="sim"
    )
    sells = [trade for trade in client.trades if trade["action"] == "sell"]
    assert sells and sells[0]["venue"] == "sim"
