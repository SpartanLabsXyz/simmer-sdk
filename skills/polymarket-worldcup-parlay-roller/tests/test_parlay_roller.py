"""Tests for the pure parlay-roller decision module."""

from datetime import datetime, timedelta, timezone

import pytest

from parlay_roller import (
    Action,
    Leg,
    MarketSnap,
    RollerConfig,
    StreakState,
    apply_entry_fill,
    apply_exit_proceeds,
    apply_partial_entry_fill,
    decide,
    entry_price,
    streak_implied_price,
    validate_config,
)

UTC = timezone.utc
NOW = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)


def mk_leg(i: int, kickoff: datetime, side: str = "yes") -> Leg:
    return Leg(
        market_id=f"m{i}",
        side=side,
        label=f"Leg {i}",
        resolution_note=f"Team {i} WIN only - a draw loses this leg",
        kickoff=kickoff,
        expected_end=kickoff + timedelta(minutes=135),
    )


def mk_config(n_legs: int = 2, start: datetime = None, **kw) -> RollerConfig:
    start = start or datetime(2026, 6, 12, 15, 0, tzinfo=UTC)
    legs = [mk_leg(i, start + timedelta(hours=4 * i)) for i in range(n_legs)]
    return RollerConfig(legs=legs, stake_usd=25.0, **kw)


def snap(mid=0.5, bid=0.49, ask=0.51, status="active", resolved_yes=None):
    return MarketSnap(mid=mid, best_bid=bid, best_ask=ask, status=status, resolved_yes=resolved_yes)


def fresh_state(cfg) -> StreakState:
    return StreakState.fresh(cfg)


def held_state(cfg, leg_index=0, shares=50.0):
    st = StreakState.fresh(cfg)
    st.phase = "LEG_OPEN"
    st.leg_index = leg_index
    st.cash = 0.0
    st.shares = shares
    return st


def after_end(cfg, leg_index=0, minutes=10):
    return cfg.legs[leg_index].expected_end + timedelta(minutes=minutes)


def test_valid_config_passes():
    assert validate_config(mk_config(3), NOW) == []


def test_rejects_more_than_max_legs():
    errs = validate_config(mk_config(6), NOW)
    assert any("max 5 legs" in err for err in errs)


def test_rejects_zero_legs():
    cfg = mk_config(2)
    cfg.legs = []
    assert any("at least 1 leg" in err for err in validate_config(cfg, NOW))


def test_rejects_overlapping_kickoffs():
    start = datetime(2026, 6, 12, 15, 0, tzinfo=UTC)
    cfg = mk_config(1, start=start)
    cfg.legs.append(mk_leg(1, start + timedelta(minutes=30)))
    errs = validate_config(cfg, NOW)
    assert any("overlap" in err for err in errs)


def test_rejects_legs_out_of_order():
    start = datetime(2026, 6, 12, 15, 0, tzinfo=UTC)
    cfg = mk_config(1, start=start)
    cfg.legs.insert(0, mk_leg(9, start + timedelta(hours=6)))
    assert any("kickoff order" in err for err in validate_config(cfg, NOW))


def test_rejects_first_kickoff_in_past():
    cfg = mk_config(2, start=NOW - timedelta(hours=1))
    assert any("already kicked off" in err for err in validate_config(cfg, NOW))


def test_validate_config_none_skips_first_kickoff_time_check():
    cfg = mk_config(2, start=NOW - timedelta(hours=1))
    assert not any("already kicked off" in err for err in validate_config(cfg, None))


def test_rejects_nonpositive_stake():
    cfg = mk_config(2)
    cfg.stake_usd = 0
    assert any("stake" in err for err in validate_config(cfg, NOW))


def test_rejects_bad_side():
    cfg = mk_config(2)
    cfg.legs[0].side = "maybe"
    assert any("side" in err for err in validate_config(cfg, NOW))


def test_config_json_roundtrip():
    cfg = mk_config(2)
    data = cfg.to_dict()
    cfg2 = RollerConfig.from_dict(data)
    assert cfg2.legs[1].market_id == "m1"
    assert cfg2.legs[1].kickoff == cfg.legs[1].kickoff
    assert cfg2.stake_usd == 25.0


def test_entry_price_is_mid_plus_tolerance_capped():
    assert entry_price(0.50, 0.01) == pytest.approx(0.51)
    assert entry_price(0.995, 0.01) == 0.99


def test_fresh_state_places_leg1_entry_immediately():
    cfg = mk_config(2)
    act = decide(fresh_state(cfg), cfg, snap(mid=0.40), now=NOW)
    assert isinstance(act, Action)
    assert act.kind == "place_entry"
    assert act.price == pytest.approx(0.41)
    assert act.amount == pytest.approx(25.0)


def test_no_reentry_while_order_working():
    cfg = mk_config(2)
    st = fresh_state(cfg)
    st.entry_order_id = "ord-1"
    st.entry_placed_at = NOW
    act = decide(st, cfg, snap(), now=NOW + timedelta(seconds=30))
    assert act.kind == "wait"


def test_entry_ttl_expiry_cancels():
    cfg = mk_config(2)
    st = fresh_state(cfg)
    st.entry_order_id = "ord-1"
    st.entry_placed_at = NOW
    act = decide(st, cfg, snap(), now=NOW + timedelta(seconds=cfg.entry_ttl_s + 1))
    assert act.kind == "cancel_entry"
    assert act.order_id == "ord-1"


def test_entry_window_closes_at_kickoff_minus_buffer():
    cfg = mk_config(2)
    st = fresh_state(cfg)
    at = cfg.legs[0].kickoff - timedelta(minutes=cfg.roll_buffer_min - 1)
    act = decide(st, cfg, snap(), now=at)
    assert act.kind == "bank_and_stop"


def test_no_mid_price_waits():
    cfg = mk_config(2)
    act = decide(fresh_state(cfg), cfg, snap(mid=None, bid=None, ask=None), now=NOW)
    assert act.kind == "wait"


def test_state_roundtrip():
    cfg = mk_config(2)
    st = fresh_state(cfg)
    st.cash = 31.5
    st.log("test entry")
    st2 = StreakState.from_dict(st.to_dict())
    assert st2.cash == 31.5
    assert st2.phase == st.phase
    assert st2.history[-1]["msg"] == "test entry"


def test_holding_mid_match_waits():
    cfg = mk_config(2)
    st = held_state(cfg)
    at = cfg.legs[0].kickoff + timedelta(minutes=30)
    assert decide(st, cfg, snap(mid=0.9, bid=0.89), now=at).kind == "wait"


def test_post_match_high_bid_sells():
    cfg = mk_config(2)
    st = held_state(cfg, shares=50.0)
    act = decide(st, cfg, snap(mid=0.98, bid=0.975), now=after_end(cfg))
    assert act.kind == "place_exit"
    assert act.price == pytest.approx(0.975)
    assert act.shares == pytest.approx(50.0)


def test_no_duplicate_exit_while_order_working():
    cfg = mk_config(2)
    st = held_state(cfg, shares=50.0)
    st.exit_order_id = "exit-1"
    act = decide(st, cfg, snap(mid=0.98, bid=0.975), now=after_end(cfg))
    assert act.kind == "wait"
    assert act.reason == "exit order working"


def test_post_match_floor_busts():
    cfg = mk_config(2)
    act = decide(held_state(cfg), cfg, snap(mid=0.02, bid=0.01), now=after_end(cfg))
    assert act.kind == "mark_busted"


def test_state_roundtrip_preserves_resting_order_prices():
    cfg = mk_config(2)
    st = fresh_state(cfg)
    st.entry_order_id = "ord-1"
    st.entry_price = 0.41
    st.entry_amount = 25.0
    st.exit_price = 0.975
    st2 = StreakState.from_dict(st.to_dict())
    assert st2.entry_price == pytest.approx(0.41)
    assert st2.entry_amount == pytest.approx(25.0)
    assert st2.exit_price == pytest.approx(0.975)


def test_state_from_dict_defaults_missing_price_fields():
    cfg = mk_config(2)
    d = fresh_state(cfg).to_dict()
    for key in ("entry_price", "entry_amount", "exit_price"):
        d.pop(key, None)
    st = StreakState.from_dict(d)
    assert st.entry_price is None
    assert st.entry_amount is None
    assert st.exit_price is None


def test_apply_entry_fill_clears_entry_price_fields():
    cfg = mk_config(2)
    st = StreakState.fresh(cfg)
    st.entry_order_id = "ord-1"
    st.entry_price = 0.41
    st.entry_amount = 25.0
    apply_entry_fill(st, shares_bought=60.0, spent=25.0, now=NOW)
    assert st.entry_price is None
    assert st.entry_amount is None


def test_apply_exit_proceeds_clears_exit_price():
    cfg = mk_config(2)
    st = held_state(cfg, leg_index=0, shares=50.0)
    st.exit_order_id = "ord-9"
    st.exit_price = 0.975
    apply_exit_proceeds(st, cfg, proceeds=48.5, now=NOW)
    assert st.exit_order_id is None
    assert st.exit_price is None


def test_post_match_middle_price_holds_to_resolution():
    cfg = mk_config(2)
    act = decide(held_state(cfg), cfg, snap(mid=0.6, bid=0.55), now=after_end(cfg))
    assert act.kind == "wait"


def test_resolved_for_us_settles_won():
    cfg = mk_config(2)
    st = held_state(cfg, shares=50.0)
    act = decide(st, cfg, snap(mid=None, bid=None, status="resolved", resolved_yes=True), now=after_end(cfg))
    assert act.kind == "settle_won"


def test_resolved_against_us_busts():
    cfg = mk_config(2)
    act = decide(held_state(cfg), cfg, snap(mid=None, bid=None, status="resolved", resolved_yes=False), now=after_end(cfg))
    assert act.kind == "mark_busted"


def test_no_leg_resolved_no_means_won():
    cfg = mk_config(2)
    cfg.legs[0].side = "no"
    act = decide(held_state(cfg), cfg, snap(mid=None, bid=None, status="resolved", resolved_yes=False), now=after_end(cfg))
    assert act.kind == "settle_won"


def test_stale_unresolved_pauses():
    cfg = mk_config(2)
    act = decide(held_state(cfg), cfg, snap(mid=0.6, bid=0.55), now=after_end(cfg, minutes=6 * 60 + 1))
    assert act.kind == "pause"


def test_voided_market_pauses():
    cfg = mk_config(2)
    act = decide(held_state(cfg), cfg, snap(mid=0.5, bid=0.5, status="voided"), now=after_end(cfg))
    assert act.kind == "pause"


def test_apply_entry_fill_moves_cash_to_shares():
    cfg = mk_config(2)
    st = StreakState.fresh(cfg)
    apply_entry_fill(st, shares_bought=60.0, spent=25.0, now=NOW)
    assert st.phase == "LEG_OPEN"
    assert st.shares == pytest.approx(60.0)
    assert st.cash == pytest.approx(0.0)
    assert st.entry_order_id is None


def test_apply_partial_entry_fill_credits_fill_and_tracks_residual():
    cfg = mk_config(2)
    st = StreakState.fresh(cfg)  # cash 25.0
    apply_partial_entry_fill(
        st, shares_bought=20.0, amount=25.0, price=0.41, order_id="ord-2", now=NOW
    )
    assert st.phase == "LEG_OPEN"
    assert st.shares == pytest.approx(20.0)
    assert st.cash == pytest.approx(25.0 - 20.0 * 0.41)
    assert st.entry_order_id == "ord-2"
    assert st.entry_placed_at == NOW
    assert st.entry_price == pytest.approx(0.41)
    assert st.entry_amount == pytest.approx(25.0 - 8.2)  # residual exposure


def test_apply_partial_entry_fill_without_order_id_pauses():
    cfg = mk_config(2)
    st = StreakState.fresh(cfg)
    apply_partial_entry_fill(
        st, shares_bought=20.0, amount=25.0, price=0.41, order_id=None, now=NOW
    )
    assert st.phase == "PAUSED"
    assert st.shares == pytest.approx(20.0)  # confirmed fill still credited
    assert st.cash == pytest.approx(16.8)
    assert st.entry_order_id is None
    assert st.entry_amount is None


def test_apply_entry_fill_accumulates_after_partial():
    """Residual fill via reconcile must ADD to partial-credited shares, not overwrite."""
    cfg = mk_config(2)
    st = StreakState.fresh(cfg)
    apply_partial_entry_fill(
        st, shares_bought=20.0, amount=25.0, price=0.41, order_id="ord-2", now=NOW
    )
    # reconcile heuristic: residual entry_amount / entry_price filled
    residual_shares = round(st.entry_amount / st.entry_price, 2)
    apply_entry_fill(st, shares_bought=residual_shares, spent=st.entry_amount, now=NOW)
    assert st.shares == pytest.approx(20.0 + residual_shares)
    assert st.cash == pytest.approx(0.0)
    assert st.entry_order_id is None
    assert st.entry_amount is None


def test_decide_partial_holding_with_working_entry_waits():
    """shares > 0 AND entry order working: no second entry, no settle action."""
    cfg = mk_config(2)
    st = fresh_state(cfg)
    st.phase = "LEG_OPEN"
    st.shares = 20.0
    st.cash = 16.8
    st.entry_order_id = "ord-2"
    st.entry_placed_at = NOW
    act = decide(st, cfg, snap(mid=0.41), now=NOW + timedelta(seconds=30))
    assert act.kind == "wait"
    assert act.reason == "entry order working"


def test_decide_partial_holding_with_stale_entry_order_cancels():
    """The TTL cancel-path manages the residual order even while holding shares."""
    cfg = mk_config(2)
    st = fresh_state(cfg)
    st.phase = "LEG_OPEN"
    st.shares = 20.0
    st.cash = 16.8
    st.entry_order_id = "ord-2"
    st.entry_placed_at = NOW
    act = decide(st, cfg, snap(mid=0.41), now=NOW + timedelta(seconds=cfg.entry_ttl_s + 1))
    assert act.kind == "cancel_entry"
    assert act.order_id == "ord-2"


def test_apply_exit_proceeds_rolls_to_next_leg():
    cfg = mk_config(2)
    st = held_state(cfg, leg_index=0, shares=50.0)
    apply_exit_proceeds(st, cfg, proceeds=48.5, now=NOW)
    assert st.leg_index == 1
    assert st.phase == "LEG_OPEN"
    assert st.cash == pytest.approx(48.5)
    assert st.shares == 0.0


def test_apply_exit_proceeds_adds_to_existing_cash():
    """Partial-fill support: proceeds ACCUMULATE into cash, never overwrite."""
    cfg = mk_config(2)
    st = held_state(cfg, leg_index=0, shares=30.0)
    st.cash = 19.5  # accumulated from an earlier partial exit fill
    apply_exit_proceeds(st, cfg, proceeds=29.25, now=NOW)
    assert st.cash == pytest.approx(48.75)
    assert st.leg_index == 1


def test_apply_exit_proceeds_on_last_leg_completes():
    cfg = mk_config(2)
    st = held_state(cfg, leg_index=1, shares=80.0)
    apply_exit_proceeds(st, cfg, proceeds=77.0, now=NOW)
    assert st.phase == "COMPLETE"
    assert st.cash == pytest.approx(77.0)


def test_bank_half_after_siphons_half():
    cfg = mk_config(3)
    cfg.bank_half_after = 1
    st = held_state(cfg, leg_index=0, shares=50.0)
    apply_exit_proceeds(st, cfg, proceeds=48.0, now=NOW)
    assert st.banked == pytest.approx(24.0)
    assert st.cash == pytest.approx(24.0)
    assert st.leg_index == 1


def test_streak_implied_price_is_product_of_leg_prices():
    assert streak_implied_price([0.5, 0.4]) == pytest.approx(0.2)


def test_streak_implied_price_ignores_missing():
    assert streak_implied_price([0.5, None, 0.4]) is None


def test_streak_implied_price_empty():
    assert streak_implied_price([]) is None
