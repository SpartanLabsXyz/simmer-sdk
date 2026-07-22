"""AXI response ergonomics for agent-facing SDK calls (SIM-4047)."""

from unittest.mock import MagicMock

from simmer_sdk.client import SimmerClient


def _client() -> SimmerClient:
    client = SimmerClient.__new__(SimmerClient)
    client._request = MagicMock()
    client._assert_not_readonly = MagicMock()
    client._get_held_markets = MagicMock(return_value={})
    client._agent_id = "agent-1"
    client._ows_wallet = None
    client._wallet_address = None
    client._private_key = None
    client._held_markets_cache = None
    client.live = True
    client.venue = "polymarket"
    return client


def test_get_markets_summary_is_compact_and_definitive_empty_state():
    client = _client()
    client._request.return_value = {"markets": [], "total": 0}

    result = client.get_markets(q="unlikely", response_mode="summary", include_hints=True)

    assert result == {
        "markets": [],
        "count": 0,
        "total": 0,
        "message": "No markets matched your filters.",
        "next_steps": [
            "Broaden q/tags or pass sort='volume' to find liquid markets.",
            "Use get_market_by_id(market_id) for full details before trading.",
        ],
    }


def test_get_markets_toon_uses_minimal_default_fields():
    client = _client()
    client._request.return_value = {
        "markets": [
            {
                "id": "m1",
                "question": "Will BTC close above 100k?",
                "status": "active",
                "current_probability": 0.61,
                "import_source": "polymarket",
                "resolution_criteria": "Verbose criteria omitted from compact mode.",
            }
        ],
        "total": 1200,
    }

    result = client.get_markets(response_mode="toon")

    assert result["count"] == 1
    assert result["total"] == 1200
    assert result["markets"] == [
        {
            "id": "m1",
            "question": "Will BTC close above 100k?",
            "status": "active",
            "import_source": "polymarket",
        }
    ]
    assert result["format"] == "toon"
    assert result["toon"].startswith("markets[1]{id,question,status,import_source}:")
    assert "resolution_criteria" not in result["toon"]


def test_get_positions_summary_keeps_default_list_mode_unchanged():
    client = _client()
    client._position_holder_cache = {}
    client._position_holder_ts = 0.0
    client._paper_portfolio = None
    client._request.return_value = {
        "positions": [
            {
                "market_id": "m1",
                "question": "Question",
                "shares_yes": 2,
                "shares_no": 0,
                "current_value": 1.2,
                "pnl": 0.2,
                "status": "active",
                "venue": "polymarket",
            }
        ]
    }

    full = client.get_positions()
    summary = client.get_positions(response_mode="summary")

    assert full[0].market_id == "m1"
    assert summary["positions"] == [
        {"market_id": "m1", "venue": "polymarket", "status": "active", "pnl": 0.2}
    ]


def test_get_trades_adds_message_and_compact_mode():
    client = _client()
    client._request.return_value = {
        "trades": [
            {
                "trade_id": "t1",
                "side": "yes",
                "venue": "sim",
                "status": "filled",
                "cost": 10.0,
                "shares": 20.0,
            }
        ],
        "total_count": 9,
    }

    full = client.get_trades()
    compact = client.get_trades(response_mode="summary", include_hints=True)

    assert full["message"] == "Showing 1 of 9 trades."
    assert compact["trades"] == [
        {"trade_id": "t1", "side": "yes", "venue": "sim", "status": "filled", "cost": 10.0}
    ]
    assert compact["next_steps"]


def test_trade_result_structures_common_failure_with_hint():
    client = _client()
    client._request.return_value = {
        "success": False,
        "market_id": "bad-market",
        "side": "yes",
        "error": "market_id not found",
    }

    result = client.trade("bad-market", "yes", amount=10, include_hints=True)

    assert result.success is False
    assert result.error_code == "market_not_found"
    assert "get_markets" in result.error_hint
    assert result.next_steps == [result.error_hint]


# ---------------------------------------------------------------------------
# _structured_error branch ordering (CTO review, SIM-4047)
# ---------------------------------------------------------------------------
# Agents ACT on error_hint, so a mislabelled bucket routes them to the wrong
# recovery call. The original ordering tested a bare "not found" first, which
# shadowed position/agent/skill errors into market_not_found.

import pytest
from simmer_sdk.client import SimmerClient


@pytest.mark.parametrize("message,expected_code", [
    # position errors must NOT be swallowed by the market branch
    ("position not found", "position_not_found"),
    ("Position not found for this market", "position_not_found"),
    ("No position found for this market", "position_not_found"),
    ("Missing position", "position_not_found"),
    # genuine market errors
    ("Market not found", "market_not_found"),
    ("market_id is required", "market_not_found"),
    ("Unknown market not found in catalog", "market_not_found"),
    # unrelated subjects must fall through to the generic bucket, not market
    ("Agent not found", "request_failed"),
    ("Skill not found", "request_failed"),
    ("Order not found", "request_failed"),
    # other buckets unchanged
    ("Insufficient balance", "insufficient_balance"),
    ("insufficient funds for this trade", "insufficient_balance"),
    ("Unauthorized", "credentials_invalid"),
    ("Invalid API key", "credentials_invalid"),
    ("No liquidity available", "no_liquidity"),
    ("Order not filled", "no_liquidity"),
    ("", "request_failed"),
    (None, "request_failed"),
])
def test_structured_error_buckets_by_most_specific_subject(message, expected_code):
    assert SimmerClient._structured_error(message)["code"] == expected_code


def test_structured_error_hint_points_at_the_matching_recovery_call():
    """The hint must name the call that actually resolves that bucket."""
    assert "get_positions" in SimmerClient._structured_error("position not found")["hint"]
    assert "get_markets" in SimmerClient._structured_error("Market not found")["hint"]
    assert "get_portfolio" in SimmerClient._structured_error("Insufficient balance")["hint"]
