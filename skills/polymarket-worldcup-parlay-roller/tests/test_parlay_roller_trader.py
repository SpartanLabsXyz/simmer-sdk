"""Trader I/O tests: state persistence, locking, and fake-client execution."""

import json
from datetime import datetime, timedelta, timezone

import pytest

import parlay_roller_trader as trader
from parlay_roller import Action, RollerConfig, StreakState

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


def test_dry_run_cancel_entry_keeps_order_state(tmp_path, capsys):
    """A dry-run cancel must NEVER clear order-tracking state - the order is
    (or may be) live on-venue and only a venue-confirmed cancel may clear it."""
    cfg = RollerConfig.from_dict(example_config_dict())
    state = resting_entry_state(cfg)
    client = FakeClient(FakeMarket(mid=0.40))
    action = Action("cancel_entry", order_id="ord-1", reason="entry TTL expired")
    trader.execute(client, action, state, cfg, 0, live=False, venue="polymarket", now=NOW)
    assert state.entry_order_id == "ord-1"
    assert state.entry_placed_at is not None
    assert state.entry_price == pytest.approx(0.41)
    assert state.entry_amount == pytest.approx(25.0)
    assert client.cancelled == []
    assert "DRY-RUN would cancel" in capsys.readouterr().out


def test_dry_tick_on_live_streak_is_read_only(tmp_path, capsys):
    """live_streak=True + live=False: decide-only. State file stays
    byte-identical, no reconcile fills, no orders, no cancels."""
    cfg = RollerConfig.from_dict(example_config_dict())
    state = resting_entry_state(cfg, placed_at=NOW - timedelta(seconds=300))
    state.live_streak = True
    cfg_path = tmp_path / "roller_config.json"
    cfg_path.write_text(json.dumps(example_config_dict()))
    state_path = tmp_path / "roller_state.json"
    trader.save_state(state, str(state_path))
    before = state_path.read_text()
    client = FakeClient(FakeMarket(mid=0.40))
    client.open_orders = {"orders": []}  # reconcile would normally infer a fill
    new_state = trader.tick(
        client, str(cfg_path), str(state_path), str(tmp_path / "roller.lock"),
        live=False, now=NOW,
    )
    assert state_path.read_text() == before  # byte-identical
    assert client.trades == []
    assert client.cancelled == []
    assert new_state.entry_order_id == "ord-1"  # reconcile did not run
    assert new_state.shares == 0.0
    out = capsys.readouterr().out
    assert "read-only" in out
    assert "would cancel_entry" in out  # the decision is still printed


def test_live_tick_flips_live_streak_flag_and_persists(tmp_path):
    client = FakeClient(FakeMarket(mid=0.40))
    state = run_tick(tmp_path, client, live=True)
    assert state.live_streak is True
    loaded = trader.load_state(str(tmp_path / "roller_state.json"))
    assert loaded.live_streak is True


def test_dry_streak_keeps_simulating_and_saving(tmp_path):
    """A never-live streak (live_streak=False) keeps current dry-run behavior:
    its own simulated state may mutate and save."""
    client = FakeClient(FakeMarket(mid=0.40))
    state = run_tick(tmp_path, client, live=False)
    assert state.live_streak is False
    loaded = trader.load_state(str(tmp_path / "roller_state.json"))
    assert loaded is not None  # dry streak still saves
    assert loaded.live_streak is False
    assert client.trades == []


def test_live_abort_marks_streak_live(tmp_path):
    cfg = RollerConfig.from_dict(example_config_dict())
    client = FakeClient(FakeMarket(mid=0.6, bid=0.58))
    new_state = run_abort(tmp_path, client, abort_state(cfg, entry_order_id=None))
    assert new_state.live_streak is True


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


class PartialEntryClient(FakeClient):
    """Buy returns a partial fill (20 sh) with the remainder resting as ord-2."""

    def __init__(self, market=None, order_id="ord-2"):
        super().__init__(market)
        self.partial_order_id = order_id

    def trade(self, **kw):
        self.trades.append(kw)
        if kw.get("action") == "buy":
            return FakeResult(order_id=self.partial_order_id, shares_bought=20.0)
        return FakeResult(shares_sold=kw.get("shares", 0.0))


def test_partial_entry_fill_tracks_residual(tmp_path):
    """shares_bought < expected AND order_id -> partial entry: credit the fill,
    keep the order id with entry_amount/entry_price set to the residual."""
    client = PartialEntryClient(FakeMarket(mid=0.40))  # entry price 0.41, amount 25
    state = run_tick(tmp_path, client)
    assert state.phase == "LEG_OPEN"
    assert state.shares == pytest.approx(20.0)
    assert state.cash == pytest.approx(25.0 - 20.0 * 0.41)
    assert state.entry_order_id == "ord-2"
    assert state.entry_price == pytest.approx(0.41)
    assert state.entry_amount == pytest.approx(25.0 - 8.2)  # residual, not full amount
    assert len(client.trades) == 1  # no second buy


def test_partial_entry_fill_without_order_id_pauses(tmp_path):
    client = PartialEntryClient(FakeMarket(mid=0.40), order_id=None)
    state = run_tick(tmp_path, client)
    assert state.phase == "PAUSED"
    assert state.shares == pytest.approx(20.0)
    assert state.cash == pytest.approx(25.0 - 8.2)
    assert state.entry_order_id is None


def test_reconcile_credits_only_residual_after_partial_entry(tmp_path):
    """When the residual order later leaves the book, reconcile adds ONLY the
    residual shares (entry_amount/entry_price math) to the partial credit."""
    cfg = RollerConfig.from_dict(example_config_dict())
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.shares = 20.0
    state.cash = 16.8
    state.entry_order_id = "ord-2"
    state.entry_placed_at = NOW
    state.entry_price = 0.41
    state.entry_amount = 16.8  # residual exposure
    client = FakeClient(FakeMarket(mid=0.40))
    client.open_orders = {"orders": []}  # ord-2 left the book -> residual filled
    new_state = run_tick(tmp_path, client, state=state)
    assert new_state.shares == pytest.approx(20.0 + round(16.8 / 0.41, 2))
    assert new_state.cash == pytest.approx(0.0)
    assert new_state.entry_order_id is None
    assert client.trades == []  # no double buy


def test_partial_entry_then_window_close_cancels_residual_only(tmp_path):
    """Holding partial shares + working residual past TTL: cancel the order,
    keep the shares, and do NOT place another entry or bank_and_stop."""
    cfg = RollerConfig.from_dict(example_config_dict())
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.shares = 20.0
    state.cash = 16.8
    state.entry_order_id = "ord-2"
    state.entry_placed_at = NOW - timedelta(seconds=300)  # past TTL
    state.entry_price = 0.41
    state.entry_amount = 16.8
    client = FakeClient(FakeMarket(mid=0.40))
    client.open_orders = {"orders": [{"order_id": "ord-2"}]}  # still on the book
    new_state = run_tick(tmp_path, client, state=state)
    assert "ord-2" in client.cancelled
    assert new_state.entry_order_id is None
    assert new_state.shares == pytest.approx(20.0)  # held shares untouched
    assert new_state.phase == "LEG_OPEN"
    assert client.trades == []  # no re-entry, no sell


class PositionsDownClient(FakeClient):
    """cancel_order confirms, but get_positions is unavailable."""

    def get_positions(self, venue=None, source=None):
        raise OSError("positions api down")


def test_ttl_cancel_credits_fill_that_landed_while_resting(tmp_path):
    """An entry can PARTIALLY fill while it rests (id still in the open book,
    so reconcile skips it). A confirmed TTL cancel kills only the residual -
    the filled shares must be venue-verified and credited, cash debited once
    at the resting limit, and no re-entry placed with the spent funds."""
    cfg = RollerConfig.from_dict(example_config_dict())
    state = resting_entry_state(cfg, placed_at=NOW - timedelta(seconds=300))
    client = FakeClient(FakeMarket(mid=0.40))
    client.open_orders = {"orders": [{"order_id": "ord-1"}]}  # still on the book
    client.positions = [{"market_id": "m0", "shares_yes": 12.0, "shares_no": 0.0}]
    new_state = run_tick(tmp_path, client, state=state)
    assert "ord-1" in client.cancelled
    assert new_state.shares == pytest.approx(12.0)
    assert new_state.cash == pytest.approx(25.0 - 12.0 * 0.41)  # debited once
    assert new_state.entry_order_id is None
    assert new_state.phase == "LEG_OPEN"
    assert client.trades == []  # no re-entry overspend


def test_ttl_cancel_after_partial_credits_only_the_delta(tmp_path):
    """Partial already credited 20 sh; 6 more filled while the residual rested.
    The confirmed cancel must credit ONLY the 6-share delta."""
    cfg = RollerConfig.from_dict(example_config_dict())
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.shares = 20.0
    state.cash = 16.8
    state.entry_order_id = "ord-2"
    state.entry_placed_at = NOW - timedelta(seconds=300)  # past TTL
    state.entry_price = 0.41
    state.entry_amount = 16.8
    client = FakeClient(FakeMarket(mid=0.40))
    client.open_orders = {"orders": [{"order_id": "ord-2"}]}
    client.positions = [{"market_id": "m0", "shares_yes": 26.0, "shares_no": 0.0}]
    new_state = run_tick(tmp_path, client, state=state)
    assert new_state.shares == pytest.approx(26.0)
    assert new_state.cash == pytest.approx(round(16.8 - 6.0 * 0.41, 6))
    assert new_state.entry_order_id is None
    assert client.trades == []


def test_ttl_cancel_verification_failure_keeps_order_id(tmp_path, capsys):
    """Cancel confirmed but get_positions down: holdings are UNKNOWN, so
    tracking must NOT be cleared - the next tick retries."""
    cfg = RollerConfig.from_dict(example_config_dict())
    state = resting_entry_state(cfg, placed_at=NOW - timedelta(seconds=300))
    client = PositionsDownClient(FakeMarket(mid=0.40))
    client.open_orders = {"orders": [{"order_id": "ord-1"}]}
    new_state = run_tick(tmp_path, client, state=state)
    assert "ord-1" in client.cancelled  # the cancel itself was confirmed
    assert new_state.entry_order_id == "ord-1"  # kept: fill unknown
    assert new_state.shares == 0.0
    assert new_state.cash == pytest.approx(25.0)
    assert new_state.phase == "LEG_OPEN"  # not terminal; next tick retries
    assert client.trades == []
    assert "fill verification unavailable" in capsys.readouterr().out


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


def settling_state(cfg, shares=50.0):
    state = StreakState.fresh(cfg)
    state.phase = "LEG_OPEN"
    state.cash = 0.0
    state.shares = shares
    return state


class SellResultClient(FakeClient):
    """Sell returns a fixed result; buys behave like FakeClient."""

    def __init__(self, market=None, sell_result=None):
        super().__init__(market)
        self.sell_result = sell_result

    def trade(self, **kw):
        self.trades.append(kw)
        if kw.get("action") == "sell":
            return self.sell_result
        return FakeResult(shares_bought=round(kw["amount"] / kw["price"], 2))


def test_partial_exit_fill_without_order_id_pauses(tmp_path):
    """0 < sold < requested AND no order id for the remainder: mirror the
    entry path - credit the partial proceeds, then PAUSE (untrackable)."""
    cfg_dict = example_config_dict()
    cfg = RollerConfig.from_dict(cfg_dict)
    state = settling_state(cfg)
    at = cfg.legs[0].expected_end + timedelta(minutes=10)
    client = SellResultClient(
        FakeMarket(mid=0.98, bid=0.975),
        sell_result=FakeResult(order_id=None, shares_sold=20.0),
    )
    new_state = run_tick(tmp_path, client, cfg_dict=cfg_dict, state=state, at=at)
    assert new_state.phase == "PAUSED"
    assert new_state.shares == pytest.approx(30.0)
    assert new_state.cash == pytest.approx(20.0 * 0.975)  # partial proceeds kept
    assert new_state.exit_order_id is None
    assert new_state.leg_index == 0  # never advanced
    assert "untrackable" in new_state.history[-1]["msg"]


def test_exit_zero_fill_without_order_id_pauses(tmp_path):
    """success=True, zero fill, no order id: the sell may be live on-venue but
    cannot be tracked - PAUSE instead of re-selling next tick (double-sell)."""
    cfg_dict = example_config_dict()
    cfg = RollerConfig.from_dict(cfg_dict)
    state = settling_state(cfg)
    at = cfg.legs[0].expected_end + timedelta(minutes=10)
    client = SellResultClient(
        FakeMarket(mid=0.98, bid=0.975),
        sell_result=FakeResult(order_id=None, shares_sold=0.0),
    )
    new_state = run_tick(tmp_path, client, cfg_dict=cfg_dict, state=state, at=at)
    assert new_state.phase == "PAUSED"
    assert new_state.shares == pytest.approx(50.0)
    assert new_state.cash == pytest.approx(0.0)
    assert new_state.exit_order_id is None
    assert "untrackable" in new_state.history[-1]["msg"]


def test_entry_zero_fill_without_order_id_pauses(tmp_path):
    """Same hazard on the buy side: an accepted-but-unfilled entry with no
    order id would be re-placed next tick (double-buy). PAUSE instead."""

    class BuyNoIdClient(FakeClient):
        def trade(self, **kw):
            self.trades.append(kw)
            if kw.get("action") == "buy":
                return FakeResult(order_id=None, shares_bought=0.0)
            return FakeResult(shares_sold=kw.get("shares", 0.0))

    client = BuyNoIdClient(FakeMarket(mid=0.40))
    state = run_tick(tmp_path, client)
    assert state.phase == "PAUSED"
    assert state.shares == 0.0
    assert state.cash == pytest.approx(25.0)  # nothing debited
    assert state.entry_order_id is None
    assert "untrackable" in state.history[-1]["msg"]


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


class AbortSellClient(FakeClient):
    """Sell returns a configurable result; buys behave like FakeClient."""

    def __init__(self, market=None, sell_result=None):
        super().__init__(market)
        self.sell_result = sell_result

    def trade(self, **kw):
        self.trades.append(kw)
        if kw.get("action") == "sell" and self.sell_result is not None:
            return self.sell_result
        if kw.get("action") == "buy":
            return FakeResult(shares_bought=round(kw["amount"] / kw["price"], 2))
        return FakeResult(shares_sold=kw.get("shares", 0.0))


def test_abort_sell_rejected_pauses_not_banked(tmp_path):
    cfg = RollerConfig.from_dict(example_config_dict())
    fail = FakeResult(success=False, order_id=None)
    fail.error = "insufficient balance"
    client = AbortSellClient(FakeMarket(mid=0.6, bid=0.58), sell_result=fail)
    new_state = run_abort(tmp_path, client, abort_state(cfg, entry_order_id=None))
    assert new_state.phase == "PAUSED"
    assert new_state.shares == pytest.approx(40.0)  # nothing credited
    assert new_state.cash == pytest.approx(0.0)
    assert "abort sell rejected" in new_state.history[-1]["msg"]


def test_abort_sell_resting_records_order_and_pauses(tmp_path):
    """success + zero fill + order_id: the sell rests - track it, PAUSE, never BANK."""
    cfg = RollerConfig.from_dict(example_config_dict())
    resting = FakeResult(success=True, order_id="ord-sell", shares_sold=0.0)
    client = AbortSellClient(FakeMarket(mid=0.6, bid=0.58), sell_result=resting)
    new_state = run_abort(tmp_path, client, abort_state(cfg, entry_order_id=None))
    assert new_state.phase == "PAUSED"
    assert new_state.shares == pytest.approx(40.0)
    assert new_state.cash == pytest.approx(0.0)
    assert new_state.exit_order_id == "ord-sell"
    assert new_state.exit_price == pytest.approx(0.58)
    assert "abort sell working" in new_state.history[-1]["msg"]


def test_abort_sell_partial_fill_credits_partial_and_pauses(tmp_path):
    cfg = RollerConfig.from_dict(example_config_dict())
    partial = FakeResult(success=True, order_id="ord-sell", shares_sold=15.0)
    client = AbortSellClient(FakeMarket(mid=0.6, bid=0.58), sell_result=partial)
    new_state = run_abort(tmp_path, client, abort_state(cfg, entry_order_id=None))
    assert new_state.phase == "PAUSED"
    assert new_state.shares == pytest.approx(25.0)
    assert new_state.cash == pytest.approx(round(15.0 * 0.58, 6))
    assert new_state.exit_order_id == "ord-sell"


def test_abort_no_bid_pauses_not_banked(tmp_path):
    """No bid -> shares cannot be disposed; PAUSED (previously BANKED while still holding)."""
    cfg = RollerConfig.from_dict(example_config_dict())
    client = FakeClient(FakeMarket(mid=None, bid=None, ask=None))
    new_state = run_abort(tmp_path, client, abort_state(cfg, entry_order_id=None))
    assert new_state.phase == "PAUSED"
    assert new_state.shares == pytest.approx(40.0)
    assert client.trades == []  # no sell placed without a price
    assert "no bid available" in new_state.history[-1]["msg"]


def test_abort_full_fill_banks(tmp_path):
    """Venue-confirmed full disposal: no shares, no working orders -> BANKED."""
    cfg = RollerConfig.from_dict(example_config_dict())
    client = FakeClient(FakeMarket(mid=0.6, bid=0.58))  # sells fill in full
    new_state = run_abort(tmp_path, client, abort_state(cfg, entry_order_id=None))
    assert new_state.phase == "BANKED"
    assert new_state.shares == 0.0
    assert new_state.cash == pytest.approx(round(40.0 * 0.58, 6))
    assert new_state.exit_order_id is None


def test_abort_entry_cancel_credits_resting_fill_before_sell(tmp_path):
    """12 sh filled while the entry rested; the confirmed abort-cancel must
    credit them (and debit their cost) BEFORE selling, so the abort disposes
    the REAL holding instead of banking while still holding shares."""
    cfg = RollerConfig.from_dict(example_config_dict())
    state = abort_state(cfg, entry_order_id="ord-x", shares=40.0)
    state.cash = 16.8
    state.entry_price = 0.41
    client = FakeClient(FakeMarket(mid=0.6, bid=0.58))
    client.positions = [{"market_id": "m0", "shares_yes": 52.0, "shares_no": 0.0}]
    new_state = run_abort(tmp_path, client, state)
    assert "ord-x" in client.cancelled
    sells = [trade for trade in client.trades if trade["action"] == "sell"]
    assert sells and sells[0]["shares"] == pytest.approx(52.0)  # delta included
    assert new_state.phase == "BANKED"
    assert new_state.shares == 0.0
    # cash: 16.8 - 12 sh * 0.41 (fill debit) + 52 sh * 0.58 (abort sell)
    assert new_state.cash == pytest.approx(round(16.8 - 12.0 * 0.41 + 52.0 * 0.58, 6))


def test_abort_entry_cancel_verification_failure_pauses(tmp_path):
    """Cancel confirmed but holdings unverifiable: BANKED would lie about
    exposure and selling state.shares could under-dispose - PAUSE, keep id."""
    cfg = RollerConfig.from_dict(example_config_dict())
    state = abort_state(cfg, entry_order_id="ord-x")
    state.entry_price = 0.41
    client = PositionsDownClient(FakeMarket(mid=0.6, bid=0.58))
    new_state = run_abort(tmp_path, client, state)
    assert new_state.phase == "PAUSED"
    assert new_state.entry_order_id == "ord-x"  # tracking kept for retry
    assert new_state.shares == pytest.approx(40.0)
    assert client.trades == []  # no sell on unverified holdings
    assert "fill verification" in new_state.history[-1]["msg"]


def test_abort_exit_cancel_credits_partial_sell_before_continuing(tmp_path):
    """12 sh sold while the exit rested (venue holds 28 < state's 40): reduce
    shares to the venue count, credit proceeds at the resting exit price, then
    abort-sell only what is actually held."""
    cfg = RollerConfig.from_dict(example_config_dict())
    state = abort_state(cfg, entry_order_id=None, exit_order_id="ord-exit", shares=40.0)
    state.exit_price = 0.975
    client = FakeClient(FakeMarket(mid=0.6, bid=0.58))
    client.positions = [{"market_id": "m0", "shares_yes": 28.0, "shares_no": 0.0}]
    new_state = run_abort(tmp_path, client, state)
    assert "ord-exit" in client.cancelled
    sells = [trade for trade in client.trades if trade["action"] == "sell"]
    assert sells and sells[0]["shares"] == pytest.approx(28.0)  # real holding only
    assert new_state.phase == "BANKED"
    # cash: 12 sh * 0.975 (resting-sell credit) + 28 sh * 0.58 (abort sell)
    assert new_state.cash == pytest.approx(round(12.0 * 0.975 + 28.0 * 0.58, 6))


def test_abort_exit_cancel_detects_full_resting_fill_banks_without_sell(tmp_path):
    """All 40 sh sold while the exit rested (venue holds 0): credit the full
    proceeds and BANK without placing another sell."""
    cfg = RollerConfig.from_dict(example_config_dict())
    state = abort_state(cfg, entry_order_id=None, exit_order_id="ord-exit", shares=40.0)
    state.exit_price = 0.975
    client = FakeClient(FakeMarket(mid=0.6, bid=0.58))
    client.positions = []  # fully sold while resting
    new_state = run_abort(tmp_path, client, state)
    assert new_state.phase == "BANKED"
    assert new_state.shares == 0.0
    assert new_state.cash == pytest.approx(round(40.0 * 0.975, 6))
    assert client.trades == []  # nothing left to sell


def test_abort_exit_cancel_verification_failure_pauses(tmp_path):
    cfg = RollerConfig.from_dict(example_config_dict())
    state = abort_state(cfg, entry_order_id=None, exit_order_id="ord-exit", shares=40.0)
    state.exit_price = 0.975
    client = PositionsDownClient(FakeMarket(mid=0.6, bid=0.58))
    new_state = run_abort(tmp_path, client, state)
    assert new_state.phase == "PAUSED"
    assert new_state.exit_order_id == "ord-exit"  # kept for retry
    assert new_state.shares == pytest.approx(40.0)
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
