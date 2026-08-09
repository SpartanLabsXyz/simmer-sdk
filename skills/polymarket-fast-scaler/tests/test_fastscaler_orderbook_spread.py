"""
Unit tests for fetch_orderbook_spread in polymarket-fast-scaler.

Pins the CLOB book ordering contract: the API returns bids LOW→HIGH and asks
HIGH→LOW (documented in simmer_v3/polymarket_client.py and orderbook.py), so
best bid/ask must be read from the sorted front, never from index [0] of the
raw response.

fast-scaler has no pre-fetched `spread_cents` fallback — every live run that
reaches the spread check calls this function, and its result gates directly on
MAX_SPREAD_PCT. Reading [0] computed worst-ask minus worst-bid (the full book
width), so deep books were skipped as "illiquid" while near-empty one-level
books computed a true touch spread and traded.

All tests are pure-unit: no network calls, no SIMMER_API_KEY required.
"""
import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

# Source the mock config from the skill's own config.json so this test doesn't
# drift as CONFIG_SCHEMA gains keys.
with open(os.path.join(_SKILL_DIR, "config.json")) as fh:
    _mock_cfg = json.load(fh)

_skill_stub = types.ModuleType("simmer_sdk.skill")
_skill_stub.load_config = lambda schema, file, slug=None: dict(_mock_cfg)
_skill_stub.update_config = lambda updates, file, slug=None: None
_skill_stub.get_config_path = lambda file: "/tmp/config.json"

with patch.dict(sys.modules, {
    "simmer_sdk": MagicMock(),
    "simmer_sdk.skill": _skill_stub,
}):
    import fast_scaler as fs  # noqa: E402


# CLOB native ordering: bids LOW→HIGH (best 0.48 LAST), asks HIGH→LOW (best
# 0.52 LAST). Touch spread is 0.04 on a 0.50 mid = 8%, under MAX_SPREAD_PCT;
# the old [0] read gave 0.90 - 0.10 = 0.80 (160% of mid) and skipped the book.
_CLOB_BOOK = {
    "bids": [
        {"price": "0.10", "size": "10"},
        {"price": "0.30", "size": "10"},
        {"price": "0.45", "size": "10"},
        {"price": "0.48", "size": "10"},
    ],
    "asks": [
        {"price": "0.90", "size": "10"},
        {"price": "0.70", "size": "10"},
        {"price": "0.55", "size": "10"},
        {"price": "0.52", "size": "10"},
    ],
}


class TestFetchOrderbookSpread(unittest.TestCase):
    def _spread(self, book=_CLOB_BOOK):
        with patch.object(fs, "_api_request", return_value=book):
            return fs.fetch_orderbook_spread(["yes-token", "no-token"])

    def test_spread_is_the_touch_spread_not_the_book_width(self):
        self.assertAlmostEqual(self._spread(), 0.04 / 0.50)

    def test_liquid_book_passes_the_spread_gate(self):
        self.assertLess(self._spread(), fs.MAX_SPREAD_PCT)

    def test_one_sided_book_returns_none(self):
        self.assertIsNone(self._spread({"bids": _CLOB_BOOK["bids"], "asks": []}))

    def test_unparseable_ask_price_does_not_fabricate_a_zero_touch(self):
        # A zero-defaulted ask sorts to the front and yields a NEGATIVE spread,
        # which passes `> MAX_SPREAD_PCT` — fail-open on the illiquidity gate.
        book = {
            "bids": _CLOB_BOOK["bids"],
            "asks": [{"size": "10"}, {"price": "junk", "size": "10"}] + _CLOB_BOOK["asks"],
        }
        s = self._spread(book)
        self.assertAlmostEqual(s, 0.04 / 0.50)
        self.assertGreater(s, 0)

    def test_every_level_unparseable_returns_none(self):
        self.assertIsNone(self._spread({"bids": [{"price": "x"}], "asks": _CLOB_BOOK["asks"]}))


if __name__ == "__main__":
    unittest.main()
