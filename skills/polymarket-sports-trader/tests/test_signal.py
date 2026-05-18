"""
Unit tests for the sports-trader decision logic.

Pure-unit: no network calls, no SDK required. We stub simmer_sdk.skill before
importing sports_trader so the module's load_config call doesn't try to read
config.json from disk.
"""

import os
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)


def _mock_cfg():
    return {
        "min_volume_usd": 25000,
        "max_markets_per_run": 8,
        "divergence_min": 0.08,
        "max_position_usd": 5.0,
        "min_position_usd": 2.0,
        "sizing_pct": 0.05,
        "max_trades_per_run": 4,
        "extreme_price_floor": 0.05,
        "extreme_price_ceil": 0.95,
        "slippage_max_pct": 0.05,
        "auto_import": False,
        "order_type": "GTC",
        "llm_base_url": "https://openrouter.ai/api/v1",
        "llm_model": "anthropic/claude-haiku-4.5",
        "llm_timeout_secs": 30,
    }


with patch.dict("sys.modules", {"simmer_sdk": MagicMock(), "simmer_sdk.skill": MagicMock()}):
    skill_module = types.ModuleType("simmer_sdk.skill")
    skill_module.load_config = lambda schema, file, slug=None: _mock_cfg()
    skill_module.update_config = lambda *a, **kw: None
    skill_module.get_config_path = lambda *a, **kw: "/tmp/config.json"
    sys.modules["simmer_sdk"] = MagicMock()
    sys.modules["simmer_sdk.skill"] = skill_module

    import sports_trader as st


def _market(yes_price=0.50, current_probability=0.50, question="LAL vs BOS — Lakers win", resolves_at=None):
    return SimpleNamespace(
        id="m-1",
        question=question,
        external_price_yes=yes_price,
        current_probability=current_probability,
        resolves_at=resolves_at,
    )


class TestExtremePriceSkip(unittest.TestCase):
    """Markets above the ceiling or below the floor are skipped before we burn an LLM call."""

    def test_skip_above_ceiling(self):
        with patch.object(st, "llm_fair_value") as fake_llm:
            d = st.evaluate_market(_market(yes_price=0.97))
            self.assertTrue(d.get("skip"))
            self.assertIn("extreme price", d.get("reason", ""))
            fake_llm.assert_not_called()

    def test_skip_below_floor(self):
        with patch.object(st, "llm_fair_value") as fake_llm:
            d = st.evaluate_market(_market(yes_price=0.02))
            self.assertTrue(d.get("skip"))
            self.assertIn("extreme price", d.get("reason", ""))
            fake_llm.assert_not_called()

    def test_no_price_skip(self):
        with patch.object(st, "llm_fair_value") as fake_llm:
            d = st.evaluate_market(_market(yes_price=None, current_probability=None))
            self.assertTrue(d.get("skip"))
            self.assertIn("no yes price", d.get("reason", ""))
            fake_llm.assert_not_called()


class TestLLMUnavailable(unittest.TestCase):
    def test_skip_when_llm_returns_none(self):
        with patch.object(st, "llm_fair_value", return_value=None):
            d = st.evaluate_market(_market(yes_price=0.50))
            self.assertTrue(d.get("skip"))
            self.assertEqual(d.get("reason"), "llm signal unavailable")


class TestDivergenceThreshold(unittest.TestCase):
    """The divergence_min threshold (default 0.08) gates whether a trade fires."""

    def test_below_threshold_skipped(self):
        with patch.object(st, "llm_fair_value", return_value={"fair_yes": 0.55, "confidence": "medium", "reasoning": "."}):
            d = st.evaluate_market(_market(yes_price=0.50))
            self.assertTrue(d.get("skip"))
            self.assertIn("below threshold", d.get("reason", ""))

    def test_exactly_threshold_skipped(self):
        # edge = 0.08 with divergence_min 0.08 → strict-less-than → skip
        with patch.object(st, "llm_fair_value", return_value={"fair_yes": 0.58, "confidence": "medium", "reasoning": "."}):
            d = st.evaluate_market(_market(yes_price=0.50))
            self.assertTrue(d.get("skip"))

    def test_above_threshold_yes_buy(self):
        with patch.object(st, "llm_fair_value", return_value={"fair_yes": 0.70, "confidence": "high", "reasoning": "starter back"}):
            d = st.evaluate_market(_market(yes_price=0.50))
            self.assertFalse(d.get("skip"))
            self.assertEqual(d["side"], "yes")
            self.assertAlmostEqual(d["edge"], 0.20, places=4)

    def test_above_threshold_no_buy(self):
        with patch.object(st, "llm_fair_value", return_value={"fair_yes": 0.30, "confidence": "high", "reasoning": "injury news"}):
            d = st.evaluate_market(_market(yes_price=0.50))
            self.assertFalse(d.get("skip"))
            self.assertEqual(d["side"], "no")
            self.assertAlmostEqual(d["edge"], -0.20, places=4)


class TestPositionSizing(unittest.TestCase):
    def test_fixed_sizing_returns_max_position(self):
        size = st.calculate_position_size(smart_sizing=False)
        self.assertEqual(size, 5.0)

    def test_smart_sizing_caps_at_max(self):
        with patch.object(st, "get_portfolio", return_value={"balance_usdc": 100_000}):
            # 5% of $100k = $5000 → capped at max_position_usd = $5
            size = st.calculate_position_size(smart_sizing=True)
            self.assertEqual(size, 5.0)

    def test_smart_sizing_floors_at_min(self):
        with patch.object(st, "get_portfolio", return_value={"balance_usdc": 10}):
            # 5% of $10 = $0.50 → floored at min_position_usd = $2
            size = st.calculate_position_size(smart_sizing=True)
            self.assertEqual(size, 2.0)

    def test_smart_sizing_no_portfolio_falls_back(self):
        with patch.object(st, "get_portfolio", return_value=None):
            size = st.calculate_position_size(smart_sizing=True)
            self.assertEqual(size, 5.0)


if __name__ == "__main__":
    unittest.main()
