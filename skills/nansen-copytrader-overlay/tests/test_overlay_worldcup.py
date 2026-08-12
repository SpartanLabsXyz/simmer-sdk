"""Tests for nansen_copytrader_overlay_worldcup.py — mocked, no Nansen calls."""

import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import patch
import pytest

import nansen_copytrader_overlay_worldcup as wc
from nansen_adapter import NansenError


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
# _is_wc_market
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question,expected", [
    ("Will Brazil win the 2026 World Cup?", True),
    ("Will France win Group D at fifwc?", True),
    ("Will USA advance in WC 2026 knockout?", True),
    ("Bitcoin above $100k by end of 2026?", False),
    ("Will Messi score in the Semi-Final 2026?", True),
    ("Will it rain in London tomorrow?", False),
    ("fifwc group winner Argentina", True),
])
def test_is_wc_market(question, expected):
    assert wc._is_wc_market(question) == expected


# ---------------------------------------------------------------------------
# _compute_wc_quality_score (secondary signal)
# ---------------------------------------------------------------------------

def test_wc_quality_score_specialist():
    general_features = {
        "win_rate": 0.75,
        "avg_roi": 1.2,
        "consistency": 0.70,
        "concentration": 0.30,
        "resolved_count": 10,
    }
    wc_features = {
        "wc_resolved_count": 8,   # 80% WC focus → specialist
        "wc_win_rate": 0.80,
        "wc_avg_roi": 2.5,
    }
    score, tags = wc._compute_wc_quality_score(general_features, wc_features, distinct_markets=10)
    assert score > 0.5
    assert "WC_SPECIALIST" in tags
    assert "WC_HIGH_WIN_RATE" in tags


def test_wc_quality_score_novice():
    general_features = {
        "win_rate": 0.60,
        "avg_roi": 0.5,
        "consistency": 0.6,
        "concentration": 0.4,
        "resolved_count": 20,
    }
    wc_features = {
        "wc_resolved_count": 1,   # only 1 WC market → novice
        "wc_win_rate": 1.0,
        "wc_avg_roi": 5.0,
    }
    score, tags = wc._compute_wc_quality_score(general_features, wc_features, distinct_markets=18)
    assert "WC_NOVICE" in tags


def test_wc_quality_score_insufficient_history():
    general_features = {
        "win_rate": None, "avg_roi": None, "consistency": None,
        "concentration": None, "resolved_count": 1,
    }
    wc_features = {"wc_resolved_count": 0, "wc_win_rate": None, "wc_avg_roi": None}
    score, tags = wc._compute_wc_quality_score(general_features, wc_features, distinct_markets=1)
    assert score == 0.0
    assert any("INSUFFICIENT_HISTORY" in t for t in tags)


# ---------------------------------------------------------------------------
# enrich_leaders (WC variant) — market-first path
# ---------------------------------------------------------------------------

def test_wc_enrich_leaders_empty():
    assert wc.enrich_leaders([]) == []


def test_wc_enrich_leaders_market_first_primary_signal():
    market_rows = [market_row("0xABC", 100.0, 80.0)]
    leaders = [{"proxy_address": "0xABC", "owner_address": "0xABC-OWNER", "wallet_score": 0.70}]

    with patch("nansen_copytrader_overlay_worldcup.fetch_market_leaderboard",
               return_value=({"0xabc": (0, market_rows[0])}, 1)), \
         patch("nansen_copytrader_overlay_worldcup.address_summary", return_value=summary(resolved_count=1)), \
         patch("time.sleep"):
        result = wc.enrich_leaders(leaders, market_id="999", dry_run=True)

    assert len(result) == 1
    r = result[0]
    assert r["dry_run"] is True
    assert r["nansen_features"]["market_score"] is not None
    assert "wc_resolved_count" in r["nansen_features"]
    assert r["adjusted_rank"] == 1


def test_wc_enrich_leaders_detects_wc_markets_in_secondary_signal():
    """WC markets in the fixture should be counted in wc_resolved_count
    once the address_summary pre-filter passes and the full pull runs."""
    market_rows = [market_row("0xABC", 100.0, 80.0)]
    pnl_data = load_fixture("pnl_by_address.json")
    trades_data = load_fixture("trades_by_address.json")

    # The fixture has "Brazil win the 2026 World Cup" (WC) and
    # "France win Group D" (WC) and "Bitcoin" (not WC) — both WC markets
    # are resolved → wc_resolved_count should be 2.
    leaders = [{"proxy_address": "0xABC", "owner_address": "0xABC-OWNER", "wallet_score": 0.5}]

    with patch("nansen_copytrader_overlay_worldcup.fetch_market_leaderboard",
               return_value=({"0xabc": (0, market_rows[0])}, 1)), \
         patch("nansen_copytrader_overlay_worldcup.address_summary", return_value=summary(resolved_count=10)), \
         patch("nansen_copytrader_overlay_worldcup.pnl_by_address", return_value=pnl_data), \
         patch("nansen_copytrader_overlay_worldcup.trades_by_address", return_value=trades_data), \
         patch("time.sleep"):
        result = wc.enrich_leaders(leaders, market_id="999", dry_run=True)

    assert result[0]["nansen_features"]["wc_resolved_count"] == 2


def test_wc_enrich_leaders_not_in_market_leaderboard():
    leaders = [{"proxy_address": "0xNOTFOUND", "owner_address": "0xO", "wallet_score": 0.5}]

    with patch("nansen_copytrader_overlay_worldcup.fetch_market_leaderboard",
               return_value=({}, 0)), \
         patch("time.sleep"):
        result = wc.enrich_leaders(leaders, market_id="999", dry_run=True)

    assert "NOT_IN_MARKET_LEADERBOARD" in result[0]["reason_tags"]
    assert result[0]["adjusted_score"] == pytest.approx(0.5 * 0.9, abs=0.001)


def test_wc_enrich_leaders_missing_proxy_address():
    leaders = [{"owner_address": "0xOWNER", "wallet_score": 0.5}]

    with patch("nansen_copytrader_overlay_worldcup.fetch_market_leaderboard",
               return_value=({}, 0)), \
         patch("time.sleep"):
        result = wc.enrich_leaders(leaders, market_id="999", dry_run=True)

    assert "MISSING_PROXY_ADDRESS" in result[0]["reason_tags"]


def test_wc_enrich_leaders_market_ordering():
    """Wallet with better in-market PnL should outrank one with a higher
    original score but worse market-specific performance."""
    market_rows = [
        market_row("0xSPECIALIST", 100.0, 150.0),  # ROI 1.5
        market_row("0xGENERALIST", 100.0, -20.0),  # loss
    ]
    leaderboard = {
        "0xspecialist": (0, market_rows[0]),
        "0xgeneralist": (1, market_rows[1]),
    }
    leaders = [
        {"proxy_address": "0xSPECIALIST", "owner_address": "0xS", "wallet_score": 0.4},
        {"proxy_address": "0xGENERALIST", "owner_address": "0xG", "wallet_score": 0.8},
    ]

    with patch("nansen_copytrader_overlay_worldcup.fetch_market_leaderboard",
               return_value=(leaderboard, 2)), \
         patch("nansen_copytrader_overlay_worldcup.address_summary", return_value=summary(resolved_count=1)), \
         patch("time.sleep"):
        result = wc.enrich_leaders(leaders, market_id="999", dry_run=True)

    assert result[0]["proxy_address"] == "0xSPECIALIST"


# ---------------------------------------------------------------------------
# enrich_leaders (WC variant) — legacy path (no market_id)
# ---------------------------------------------------------------------------

def test_wc_enrich_leaders_legacy_nansen_error_graceful():
    leaders = [{"proxy_address": "0xFAIL", "owner_address": "0xFAIL-OWNER", "wallet_score": 0.5}]

    with patch("nansen_copytrader_overlay_worldcup.address_summary",
               side_effect=NansenError("timeout")), \
         patch("time.sleep"):
        result = wc.enrich_leaders(leaders, dry_run=True)

    assert "NANSEN_FETCH_ERROR" in result[0]["reason_tags"]


def test_wc_enrich_leaders_legacy_missing_proxy_address():
    leaders = [{"owner_address": "0xOWNER", "wallet_score": 0.5}]

    result = wc.enrich_leaders(leaders, dry_run=True)

    assert "MISSING_PROXY_ADDRESS" in result[0]["reason_tags"]


# --- max_wallets cap validation (simmer-sdk#306) --------------------------
#
# Same bug as the general overlay, in the World Cup variant. Greptile only
# flagged the general one; this file carried the identical
# `if max_wallets and ...` falsy-zero cap bypass.

@pytest.mark.parametrize("bad", [0, -1, -30])
def test_enrich_leaders_rejects_nonpositive_max_wallets(bad):
    leaders = [{"proxy_address": f"0x{i:040x}", "wallet_score": 0.5} for i in range(6)]
    with pytest.raises(ValueError, match="max_wallets must be >= 1"):
        wc.enrich_leaders(leaders, market_id="1", max_wallets=bad)
