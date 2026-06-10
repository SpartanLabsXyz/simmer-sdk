"""Trader I/O tests: state persistence, locking, and fake-client execution."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import parlay_roller_trader as trader
from parlay_roller import RollerConfig, StreakState

UTC = timezone.utc
NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


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


def test_combo_compare_degrades(monkeypatch):
    monkeypatch.setattr(
        trader.requests,
        "get",
        lambda *a, **k: (_ for _ in ()).throw(OSError("down")),
    )
    assert trader.fetch_combo_comparison(["m0", "m1"]) is None


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
    new_state = trader.abort(client, str(cfg_path), str(tmp_path / "roller_state.json"), live=True)
    assert "ord-x" in client.cancelled
    sells = [trade for trade in client.trades if trade["action"] == "sell"]
    assert sells and sells[0]["price"] == pytest.approx(0.58)
    assert new_state.phase == "BANKED"
