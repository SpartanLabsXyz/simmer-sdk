"""Tests for nansen_copytrader_overlay_general.py — mocked, no Nansen calls."""

import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import patch
import pytest

import nansen_copytrader_overlay_general as og
from nansen_adapter import CreditGuard, CreditGuardExceeded, NansenError


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

def load_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name)) as f:
        return json.load(f)


def market_row(address, net_buy_cost_usd, total_pnl_usd, owner_address=None):
    return {
        "address": address,
        "owner_address": owner_address or f"{address}-OWNER",
        "side_held": "yes",
        "net_buy_cost_usd": net_buy_cost_usd,
        "unrealized_value_usd": 0.0,
        "total_pnl_usd": total_pnl_usd,
    }


def summary(resolved_count=10, win_rate=0.7, avg_roi=0.5):
    return {"address": "x", "win_rate": win_rate, "avg_roi": avg_roi,
            "resolved_count": resolved_count, "total_pnl_usd": 100.0}


# ---------------------------------------------------------------------------
# _compute_quality_score (general/secondary signal)
# ---------------------------------------------------------------------------

def test_quality_score_high_win_rate():
    features = {
        "win_rate": 0.80,
        "avg_roi": 1.5,
        "consistency": 0.75,
        "concentration": 0.30,
        "resolved_count": 15,
    }
    score, tags = og._compute_quality_score(features, distinct_markets=12)
    assert score > 0.5
    assert "HIGH_WIN_RATE" in tags
    assert "STRONG_ROI" in tags
    assert "CONSISTENT_PNL" in tags


def test_quality_score_insufficient_history():
    features = {
        "win_rate": 0.80,
        "avg_roi": 2.0,
        "consistency": 0.9,
        "concentration": 0.1,
        "resolved_count": 2,   # below MIN_RESOLVED_MARKETS=3
    }
    score, tags = og._compute_quality_score(features, distinct_markets=5)
    assert score == 0.0
    assert any("INSUFFICIENT_HISTORY" in t for t in tags)


def test_quality_score_applies_concentration_penalty():
    features = {
        "win_rate": 0.70,
        "avg_roi": 1.0,
        "consistency": 0.7,
        "concentration": 0.90,  # very high — should penalise
        "resolved_count": 10,
    }
    score_high_conc, tags_high = og._compute_quality_score(features, distinct_markets=5)

    features_low = {**features, "concentration": 0.10}
    score_low_conc, tags_low = og._compute_quality_score(features_low, distinct_markets=5)

    assert score_low_conc > score_high_conc
    assert "HIGH_CONCENTRATION_RISK" in tags_high


def test_quality_score_negative_roi_penalised():
    features = {
        "win_rate": 0.30,
        "avg_roi": -0.5,
        "consistency": 0.5,
        "concentration": 0.3,
        "resolved_count": 5,
    }
    score, tags = og._compute_quality_score(features, distinct_markets=8)
    assert "NEGATIVE_AVG_ROI" in tags
    assert "LOW_WIN_RATE" in tags


# ---------------------------------------------------------------------------
# _compute_market_score (primary, pnl-by-market signal)
# ---------------------------------------------------------------------------

def test_market_score_profitable_high_roi():
    row = {"total_pnl_usd": 800.0, "net_buy_cost_usd": 400.0}  # ROI = 2.0
    score, tags = og._compute_market_score(row, rank=0, leaderboard_size=10)
    assert score == 1.0
    assert "MARKET_PROFITABLE" in tags
    assert "MARKET_TOP_PNL" in tags  # rank 0 of 10 → top 20%


def test_market_score_unprofitable():
    row = {"total_pnl_usd": -100.0, "net_buy_cost_usd": 200.0}
    score, tags = og._compute_market_score(row, rank=8, leaderboard_size=10)
    assert score == 0.0
    assert "MARKET_UNPROFITABLE" in tags
    assert "MARKET_TOP_PNL" not in tags


def test_market_score_no_cost_basis_but_profitable():
    row = {"total_pnl_usd": 50.0, "net_buy_cost_usd": 0.0}
    score, tags = og._compute_market_score(row, rank=None, leaderboard_size=0)
    assert score == 0.6
    assert "MARKET_PROFITABLE" in tags


# ---------------------------------------------------------------------------
# enrich_leaders — market-first path (market_id given, the primary signal)
# ---------------------------------------------------------------------------

def test_enrich_leaders_requires_market_id_for_primary_signal():
    """Without market_id, enrich_leaders falls back to legacy broad-history
    ranking and logs a warning — market-first is the recommended path."""
    leaders = [{"proxy_address": "0xABC", "owner_address": "0xABC-OWNER", "wallet_score": 0.5}]

    with patch("nansen_copytrader_overlay_general.pnl_by_address", return_value=[]), \
         patch("nansen_copytrader_overlay_general.trades_by_address", return_value=[]), \
         patch("time.sleep"):
        result = og.enrich_leaders(leaders, dry_run=True)

    assert len(result) == 1
    assert "market_score" in result[0]["nansen_features"]


def test_enrich_leaders_market_first_uses_pnl_by_market_as_primary():
    market_rows = [
        market_row("0xGOOD", net_buy_cost_usd=100.0, total_pnl_usd=150.0),  # ROI 1.5
    ]
    leaders = [{"proxy_address": "0xGOOD", "owner_address": "0xGOOD-OWNER", "wallet_score": 0.4}]

    with patch("nansen_copytrader_overlay_general.pnl_by_market", return_value=market_rows), \
         patch("nansen_copytrader_overlay_general.address_summary", return_value=summary(resolved_count=1)), \
         patch("time.sleep"):
        result = og.enrich_leaders(leaders, market_id="42", dry_run=True)

    r = result[0]
    assert r["nansen_features"]["market_score"] is not None
    assert "MARKET_PROFITABLE" in r["reason_tags"]
    # address_summary gated out the expensive pull (resolved_count=1 < MIN_RESOLVED_MARKETS)
    assert any("INSUFFICIENT_HISTORY" in t for t in r["reason_tags"])


def test_enrich_leaders_not_in_market_leaderboard_gets_discount_not_blend():
    market_rows = [market_row("0xOTHER", 100.0, 50.0)]
    leaders = [{"proxy_address": "0xNOTFOUND", "owner_address": "0xO", "wallet_score": 0.5}]

    with patch("nansen_copytrader_overlay_general.pnl_by_market", return_value=market_rows), \
         patch("time.sleep"):
        result = og.enrich_leaders(leaders, market_id="42", dry_run=True)

    r = result[0]
    assert "NOT_IN_MARKET_LEADERBOARD" in r["reason_tags"]
    assert r["adjusted_score"] == pytest.approx(0.5 * 0.9, abs=0.001)


def test_enrich_leaders_missing_proxy_address():
    leaders = [{"owner_address": "0xOWNER", "wallet_score": 0.5}]  # no proxy_address, no address

    with patch("nansen_copytrader_overlay_general.pnl_by_market", return_value=[]), \
         patch("time.sleep"):
        result = og.enrich_leaders(leaders, market_id="42", dry_run=True)

    assert "MISSING_PROXY_ADDRESS" in result[0]["reason_tags"]
    assert result[0]["adjusted_score"] == pytest.approx(0.5 * 0.9, abs=0.001)


def test_enrich_leaders_legacy_address_key_used_as_proxy():
    """`address` is accepted as a legacy alias for proxy_address."""
    market_rows = [market_row("0xLEGACY", 100.0, 50.0)]
    leaders = [{"address": "0xLEGACY", "wallet_score": 0.5}]

    with patch("nansen_copytrader_overlay_general.pnl_by_market", return_value=market_rows), \
         patch("time.sleep"):
        result = og.enrich_leaders(leaders, market_id="42", dry_run=True)

    assert "NOT_IN_MARKET_LEADERBOARD" not in result[0]["reason_tags"]
    assert "MISSING_PROXY_ADDRESS" not in result[0]["reason_tags"]


def test_enrich_leaders_market_fetch_error_keeps_original_score():
    leaders = [{"proxy_address": "0xABC", "owner_address": "0xO", "wallet_score": 0.7}]

    with patch("nansen_copytrader_overlay_general.pnl_by_market",
               side_effect=NansenError("rate limit")), \
         patch("time.sleep"):
        result = og.enrich_leaders(leaders, market_id="42", dry_run=True)

    r = result[0]
    assert "MARKET_FETCH_ERROR" in r["reason_tags"]
    assert r["adjusted_score"] == pytest.approx(0.7 * 0.9, abs=0.001)


def test_enrich_leaders_market_ranking_ordering():
    """Wallet with better market-specific ROI should outrank a wallet with a
    higher original score but worse in-market PnL."""
    market_rows = [
        market_row("0xGOOD", 100.0, 150.0),   # ROI 1.5
        market_row("0xPOOR", 100.0, -50.0),   # loss
    ]
    leaders = [
        {"proxy_address": "0xGOOD", "owner_address": "0xGO", "wallet_score": 0.4},
        {"proxy_address": "0xPOOR", "owner_address": "0xPO", "wallet_score": 0.9},
    ]

    with patch("nansen_copytrader_overlay_general.pnl_by_market", return_value=market_rows), \
         patch("nansen_copytrader_overlay_general.address_summary", return_value=summary(resolved_count=1)), \
         patch("time.sleep"):
        result = og.enrich_leaders(leaders, market_id="42", dry_run=True)

    assert result[0]["proxy_address"] == "0xGOOD"
    assert result[1]["proxy_address"] == "0xPOOR"


def test_enrich_leaders_secondary_signal_pulled_after_summary_passes():
    market_rows = [market_row("0xGOOD", 100.0, 150.0)]
    pnl_data = load_fixture("pnl_by_address.json")
    trades_data = load_fixture("trades_by_address.json")
    leaders = [{"proxy_address": "0xGOOD", "owner_address": "0xGOOD-OWNER", "wallet_score": 0.5}]

    with patch("nansen_copytrader_overlay_general.pnl_by_market", return_value=market_rows), \
         patch("nansen_copytrader_overlay_general.address_summary", return_value=summary(resolved_count=10)), \
         patch("nansen_copytrader_overlay_general.pnl_by_address", return_value=pnl_data), \
         patch("nansen_copytrader_overlay_general.trades_by_address", return_value=trades_data), \
         patch("time.sleep"):
        result = og.enrich_leaders(leaders, market_id="42", dry_run=True)

    r = result[0]
    assert r["nansen_features"]["win_rate"] is not None
    assert r["nansen_features"]["market_score"] is not None


# ---------------------------------------------------------------------------
# Credit guards — max_wallets and CreditGuard exhaustion
# ---------------------------------------------------------------------------

def test_enrich_leaders_max_wallets_caps_enrichment():
    market_rows = [market_row(f"0xW{i}", 100.0, 50.0) for i in range(5)]
    leaders = [{"proxy_address": f"0xW{i}", "owner_address": f"0xO{i}", "wallet_score": i * 0.1}
               for i in range(5)]

    with patch("nansen_copytrader_overlay_general.pnl_by_market", return_value=market_rows), \
         patch("nansen_copytrader_overlay_general.address_summary", return_value=summary(resolved_count=1)), \
         patch("time.sleep"):
        result = og.enrich_leaders(leaders, market_id="42", dry_run=True, max_wallets=2)

    capped = [r for r in result if "CREDIT_GUARD_MAX_WALLETS" in r["reason_tags"]]
    assert len(capped) == 3


def test_enrich_leaders_credit_guard_exhaustion_keeps_remaining_at_original_score():
    market_rows = [market_row(f"0xW{i}", 100.0, 50.0) for i in range(3)]
    leaders = [{"proxy_address": f"0xW{i}", "owner_address": f"0xO{i}", "wallet_score": 0.5}
               for i in range(3)]

    guard = CreditGuard(max_calls=1)  # exhausted after the single pnl_by_market call

    with patch("nansen_copytrader_overlay_general.pnl_by_market", return_value=market_rows), \
         patch("nansen_copytrader_overlay_general.address_summary",
               side_effect=CreditGuardExceeded("budget spent")), \
         patch("time.sleep"):
        result = og.enrich_leaders(leaders, market_id="42", dry_run=True, guard=guard)

    exhausted = [r for r in result if "CREDIT_GUARD_EXHAUSTED" in r["reason_tags"]]
    assert len(exhausted) >= 1


# ---------------------------------------------------------------------------
# top_n
# ---------------------------------------------------------------------------

def test_enrich_leaders_empty_list():
    assert og.enrich_leaders([]) == []


def test_enrich_leaders_top_n():
    market_rows = [market_row(f"0xW{i}", 100.0, 50.0) for i in range(5)]
    leaders = [{"proxy_address": f"0xW{i}", "owner_address": f"0xO{i}", "wallet_score": i * 0.1}
               for i in range(5)]

    with patch("nansen_copytrader_overlay_general.pnl_by_market", return_value=market_rows), \
         patch("nansen_copytrader_overlay_general.address_summary", return_value=summary(resolved_count=1)), \
         patch("time.sleep"):
        result = og.enrich_leaders(leaders, market_id="42", dry_run=True, top_n=3)

    assert len(result) == 3
    assert result[0]["adjusted_rank"] == 1


# --- max_wallets cap validation (Greptile P1, simmer-sdk#306) -------------
#
# `if max_wallets and len(leaders) > max_wallets` made 0 falsy, so the cap
# branch was skipped and EVERY leader got enriched; -1 became a slice bound
# (ranked[:-1]), enriching all but the last. Both spent MORE of the caller's
# credits than the number they passed. Reproduced by Greptile against the real
# enrich_leaders path: 6 leaders + max_wallets=0 -> 6 enrichments, -1 -> 5.

@pytest.mark.parametrize("bad", [0, -1, -30])
def test_enrich_leaders_rejects_nonpositive_max_wallets(bad):
    leaders = [{"proxy_address": f"0x{i:040x}", "wallet_score": 0.5} for i in range(6)]
    with pytest.raises(ValueError, match="max_wallets must be >= 1"):
        og.enrich_leaders(leaders, market_id="1", max_wallets=bad)


def test_enrich_leaders_validates_before_spending_any_credits():
    """The cap check must run before the first Nansen call, not after."""
    leaders = [{"proxy_address": f"0x{i:040x}", "wallet_score": 0.5} for i in range(6)]
    guard = CreditGuard(max_calls=40)
    with pytest.raises(ValueError):
        og.enrich_leaders(leaders, market_id="1", max_wallets=0, guard=guard)
    assert guard.calls_made == 0
