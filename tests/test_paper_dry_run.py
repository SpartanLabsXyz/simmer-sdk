"""Paper ``trade(dry_run=True)`` must not mutate PaperPortfolio.

Live-venue dry_run already skips apply (no signed order, no held-markets
cache write). Paper used to call ``log_trade`` before checking dry_run, so
a preview then a real paper fill on the same market double-booked shares
and cost_basis (Grok Bot dogfood, issue #345).
"""

from unittest.mock import MagicMock

import pytest

from simmer_sdk.client import SimmerClient
from simmer_sdk.paper import PaperPortfolio


_OPEN_MARKET = {
    "question": "Will NYC hit 90F?",
    "external_price_yes": 0.50,
    "current_probability": 0.50,
    "status": "active",
}
_RESOLVED_YES_MARKET = {
    "question": "Resolved YES",
    "external_price_yes": 1.0,
    "current_probability": 1.0,
    "status": "resolved",
    "outcome": "yes",
}


def _paper_client(starting_balance: float = 10_000.0, markets=None) -> SimmerClient:
    client = SimmerClient.__new__(SimmerClient)
    client._readonly = False
    client.live = False
    client.venue = "polymarket"
    client._paper_portfolio = PaperPortfolio(starting_balance=starting_balance)
    markets = markets or {"m1": _OPEN_MARKET}
    client.get_market_context = MagicMock(
        side_effect=lambda market_id, *a, **k: {"market": markets[market_id]}
    )
    return client


def test_paper_dry_run_leaves_portfolio_unchanged():
    client = _paper_client()
    before = client._paper_portfolio.summary()

    result = client.trade("m1", "yes", amount=1.0, venue="polymarket", dry_run=True)

    after = client._paper_portfolio.summary()
    assert result.success
    assert result.simulated
    assert result.cost == 1.0
    assert result.shares_bought > 0
    assert after == before
    assert after["balance"] == 10_000.0
    assert after["open_positions"] == 0
    assert after["positions"] == {}


def test_paper_trade_after_dry_run_books_once():
    """Preview then place must apply a single fill, not stack two."""
    client = _paper_client()

    preview = client.trade("m1", "yes", amount=1.0, venue="polymarket", dry_run=True)
    placed = client.trade("m1", "yes", amount=1.0, venue="polymarket")

    summary = client._paper_portfolio.summary()
    assert preview.success
    assert placed.success
    assert summary["balance"] == 9_999.0
    assert summary["open_positions"] == 1
    assert summary["positions"]["m1"]["cost_basis"] == 1.0
    # Mid 0.50 + 1¢ half-spread → fill 0.51, so ~1.96 shares, not ~3.92.
    # TradeResult rounds shares_bought to 6 decimals; the book stores raw.
    assert summary["positions"]["m1"]["shares_yes"] == pytest.approx(1.0 / 0.51)
    assert placed.shares_bought == pytest.approx(1.0 / 0.51, abs=1e-6)


def _client_with_resolved_position() -> SimmerClient:
    """Balance 50 and 100 YES shares of a market that has since resolved YES."""
    client = _paper_client(
        starting_balance=100.0,
        markets={"m1": _RESOLVED_YES_MARKET, "m2": _OPEN_MARKET},
    )
    client._paper_portfolio.log_trade("m1", "yes", "buy", 100.0, 50.0, 0.50, venue="polymarket")
    assert client._paper_portfolio.balance == 50.0
    return client


def test_paper_dry_run_settles_before_pricing_a_buy():
    """A dry run must see the same book the real trade would.

    Settlement is the book catching up with a resolved market, not the
    previewed order. With it skipped, the preview said "insufficient
    balance" for a buy that then succeeded once settlement credited the win.
    """
    client = _client_with_resolved_position()

    preview = client.trade("m2", "yes", amount=80.0, venue="polymarket", dry_run=True)
    placed = client.trade("m2", "yes", amount=80.0, venue="polymarket")

    assert preview.success, preview.error
    assert placed.success, placed.error
    # 50 + 100 settlement - 80 buy; the dry run itself booked nothing.
    assert client._paper_portfolio.balance == 70.0
    assert client._paper_portfolio.summary()["positions"]["m2"]["cost_basis"] == 80.0


def test_paper_dry_run_sell_of_settled_position_matches_real_sell():
    """A dry run must not report a fill on shares settlement already removed."""
    client = _client_with_resolved_position()

    preview = client.trade("m1", "yes", shares=100.0, action="sell", venue="polymarket", dry_run=True)
    placed = client.trade("m1", "yes", shares=100.0, action="sell", venue="polymarket")

    assert not preview.success
    assert not placed.success
    assert preview.error == placed.error
    assert "No paper position to sell" in preview.error
