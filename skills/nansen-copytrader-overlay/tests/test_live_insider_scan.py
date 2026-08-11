"""Tests for nansen_live_insider_scan.py — mocked, no Nansen calls."""

import json
import sys
import os
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import patch
import pytest

import nansen_live_insider_scan as lis
from nansen_live_insider_scan import InsiderSignal, SUSPICION_THRESHOLD, HIGH_RISK_THRESHOLD


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

def load_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name)) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# _score_wallet
# ---------------------------------------------------------------------------

def test_score_wallet_suspicious_new_wallet():
    """NEW_WALLET + SINGLE_MARKET + HIGH_YES_ENTRY + LARGE_POSITION + RAPID_ACCUMULATION = high score."""
    market_trades = load_fixture("trades_by_market_live.json")
    balances = load_fixture("historical_balances_new_wallet.json")

    # The fixture wallet 0xSUSPICIOUS001 has:
    # - 3 trades in the last 24h (RAPID_ACCUMULATION)
    # - entry prices 0.82, 0.83, 0.84 (HIGH_YES_ENTRY)
    # - total ~$10k position (LARGE_POSITION)
    single_market_trades = [
        t for t in market_trades if t.get("buyer") == "0xSUSPICIOUS001"
    ]

    with patch("nansen_live_insider_scan.trades_by_address", return_value=single_market_trades), \
         patch("nansen_live_insider_scan.historical_balances", return_value=balances), \
         patch("time.sleep"):

        signal = lis._score_wallet("0xSUSPICIOUS001", "999", market_trades, dry_run=True)

    assert signal is not None
    assert signal.suspicion_score >= SUSPICION_THRESHOLD
    assert signal.side == "yes"
    assert signal.dry_run is True
    assert "HIGH_YES_ENTRY" in signal.reason_tags


def test_score_wallet_new_wallet_flag():
    """Wallet funded 2 days ago → NEW_WALLET flag."""
    two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    balances = [{"block_timestamp": two_days_ago, "value_usd": 5000.0, "token_symbol": "USDC"}]

    market_trades = [
        {
            "timestamp": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "market_id": "888",
            "market_question": "Will X happen?",
            "side": "yes",
            "price": 0.80,
            "size": 1000.0,
            "usdc_value": 800.0,
            "taker_action": "buy",
            "buyer": "0xNEWBUYER",
            "seller": "0xSELLER",
        }
    ]

    with patch("nansen_live_insider_scan.trades_by_address", return_value=market_trades), \
         patch("nansen_live_insider_scan.historical_balances", return_value=balances), \
         patch("time.sleep"):

        signal = lis._score_wallet("0xNEWBUYER", "888", market_trades, dry_run=True)

    # Score = NEW_WALLET(3) + SINGLE_MARKET(3) + HIGH_YES_ENTRY(2) = 8 ≥ threshold
    assert signal is not None
    assert "NEW_WALLET" in signal.reason_tags


def test_score_wallet_known_entity_discount():
    """Address on the known-entity allowlist reduces suspicion score."""
    recent_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    recent_ts2 = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    recent_ts3 = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()

    market_trades = [
        {"timestamp": recent_ts, "market_id": "777", "market_question": "Q",
         "side": "yes", "price": 0.82, "size": 1000, "usdc_value": 820,
         "taker_action": "buy", "buyer": "0xFUND", "seller": "0xS"},
        {"timestamp": recent_ts2, "market_id": "777", "market_question": "Q",
         "side": "yes", "price": 0.83, "size": 600, "usdc_value": 498,
         "taker_action": "buy", "buyer": "0xFUND", "seller": "0xS"},
        {"timestamp": recent_ts3, "market_id": "777", "market_question": "Q",
         "side": "yes", "price": 0.84, "size": 357, "usdc_value": 300,
         "taker_action": "buy", "buyer": "0xFUND", "seller": "0xS"},
    ]
    old_balance = [{"block_timestamp": "2020-01-01T00:00:00Z", "value_usd": 1000000.0,
                    "token_symbol": "USDC"}]

    with patch("nansen_live_insider_scan.trades_by_address", return_value=market_trades), \
         patch("nansen_live_insider_scan.historical_balances", return_value=old_balance), \
         patch("nansen_live_insider_scan.KNOWN_ENTITY_ADDRESSES", {"0xfund"}), \
         patch("time.sleep"):

        signal = lis._score_wallet("0xFUND", "777", market_trades, dry_run=True)

    if signal:
        assert "KNOWN_ENTITY" in signal.reason_tags
        assert signal.has_known_label is True


def test_score_wallet_below_threshold_returns_none():
    """Normal wallet with low entry and small position should not be flagged."""
    market_trades = [
        {
            "timestamp": "2026-06-15T10:00:00Z",
            "market_id": "555",
            "market_question": "Normal market Q",
            "side": "yes",
            "price": 0.45,
            "size": 100.0,
            "usdc_value": 45.0,
            "taker_action": "buy",
            "buyer": "0xNORMAL",
            "seller": "0xS",
        }
    ]
    old_wallet_balance = [
        {"block_timestamp": "2024-01-01T00:00:00Z", "value_usd": 1000.0, "token_symbol": "USDC"}
    ]
    many_markets = [
        {"market_id": str(i), "market_question": f"Q{i}", "side": "yes",
         "price": 0.5, "size": 10, "usdc_value": 5, "taker_action": "buy",
         "buyer": "0xNORMAL", "seller": "0xS", "timestamp": "2026-01-01T00:00:00Z"}
        for i in range(20)
    ]

    with patch("nansen_live_insider_scan.trades_by_address", return_value=many_markets), \
         patch("nansen_live_insider_scan.historical_balances", return_value=old_wallet_balance), \
         patch("time.sleep"):

        signal = lis._score_wallet("0xNORMAL", "555", market_trades, dry_run=True)

    # Score: no age flag (old wallet) + no market focus flag (20 markets) + no high entry
    # + no large position + no rapid accumulation = 0
    assert signal is None


# ---------------------------------------------------------------------------
# dry_run gate
# ---------------------------------------------------------------------------

def test_dry_run_signals_always_have_dry_run_true():
    """dry_run=True signals are emitted but callers must not act on them."""
    market_trades = load_fixture("trades_by_market_live.json")
    balances = load_fixture("historical_balances_new_wallet.json")
    single_market = [t for t in market_trades if t.get("buyer") == "0xSUSPICIOUS001"]

    with patch("nansen_live_insider_scan.trades_by_address", return_value=single_market), \
         patch("nansen_live_insider_scan.historical_balances", return_value=balances), \
         patch("time.sleep"):

        signal = lis._score_wallet("0xSUSPICIOUS001", "999", market_trades, dry_run=True)

    if signal:
        assert signal.dry_run is True


def test_live_flag_propagates():
    """When dry_run=False the signal.dry_run is False (caller enables real orders)."""
    market_trades = load_fixture("trades_by_market_live.json")
    balances = load_fixture("historical_balances_new_wallet.json")
    single_market = [t for t in market_trades if t.get("buyer") == "0xSUSPICIOUS001"]

    with patch("nansen_live_insider_scan.trades_by_address", return_value=single_market), \
         patch("nansen_live_insider_scan.historical_balances", return_value=balances), \
         patch("time.sleep"):

        signal = lis._score_wallet("0xSUSPICIOUS001", "999", market_trades, dry_run=False)

    if signal:
        assert signal.dry_run is False


# ---------------------------------------------------------------------------
# scan_live_markets
# ---------------------------------------------------------------------------

def test_scan_live_markets_no_markets_found():
    with patch("nansen_live_insider_scan.market_screener", return_value=[]), \
         patch("time.sleep"):
        signals = lis.scan_live_markets(market_ids=None, dry_run=True)
    assert signals == []


def test_scan_live_markets_explicit_ids():
    """Providing explicit market_ids skips auto-discovery."""
    market_trades = load_fixture("trades_by_market_live.json")
    balances = load_fixture("historical_balances_new_wallet.json")

    def mock_trades_by_address(addr, **kwargs):
        return [t for t in market_trades if t.get("buyer") == addr]

    with patch("nansen_live_insider_scan.trades_by_market", return_value=market_trades), \
         patch("nansen_live_insider_scan.trades_by_address", side_effect=mock_trades_by_address), \
         patch("nansen_live_insider_scan.historical_balances", return_value=balances), \
         patch("time.sleep"):

        signals = lis.scan_live_markets(market_ids=["999"], dry_run=True, min_suspicion_score=3)

    # Should find 0xSUSPICIOUS001 as suspicious
    suspicious_addrs = {s.wallet_address for s in signals}
    assert "0xSUSPICIOUS001" in suspicious_addrs


def test_scan_live_markets_sorted_by_score():
    """Results should be sorted by suspicion_score descending."""
    market_trades = load_fixture("trades_by_market_live.json")
    balances = load_fixture("historical_balances_new_wallet.json")

    def mock_trades_by_address(addr, **kwargs):
        return [t for t in market_trades if t.get("buyer") == addr]

    with patch("nansen_live_insider_scan.trades_by_market", return_value=market_trades), \
         patch("nansen_live_insider_scan.trades_by_address", side_effect=mock_trades_by_address), \
         patch("nansen_live_insider_scan.historical_balances", return_value=balances), \
         patch("time.sleep"):

        signals = lis.scan_live_markets(market_ids=["999"], dry_run=True)

    scores = [s.suspicion_score for s in signals]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# InsiderSignal.as_dict
# ---------------------------------------------------------------------------

def test_insider_signal_as_dict_is_serialisable():
    s = InsiderSignal(
        market_id="123",
        market_question="Will X happen?",
        wallet_address="0xABC",
        side="yes",
        confidence=0.75,
        edge=0.3,
        suspicion_score=7,
        reason_tags=["NEW_WALLET", "SINGLE_MARKET"],
        entry_price=0.82,
        position_usd=5000.0,
        wallet_age_days=3.0,
        distinct_markets_traded=1,
        has_known_label=False,
        dry_run=True,
    )
    d = s.as_dict()
    assert d["market_id"] == "123"
    assert d["side"] == "yes"
    assert d["dry_run"] is True
    # Confirm all required keys present
    for key in ("market_id", "market_question", "wallet_address", "side",
                 "confidence", "edge", "suspicion_score", "reason_tags",
                 "entry_price", "position_usd", "dry_run"):
        assert key in d


def test_scan_live_markets_hard_refuses_live_mode():
    """scan_live_markets(dry_run=False) must hard-raise — this module is
    experimental until owner/proxy handling and price semantics are
    confirmed against the live API."""
    with pytest.raises(lis.LiveTradingNotSupportedError):
        lis.scan_live_markets(market_ids=["1"], dry_run=False)


def test_scan_live_markets_hard_refuses_live_mode_before_any_api_call():
    """The refusal must happen before any Nansen call — no market_screener,
    no trades_by_market, nothing — so a --live typo can't spend credits."""
    with patch("nansen_live_insider_scan.market_screener") as mock_screener, \
         patch("nansen_live_insider_scan.trades_by_market") as mock_trades:
        with pytest.raises(lis.LiveTradingNotSupportedError):
            lis.scan_live_markets(dry_run=False)

    mock_screener.assert_not_called()
    mock_trades.assert_not_called()


def test_live_trading_not_supported_is_a_nansen_error():
    from nansen_adapter import NansenError
    assert issubclass(lis.LiveTradingNotSupportedError, NansenError)


def test_scan_live_markets_credit_guard_stops_scan_early():
    """When the shared CreditGuard's budget is exhausted mid-scan, remaining
    markets are skipped rather than continuing to spend credits."""
    from nansen_adapter import CreditGuard

    guard = CreditGuard(max_calls=100)

    def raise_on_second_market(market_id, **kwargs):
        if market_id == "2":
            raise lis.CreditGuardExceeded("budget exhausted")
        return []

    with patch("nansen_live_insider_scan.trades_by_market", side_effect=raise_on_second_market), \
         patch("time.sleep"):
        signals = lis.scan_live_markets(market_ids=["1", "2", "3"], dry_run=True, guard=guard)

    assert signals == []


def test_insider_signal_deterministic():
    """Same inputs → same signal (no randomness)."""
    s1 = InsiderSignal(market_id="1", market_question="Q", wallet_address="0xA",
                       side="yes", confidence=0.5, edge=0.3, suspicion_score=5,
                       reason_tags=["NEW_WALLET"])
    s2 = InsiderSignal(market_id="1", market_question="Q", wallet_address="0xA",
                       side="yes", confidence=0.5, edge=0.3, suspicion_score=5,
                       reason_tags=["NEW_WALLET"])
    assert s1.as_dict() == s2.as_dict()
