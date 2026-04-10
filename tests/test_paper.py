"""Tests for simmer_sdk.paper — Paper trading portfolio tracker."""

import pytest
from simmer_sdk.paper import PaperPortfolio, PaperPosition, PaperSettlement, DEFAULT_STARTING_BALANCE


# --- PaperPortfolio initialization ---

def test_default_starting_balance():
    p = PaperPortfolio()
    assert p.balance == DEFAULT_STARTING_BALANCE
    assert p.starting_balance == DEFAULT_STARTING_BALANCE

def test_custom_starting_balance():
    p = PaperPortfolio(starting_balance=5000)
    assert p.balance == 5000

def test_no_positions_initially():
    p = PaperPortfolio()
    assert len(p.positions) == 0
    assert p.get_open_market_ids() == []


# --- Buying ---

def test_buy_creates_position():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=100, cost=50, price=0.50)
    pos = p.get_position("m1")
    assert pos.shares_yes == 100
    assert pos.shares_no == 0
    assert pos.total_cost == 50

def test_buy_deducts_balance():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=100, cost=50, price=0.50)
    assert p.balance == 950

def test_buy_no_side():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "no", "buy", shares=200, cost=80, price=0.40)
    pos = p.get_position("m1")
    assert pos.shares_no == 200
    assert pos.shares_yes == 0
    assert p.balance == 920


# --- Selling ---

def test_sell_credits_balance():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=100, cost=50, price=0.50)
    p.log_trade("m1", "yes", "sell", shares=50, cost=30, price=0.60)
    assert p.balance == 980  # -50 + 30
    pos = p.get_position("m1")
    assert pos.shares_yes == 50

def test_sell_more_than_held_capped():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=10, cost=5, price=0.50)
    p.log_trade("m1", "yes", "sell", shares=100, cost=50, price=0.50)
    pos = p.get_position("m1")
    assert pos.shares_yes == 0


# --- Settlement ---

def test_settle_yes_outcome():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=100, cost=60, price=0.60)
    # Balance after buy: 940
    s = p.settle("m1", "yes")
    assert s is not None
    assert s.payout == 100  # 100 shares * $1
    assert s.pnl == 40  # 100 - 60
    assert p.balance == 1040  # 940 + 100
    assert "m1" not in p.positions

def test_settle_no_outcome():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=100, cost=60, price=0.60)
    s = p.settle("m1", "no")
    assert s is not None
    assert s.payout == 0  # YES shares worthless
    assert s.pnl == -60  # 0 - 60
    assert p.balance == 940  # no payout

def test_settle_mixed_position():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=50, cost=30, price=0.60)
    p.log_trade("m1", "no", "buy", shares=40, cost=16, price=0.40)
    # Balance: 1000 - 30 - 16 = 954
    s = p.settle("m1", "yes")
    assert s.shares_won == 50  # yes shares won
    assert s.shares_lost == 40  # no shares lost
    assert s.payout == 50
    assert p.balance == 1004  # 954 + 50

def test_settle_nonexistent_market():
    p = PaperPortfolio(starting_balance=1000)
    assert p.settle("nonexistent", "yes") is None

def test_settle_empty_position():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=10, cost=5, price=0.50)
    p.log_trade("m1", "yes", "sell", shares=10, cost=5, price=0.50)
    assert p.settle("m1", "yes") is None


# --- total_pnl ---

def test_total_pnl_accumulates():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=100, cost=60, price=0.60)
    p.log_trade("m2", "no", "buy", shares=50, cost=20, price=0.40)
    p.settle("m1", "yes")  # pnl = +40
    p.settle("m2", "no")  # pnl = 50 - 20 = +30 (no shares win)
    assert p.total_pnl == pytest.approx(70)


# --- summary ---

def test_summary_structure():
    p = PaperPortfolio(starting_balance=500)
    p.log_trade("m1", "yes", "buy", shares=10, cost=5, price=0.50)
    s = p.summary()
    assert s["starting_balance"] == 500
    assert s["balance"] == 495
    assert s["open_positions"] == 1
    assert s["settled_positions"] == 0
    assert "m1" in s["positions"]


# --- get_open_market_ids ---

def test_open_market_ids():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=10, cost=5, price=0.50)
    p.log_trade("m2", "no", "buy", shares=20, cost=8, price=0.40)
    ids = p.get_open_market_ids()
    assert set(ids) == {"m1", "m2"}

def test_open_market_ids_after_settle():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=10, cost=5, price=0.50)
    p.settle("m1", "yes")
    assert p.get_open_market_ids() == []


# --- get_position for unknown market ---

def test_get_position_unknown_market():
    p = PaperPortfolio()
    pos = p.get_position("unknown")
    assert pos.market_id == "unknown"
    assert pos.shares_yes == 0
    assert pos.shares_no == 0


# --- Venue tracking ---

def test_position_tracks_venue():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=10, cost=5, price=0.50, venue="polymarket")
    pos = p.get_position("m1")
    assert pos.venue == "polymarket"

def test_position_default_venue_sim():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=10, cost=5, price=0.50)
    pos = p.get_position("m1")
    assert pos.venue == "sim"

def test_settlement_tracks_venue():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=10, cost=5, price=0.50, venue="polymarket")
    s = p.settle("m1", "yes")
    assert s.venue == "polymarket"

def test_summary_includes_venue():
    p = PaperPortfolio(starting_balance=1000)
    p.log_trade("m1", "yes", "buy", shares=10, cost=5, price=0.50, venue="polymarket")
    s = p.summary()
    assert s["positions"]["m1"]["venue"] == "polymarket"


# --- Polymarket spread simulation (unit-level) ---

def test_spread_affects_shares_received():
    """Buying at a higher price (ask) yields fewer shares per dollar."""
    p = PaperPortfolio(starting_balance=10000)
    # No spread: 100 / 0.50 = 200 shares
    p.log_trade("m1", "yes", "buy", shares=200, cost=100, price=0.50)
    # With spread: 100 / 0.51 ≈ 196.08 shares (ask price)
    p.log_trade("m2", "yes", "buy", shares=196.08, cost=100, price=0.51)
    pos1 = p.get_position("m1")
    pos2 = p.get_position("m2")
    assert pos1.shares_yes > pos2.shares_yes

def test_spread_sell_at_bid():
    """Selling at bid price (lower) yields less proceeds."""
    p = PaperPortfolio(starting_balance=10000)
    # Buy 100 shares
    p.log_trade("m1", "yes", "buy", shares=100, cost=50, price=0.50)
    # Sell at bid (0.49) instead of mid (0.50) — receives less
    p.log_trade("m1", "yes", "sell", shares=100, cost=49, price=0.49)
    # Balance: 10000 - 50 + 49 = 9999 (loss from spread)
    assert p.balance == pytest.approx(9999)
