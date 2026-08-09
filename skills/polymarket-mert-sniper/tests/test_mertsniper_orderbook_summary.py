"""
Unit tests for fetch_orderbook_summary in polymarket-mert-sniper.

Pins the CLOB book ordering contract: the API returns bids LOW→HIGH and asks
HIGH→LOW (documented in simmer_v3/polymarket_client.py and orderbook.py), so
best bid/ask must be read from the sorted front, never from index [0] of the
raw response.

mert-sniper's exposure is the DEPTH gate, not the spread: it reads
bid_depth_usd / ask_depth_usd against MIN_BOOK_DEPTH_USD to decide whether a
book is too thin to trade. A raw `[:5]` slice on CLOB-ordered arrays sums the
five levels FURTHEST from the touch, so that gate was measuring the back of
the book.

All tests are pure-unit: no network calls, no SIMMER_API_KEY required.
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

_mock_cfg = {
    "market_filter": "", "max_bet_usd": 10.00, "expiry_window_mins": 8,
    "min_split": 0.60, "max_trades_per_run": 5, "sizing_pct": 0.05,
    "order_type": "GTC", "fee_buffer": 0.02, "min_edge": 0.0,
    "enable_news_veto": True,
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
    import mert_sniper as ms  # noqa: E402


# Liquid two-sided book in CLOB native ordering: bids LOW→HIGH (best 0.48 is
# LAST), asks HIGH→LOW (best 0.52 is LAST). The near-touch levels carry real
# size and the far levels are near-worthless, so a back-of-book depth sum is
# numerically distinguishable from a touch-side one.
_CLOB_BOOK = {
    "bids": [
        {"price": "0.10", "size": "5"},
        {"price": "0.20", "size": "5"},
        {"price": "0.30", "size": "5"},
        {"price": "0.40", "size": "100"},
        {"price": "0.45", "size": "100"},
        {"price": "0.48", "size": "100"},
    ],
    "asks": [
        {"price": "0.90", "size": "5"},
        {"price": "0.80", "size": "5"},
        {"price": "0.70", "size": "5"},
        {"price": "0.60", "size": "100"},
        {"price": "0.55", "size": "100"},
        {"price": "0.52", "size": "100"},
    ],
}


class TestFetchOrderbookSummary(unittest.TestCase):
    def _summary(self, book=_CLOB_BOOK):
        with patch.object(ms, "_clob_request", return_value=book):
            return ms.fetch_orderbook_summary("yes-token")

    def test_best_prices_come_from_the_touch_not_index_zero(self):
        s = self._summary()
        self.assertAlmostEqual(s["best_bid"], 0.48)
        self.assertAlmostEqual(s["best_ask"], 0.52)

    def test_depth_sums_the_five_levels_nearest_the_touch(self):
        s = self._summary()
        # bids: 0.48*100 + 0.45*100 + 0.40*100 + 0.30*5 + 0.20*5 = 135.5
        self.assertAlmostEqual(s["bid_depth_usd"], 135.5)
        # asks: 0.52*100 + 0.55*100 + 0.60*100 + 0.70*5 + 0.80*5 = 174.5
        self.assertAlmostEqual(s["ask_depth_usd"], 174.5)

    def test_liquid_book_clears_the_min_depth_gate(self):
        # The old [0]-indexed read summed the FAR side: bids 0.10*5 + 0.20*5 +
        # 0.30*5 + 0.40*100 + 0.45*100 = 88.0 on the bid side and, on the asks,
        # the most expensive levels — so a genuinely deep book could be skipped
        # as "thin" (or a junk-laden one waved through) on back-of-book size.
        s = self._summary()
        self.assertGreater(s["bid_depth_usd"], ms.MIN_BOOK_DEPTH_USD)
        self.assertGreater(s["ask_depth_usd"], ms.MIN_BOOK_DEPTH_USD)

    def test_spread_is_the_touch_spread(self):
        s = self._summary()
        self.assertAlmostEqual(s["spread_pct"], 0.04 / 0.50)

    def test_one_sided_book_returns_none(self):
        self.assertIsNone(self._summary({"bids": _CLOB_BOOK["bids"], "asks": []}))

    def test_unparseable_ask_price_does_not_fabricate_a_zero_touch(self):
        book = {
            "bids": _CLOB_BOOK["bids"],
            "asks": [{"size": "10"}, {"price": "junk", "size": "10"}] + _CLOB_BOOK["asks"],
        }
        s = self._summary(book)
        self.assertAlmostEqual(s["best_ask"], 0.52)
        self.assertGreater(s["spread_pct"], 0)

    def test_malformed_size_deep_in_the_book_does_not_discard_the_summary(self):
        deep_junk = [{"price": "0.05", "size": "not-a-number"}]
        s = self._summary({"bids": deep_junk + _CLOB_BOOK["bids"], "asks": _CLOB_BOOK["asks"]})
        self.assertIsNotNone(s)
        self.assertAlmostEqual(s["bid_depth_usd"], 135.5)


if __name__ == "__main__":
    unittest.main()
