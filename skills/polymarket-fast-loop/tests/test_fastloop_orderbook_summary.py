"""
Unit tests for fetch_orderbook_summary in polymarket-fast-loop.

Pins the CLOB book ordering contract: the API returns bids LOW→HIGH and asks
HIGH→LOW (documented in simmer_v3/polymarket_client.py and orderbook.py), so
best bid/ask must be read from the sorted front, never from index [0] of the
raw response. The old code read [0] on both sides, which computed the spread
as worst-ask minus worst-bid — inverting the 10% illiquidity gate: deep books
looked wide (skipped) while near-empty books looked tight (traded).

All tests are pure-unit: no network calls, no SIMMER_API_KEY required.
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

# Stub simmer_sdk and simmer_sdk.skill before importing the skill module
# (fastloop_trader imports from simmer_sdk.skill at module level)
_mock_cfg = {
    "entry_threshold": 0.05, "min_momentum_pct": 0.5, "max_position": 5.0,
    "signal_source": "binance", "lookback_minutes": 5, "min_time_remaining": 0,
    "asset": "BTC", "window": "5m", "volume_confidence": True, "daily_budget": 10.0,
    "use_fair_value": False, "fair_value_min_edge": 0.05, "btc_annual_vol": 0.55,
    "order_type": "GTC", "enable_news_veto": True,
}

_skill_stub = types.ModuleType("simmer_sdk.skill")
_skill_stub.load_config = lambda schema, file, slug=None: _mock_cfg.copy()
_skill_stub.update_config = lambda updates, file, slug=None: None
_skill_stub.get_config_path = lambda file: "/tmp/config.json"
_guards_stub = types.ModuleType("simmer_sdk.guards.news_recency_veto")
_guards_stub.load_macro_news_schedule = lambda path=None: {"events": []}
_guards_stub.news_window_match = lambda market_id, schedule, lookback_s=30, now=None: (False, None)

with patch.dict(sys.modules, {
    "simmer_sdk": MagicMock(),
    "simmer_sdk.skill": _skill_stub,
    "simmer_sdk.guards.news_recency_veto": _guards_stub,
}):
    import fastloop_trader as ft  # noqa: E402


# A liquid two-sided book in CLOB native ordering: bids LOW→HIGH (best = 0.48
# is LAST), asks HIGH→LOW (best = 0.52 is LAST). Six levels per side so the
# top-5 depth slice is distinguishable from a naive [:5] on the raw arrays.
_CLOB_BOOK = {
    "bids": [
        {"price": "0.10", "size": "10"},
        {"price": "0.20", "size": "10"},
        {"price": "0.30", "size": "10"},
        {"price": "0.40", "size": "10"},
        {"price": "0.45", "size": "10"},
        {"price": "0.48", "size": "10"},
    ],
    "asks": [
        {"price": "0.90", "size": "10"},
        {"price": "0.80", "size": "10"},
        {"price": "0.70", "size": "10"},
        {"price": "0.60", "size": "10"},
        {"price": "0.55", "size": "10"},
        {"price": "0.52", "size": "10"},
    ],
}


class TestFetchOrderbookSummary(unittest.TestCase):
    def _summary(self, book=_CLOB_BOOK):
        with patch.object(ft, "_api_request", return_value=book):
            return ft.fetch_orderbook_summary(["yes-token", "no-token"])

    def test_best_prices_come_from_the_touch_not_index_zero(self):
        s = self._summary()
        self.assertAlmostEqual(s["best_bid"], 0.48)
        self.assertAlmostEqual(s["best_ask"], 0.52)

    def test_liquid_book_passes_the_spread_gate(self):
        # Touch spread 0.04 on a 0.50 mid = 8%: under MAX_SPREAD_PCT. The old
        # [0]-indexed read computed 0.90 - 0.10 = 0.80 spread (160% of mid)
        # and skipped exactly the liquid books the gate exists to keep.
        s = self._summary()
        self.assertAlmostEqual(s["spread_pct"], 0.04 / 0.50)
        self.assertLess(s["spread_pct"], ft.MAX_SPREAD_PCT)

    def test_depth_sums_the_five_levels_nearest_the_touch(self):
        s = self._summary()
        # bids: 0.48+0.45+0.40+0.30+0.20 (drops the 0.10 tail), size 10 each
        self.assertAlmostEqual(s["bid_depth_usd"], 18.3)
        # asks: 0.52+0.55+0.60+0.70+0.80 (drops the 0.90 tail)
        self.assertAlmostEqual(s["ask_depth_usd"], 31.7)

    def test_one_sided_book_returns_none(self):
        s = self._summary({"bids": _CLOB_BOOK["bids"], "asks": []})
        self.assertIsNone(s)


if __name__ == "__main__":
    unittest.main()
