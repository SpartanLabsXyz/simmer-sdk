"""Regression: result + param dataclasses must import from top-level `simmer_sdk`.

Herman ledger 2026-05-21: noted that `TradeResult` could only be imported
from `simmer_sdk.client`, not from `simmer_sdk` itself. He explicitly chose
not to file a separate ticket (out of SIM-2238 scope), but the inconsistency
is real — the public API surface should be flat.
"""

from __future__ import annotations


def test_trade_result_importable_from_top_level():
    """`from simmer_sdk import TradeResult` must work."""
    from simmer_sdk import TradeResult
    # Sanity: it's the real dataclass, not a stub
    assert TradeResult.__dataclass_fields__["shares_sold"].default == 0


def test_real_trade_result_importable_from_top_level():
    from simmer_sdk import RealTradeResult
    assert RealTradeResult.__dataclass_fields__["success"] is not None


def test_polymarket_order_params_importable_from_top_level():
    from simmer_sdk import PolymarketOrderParams
    assert PolymarketOrderParams.__dataclass_fields__["token_id"] is not None


def test_maker_rewards_status_importable_from_top_level():
    from simmer_sdk import MakerRewardsStatus
    assert MakerRewardsStatus.__dataclass_fields__["eligible"] is not None


def test_top_level_and_client_paths_resolve_to_same_class():
    """Defensive: both import paths must return the same class object (no shim)."""
    from simmer_sdk import TradeResult as TopLevel
    from simmer_sdk.client import TradeResult as ClientLevel
    assert TopLevel is ClientLevel
