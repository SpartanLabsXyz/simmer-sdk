"""
Regression tests for SIM-2371: sources=None crash in check_exit_opportunities.

Verifies that a position dict with sources=None does not raise TypeError and
that the keyword fallback still matches weather positions correctly.

Pure-unit: no network calls, no SDK required.
"""

import sys
import os
import types
import unittest
from unittest.mock import patch, MagicMock

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

_mock_cfg = {
    "entry_threshold": 0.15,
    "exit_threshold": 0.45,
    "max_position_usd": 2.00,
    "sizing_pct": 0.05,
    "max_trades_per_run": 5,
    "locations": "NYC",
    "binary_only": False,
    "slippage_max": 0.15,
    "min_liquidity": 0.0,
    "order_type": "GTC",
    "vol_targeting": False,
    "target_vol": 0.20,
    "vol_max_leverage": 2.0,
    "vol_min_allocation": 0.2,
    "vol_span": 10,
    # SIM-2420 source-agreement knobs
    "require_source_agreement": False,
    "canary_on_adjacent": True,
    "max_canary_usd": 2.0,
    "max_source_spread_f": 2.0,
}

_skill_mod = types.ModuleType("simmer_sdk.skill")
_skill_mod.load_config = lambda schema, file, slug=None: _mock_cfg.copy()
_skill_mod.update_config = lambda updates, file, slug=None: None
_skill_mod.get_config_path = lambda file: "/tmp/config.json"
sys.modules["simmer_sdk"] = MagicMock()
sys.modules["simmer_sdk.skill"] = _skill_mod

import weather_trader as wt  # noqa: E402


class TestSourcesNoneExitScan(unittest.TestCase):

    def _pos(self, sources, question, price=0.20, shares=10.0):
        return {
            "market_id": "m-test",
            "sources": sources,
            "question": question,
            "current_price": price,
            "shares_yes": shares,
            "status": "open",
        }

    def test_sources_none_does_not_crash(self):
        """sources=None must not raise TypeError in check_exit_opportunities."""
        pos = self._pos(sources=None, question="will the highest temperature in nyc exceed 80°f?")
        with patch.object(wt, "get_positions", return_value=[pos]):
            # Must not raise — this is the regression guard for SIM-2371
            result = wt.check_exit_opportunities(dry_run=True, use_safeguards=False)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_keyword_fallback_matches_when_sources_none(self):
        """Position with sources=None is still matched via question keyword fallback."""
        # Price above EXIT_THRESHOLD so exits_found increments if the position is matched
        pos = self._pos(
            sources=None,
            question="will the highest temperature in nyc exceed 80°f?",
            price=0.90,   # > EXIT_THRESHOLD (0.45)
            shares=10.0,
        )
        with patch.object(wt, "get_positions", return_value=[pos]), \
             patch.object(wt, "execute_sell", return_value={"success": True, "simulated": True}):
            exits_found, exits_executed = wt.check_exit_opportunities(
                dry_run=True, use_safeguards=False
            )
        # If keyword fallback didn't match, weather_positions would be empty and
        # check_exit_opportunities would return (0, 0) from the early-exit guard.
        # exits_found=1 proves the position was matched and processed.
        self.assertEqual(exits_found, 1)
        self.assertEqual(exits_executed, 1)

    def test_no_keyword_no_source_skipped(self):
        """Position with sources=None and no weather keyword is NOT matched."""
        pos = self._pos(sources=None, question="will the next us president be a democrat?")
        with patch.object(wt, "get_positions", return_value=[pos]):
            exits_found, exits_executed = wt.check_exit_opportunities(
                dry_run=True, use_safeguards=False
            )
        # No weather match → returns immediately from empty weather_positions guard
        self.assertEqual(exits_found, 0)
        self.assertEqual(exits_executed, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
