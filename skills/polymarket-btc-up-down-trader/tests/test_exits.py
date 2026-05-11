"""
Unit tests for exit trigger evaluation in polymarket-btc-up-down-trader.

Tests each of the three exit triggers in isolation and in combination:
  1. time_cap    — fire when hours_to_resolution ≤ exit_before_resolution_hours
  2. target_hit  — fire when (current - entry) / max_gain ≥ target_hit_capture_pct
  3. volume_spike — fire when current 10m volume ≥ multiplier × baseline

All tests are pure-unit: no network calls, no SDK required.
"""

import sys
import os
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

# Add the skill directory to sys.path so we can import strategy directly
_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

# Patch simmer_sdk.skill before importing strategy (strategy runs load_config at import)
_mock_cfg = {
    "entry_threshold": 0.05,
    "min_momentum_pct": 0.3,
    "max_position": 10.0,
    "lookback_minutes": 30,
    "daily_budget": 50.0,
    "min_hours_to_resolution": 4.0,
    "exit_before_resolution_hours": 1.0,
    "volume_spike_exit_multiplier": 3.0,
    "target_hit_capture_pct": 0.85,
    "volume_baseline_windows": 6,
}

with patch.dict("sys.modules", {"simmer_sdk": MagicMock(), "simmer_sdk.skill": MagicMock()}):
    import importlib
    import types

    # Provide load_config / update_config / get_config_path stubs
    skill_module = types.ModuleType("simmer_sdk.skill")
    skill_module.load_config = lambda schema, file, slug=None: _mock_cfg.copy()
    skill_module.update_config = lambda updates, file, slug=None: None
    skill_module.get_config_path = lambda file: "/tmp/config.json"
    sys.modules["simmer_sdk"] = MagicMock()
    sys.modules["simmer_sdk.skill"] = skill_module

    import strategy as strat


class TestTimeCap(unittest.TestCase):
    """Tests for check_time_cap_exit."""

    def setUp(self):
        # Reset the module-level constant to the default for each test
        strat.EXIT_BEFORE_RESOLUTION_HOURS = 1.0
        strat.MIN_EXIT_TIME_REMAINING_SEC = 60

    def _end_dt(self, hours_from_now):
        return datetime.now(timezone.utc) + timedelta(hours=hours_from_now)

    def test_fires_when_within_threshold(self):
        """Market resolves in 0.5h, threshold is 1h → should exit."""
        end_dt = self._end_dt(0.5)
        fired, reason = strat.check_time_cap_exit(0.5, end_dt)
        self.assertTrue(fired)
        self.assertEqual(reason, "time_cap")

    def test_fires_exactly_at_threshold(self):
        """Market resolves in exactly 1.0h, threshold is 1.0h → should exit."""
        end_dt = self._end_dt(1.0)
        fired, reason = strat.check_time_cap_exit(1.0, end_dt)
        self.assertTrue(fired)
        self.assertEqual(reason, "time_cap")

    def test_no_fire_above_threshold(self):
        """Market resolves in 3h, threshold is 1h → should NOT exit."""
        end_dt = self._end_dt(3.0)
        fired, reason = strat.check_time_cap_exit(3.0, end_dt)
        self.assertFalse(fired)
        self.assertIsNone(reason)

    def test_disabled_when_zero(self):
        """Setting exit_before_resolution_hours=0 disables the trigger."""
        strat.EXIT_BEFORE_RESOLUTION_HOURS = 0
        end_dt = self._end_dt(0.1)
        fired, reason = strat.check_time_cap_exit(0.1, end_dt)
        self.assertFalse(fired)
        self.assertIsNone(reason)

    def test_no_fire_when_too_close_to_skip(self):
        """
        When seconds_remaining < MIN_EXIT_TIME_REMAINING_SEC, skip the exit
        (not worth executing a near-expired trade).
        """
        # 0.001h = 3.6 seconds < 60 seconds minimum
        end_dt = self._end_dt(0.001)
        fired, reason = strat.check_time_cap_exit(0.001, end_dt)
        self.assertFalse(fired)

    def test_custom_threshold(self):
        """Custom exit_before_resolution_hours=2.0 fires correctly."""
        strat.EXIT_BEFORE_RESOLUTION_HOURS = 2.0
        end_dt = self._end_dt(1.5)
        fired, reason = strat.check_time_cap_exit(1.5, end_dt)
        self.assertTrue(fired)
        self.assertEqual(reason, "time_cap")

        end_dt = self._end_dt(3.0)
        fired, reason = strat.check_time_cap_exit(3.0, end_dt)
        self.assertFalse(fired)


class TestTargetHit(unittest.TestCase):
    """Tests for check_target_hit_exit."""

    def setUp(self):
        strat.TARGET_HIT_CAPTURE_PCT = 0.85

    def test_yes_fires_at_threshold(self):
        """
        YES bought at 0.40. Max gain = 0.60.
        At 0.91 → captured = (0.91 - 0.40) / 0.60 = 0.85 → fires.
        """
        fired, reason, details = strat.check_target_hit_exit(
            entry_price=0.40, current_price=0.91, side="YES"
        )
        self.assertTrue(fired)
        self.assertEqual(reason, "target_hit")
        self.assertAlmostEqual(details["captured_pct"], 85.0, places=0)

    def test_yes_fires_above_threshold(self):
        """YES entry 0.40, current 0.95 → captured ~91.7% → fires."""
        fired, reason, _ = strat.check_target_hit_exit(0.40, 0.95, "YES")
        self.assertTrue(fired)
        self.assertEqual(reason, "target_hit")

    def test_yes_no_fire_below_threshold(self):
        """YES entry 0.40, current 0.70 → captured 50% < 85% → no fire."""
        fired, reason, details = strat.check_target_hit_exit(0.40, 0.70, "YES")
        self.assertFalse(fired)
        self.assertIsNone(reason)
        self.assertAlmostEqual(details["captured_pct"], 50.0, places=0)

    def test_no_side_fires(self):
        """
        NO position: bought NO when YES was at 0.60 (so NO was 0.40).
        If YES drops to 0.09, NO holder captured (0.60 - 0.09) / 0.60 = 85% → fires.
        """
        fired, reason, details = strat.check_target_hit_exit(
            entry_price=0.60, current_price=0.09, side="NO"
        )
        self.assertTrue(fired)
        self.assertEqual(reason, "target_hit")
        self.assertAlmostEqual(details["captured_pct"], 85.0, places=0)

    def test_no_side_no_fire(self):
        """NO position, YES dropped from 0.60 to 0.50 → only 16.7% captured → no fire."""
        fired, reason, _ = strat.check_target_hit_exit(0.60, 0.50, "NO")
        self.assertFalse(fired)
        self.assertIsNone(reason)

    def test_disabled_when_zero(self):
        """Setting target_hit_capture_pct=0 disables the trigger."""
        strat.TARGET_HIT_CAPTURE_PCT = 0
        fired, reason, _ = strat.check_target_hit_exit(0.40, 0.99, "YES")
        self.assertFalse(fired)
        self.assertIsNone(reason)

    def test_none_entry_price(self):
        """Missing entry price → no fire (can't evaluate)."""
        fired, reason, _ = strat.check_target_hit_exit(None, 0.90, "YES")
        self.assertFalse(fired)

    def test_none_current_price(self):
        """Live price unavailable → no fire."""
        fired, reason, _ = strat.check_target_hit_exit(0.40, None, "YES")
        self.assertFalse(fired)

    def test_yes_entry_at_99_cents(self):
        """
        Edge case: YES entry at 0.99. Max gain = 0.01.
        Even at 1.0, only 1¢ gain — captured=100% → fires.
        """
        fired, reason, _ = strat.check_target_hit_exit(0.99, 1.0, "YES")
        self.assertTrue(fired)

    def test_no_fire_if_price_moved_against(self):
        """YES entry at 0.60, current 0.40 → negative captured → no fire."""
        fired, reason, details = strat.check_target_hit_exit(0.60, 0.40, "YES")
        self.assertFalse(fired)
        # captured_pct should be negative
        self.assertLess(details["captured_pct"], 0)

    def test_custom_threshold_75pct(self):
        """Custom threshold 75%: YES entry 0.40, current 0.85 → captured 75% → fires."""
        strat.TARGET_HIT_CAPTURE_PCT = 0.75
        # captured = (0.85 - 0.40) / 0.60 = 75%
        fired, reason, _ = strat.check_target_hit_exit(0.40, 0.85, "YES")
        self.assertTrue(fired)


class TestVolumeSpike(unittest.TestCase):
    """Tests for check_volume_spike_exit (with mocked CLOB calls)."""

    def setUp(self):
        strat.VOLUME_SPIKE_EXIT_MULTIPLIER = 3.0
        strat.VOLUME_BASELINE_WINDOWS = 6

    def test_fires_on_3x_spike(self):
        """Current 9000, baseline avg 1000 → 9x → fires (≥3x threshold)."""
        history = [1000.0, 1200.0, 800.0, 1100.0, 900.0, 1000.0]
        with patch.object(strat, "fetch_10m_volume", return_value=9000.0), \
             patch.object(strat, "fetch_volume_history_windows", return_value=history):
            fired, reason, details = strat.check_volume_spike_exit("fake_token_id")
        self.assertTrue(fired)
        self.assertEqual(reason, "volume_spike")
        self.assertAlmostEqual(details["ratio"], 9.0, places=0)

    def test_fires_exactly_at_threshold(self):
        """Current 3000, baseline avg 1000 → exactly 3x → fires."""
        history = [1000.0, 1200.0, 800.0, 1100.0, 900.0, 1000.0]
        with patch.object(strat, "fetch_10m_volume", return_value=3000.0), \
             patch.object(strat, "fetch_volume_history_windows", return_value=history):
            fired, reason, _ = strat.check_volume_spike_exit("fake_token_id")
        self.assertTrue(fired)
        self.assertEqual(reason, "volume_spike")

    def test_no_fire_below_threshold(self):
        """Current 2500, baseline avg 1000 → 2.5x < 3x → no fire."""
        history = [1000.0, 1200.0, 800.0, 1100.0, 900.0, 1000.0]
        with patch.object(strat, "fetch_10m_volume", return_value=2500.0), \
             patch.object(strat, "fetch_volume_history_windows", return_value=history):
            fired, reason, _ = strat.check_volume_spike_exit("fake_token_id")
        self.assertFalse(fired)
        self.assertIsNone(reason)

    def test_no_fire_on_volume_fetch_error(self):
        """Cannot fetch current volume → no fire (conservative)."""
        with patch.object(strat, "fetch_10m_volume", return_value=None):
            fired, reason, _ = strat.check_volume_spike_exit("fake_token_id")
        self.assertFalse(fired)

    def test_no_fire_on_empty_history(self):
        """No volume history → no fire (can't compute baseline)."""
        with patch.object(strat, "fetch_10m_volume", return_value=5000.0), \
             patch.object(strat, "fetch_volume_history_windows", return_value=[]):
            fired, reason, _ = strat.check_volume_spike_exit("fake_token_id")
        self.assertFalse(fired)

    def test_no_fire_on_zero_baseline(self):
        """Zero baseline (inactive market) → no fire (conservative)."""
        with patch.object(strat, "fetch_10m_volume", return_value=5000.0), \
             patch.object(strat, "fetch_volume_history_windows", return_value=[0.0, 0.0, 0.0]):
            fired, reason, _ = strat.check_volume_spike_exit("fake_token_id")
        self.assertFalse(fired)

    def test_disabled_when_zero_multiplier(self):
        """Setting volume_spike_exit_multiplier=0 disables the trigger."""
        strat.VOLUME_SPIKE_EXIT_MULTIPLIER = 0
        with patch.object(strat, "fetch_10m_volume", return_value=9000.0), \
             patch.object(strat, "fetch_volume_history_windows", return_value=[1000.0, 1000.0]):
            fired, reason, _ = strat.check_volume_spike_exit("fake_token_id")
        self.assertFalse(fired)
        self.assertIsNone(reason)


class TestEvaluateExitTriggers(unittest.TestCase):
    """
    Integration tests for evaluate_exit_triggers — checks priority ordering
    and combined trigger behavior with mocked sub-functions.
    """

    def setUp(self):
        strat.EXIT_BEFORE_RESOLUTION_HOURS = 1.0
        strat.TARGET_HIT_CAPTURE_PCT = 0.85
        strat.VOLUME_SPIKE_EXIT_MULTIPLIER = 3.0
        strat.MIN_EXIT_TIME_REMAINING_SEC = 60

    def _end_dt(self, hours_from_now):
        return datetime.now(timezone.utc) + timedelta(hours=hours_from_now)

    def test_time_cap_takes_priority(self):
        """
        When time_cap fires (30 min < 1h threshold), it short-circuits before
        fetching live price or evaluating volume spike.
        """
        pos = {"entry_price": 0.40, "side": "YES"}
        end_dt = self._end_dt(0.5)  # 30 min left < 1h threshold

        with patch.object(strat, "fetch_live_midpoint", return_value=0.75), \
             patch.object(strat, "check_volume_spike_exit", return_value=(False, None, {})) as mock_vol:
            fired, reason, _ = strat.evaluate_exit_triggers(pos, end_dt, "token_id")
        self.assertTrue(fired)
        self.assertEqual(reason, "time_cap")
        # time_cap short-circuits before volume spike is evaluated
        mock_vol.assert_not_called()

    def test_target_hit_before_volume_spike(self):
        """
        When target_hit fires (current 0.91, entry 0.40 → 85% captured),
        volume_spike is not evaluated (target_hit is priority 2).
        """
        pos = {"entry_price": 0.40, "side": "YES"}
        end_dt = self._end_dt(5.0)  # 5h left, no time_cap

        with patch.object(strat, "fetch_live_midpoint", return_value=0.91), \
             patch.object(strat, "check_volume_spike_exit", return_value=(True, "volume_spike", {"ratio": 4.0})) as mock_vol:
            fired, reason, details = strat.evaluate_exit_triggers(pos, end_dt, "token_id")
        self.assertTrue(fired)
        self.assertEqual(reason, "target_hit")
        # target_hit fired first — volume spike should not have been called
        mock_vol.assert_not_called()

    def test_volume_spike_fires_when_target_not_hit(self):
        """
        Volume spike fires when time_cap and target_hit don't trigger.
        """
        pos = {"entry_price": 0.40, "side": "YES"}
        end_dt = self._end_dt(5.0)

        with patch.object(strat, "fetch_live_midpoint", return_value=0.55), \
             patch.object(strat, "check_volume_spike_exit", return_value=(True, "volume_spike", {"ratio": 4.0})):
            fired, reason, details = strat.evaluate_exit_triggers(pos, end_dt, "token_id")
        self.assertTrue(fired)
        self.assertEqual(reason, "volume_spike")

    def test_no_trigger_fires_in_healthy_hold(self):
        """
        Normal hold: 5h left, 50¢ price, no volume spike → nothing fires.
        """
        pos = {"entry_price": 0.40, "side": "YES"}
        end_dt = self._end_dt(5.0)

        with patch.object(strat, "fetch_live_midpoint", return_value=0.50), \
             patch.object(strat, "check_volume_spike_exit", return_value=(False, None, {})):
            fired, reason, details = strat.evaluate_exit_triggers(pos, end_dt, "token_id")
        self.assertFalse(fired)
        self.assertIsNone(reason)
        self.assertIn("hours_to_resolution", details)


class TestExitReasonCompleteness(unittest.TestCase):
    """Verify all four exit reasons are defined and handled in evaluation."""

    VALID_EXIT_REASONS = {"time_cap", "target_hit", "volume_spike", "manual"}

    def test_time_cap_reason(self):
        strat.EXIT_BEFORE_RESOLUTION_HOURS = 1.0
        end_dt = datetime.now(timezone.utc) + timedelta(hours=0.5)
        fired, reason = strat.check_time_cap_exit(0.5, end_dt)
        self.assertIn(reason, self.VALID_EXIT_REASONS)

    def test_target_hit_reason(self):
        strat.TARGET_HIT_CAPTURE_PCT = 0.85
        fired, reason, _ = strat.check_target_hit_exit(0.40, 0.91, "YES")
        self.assertIn(reason, self.VALID_EXIT_REASONS)

    def test_volume_spike_reason(self):
        strat.VOLUME_SPIKE_EXIT_MULTIPLIER = 3.0
        with patch.object(strat, "fetch_10m_volume", return_value=9000.0), \
             patch.object(strat, "fetch_volume_history_windows", return_value=[1000.0] * 6):
            fired, reason, _ = strat.check_volume_spike_exit("token")
        self.assertIn(reason, self.VALID_EXIT_REASONS)


class TestEntryEdgeSemantics(unittest.TestCase):
    """
    Tests for entry edge calculation (option-b semantics):
    Only enter when the market DISAGREES with momentum direction.
    Edge is always measured toward the momentum-opposing side.
    Mirrors the edge logic in strategy.py run_entry_scan.
    """

    def _edge_up(self, live_price):
        """Edge for 'up' momentum direction: positive only when YES < 0.50."""
        return 0.50 - live_price

    def _edge_down(self, live_price):
        """Edge for 'down' momentum direction: positive only when YES > 0.50."""
        return live_price - 0.50

    def test_up_momentum_yes_cheap_has_positive_edge(self):
        """
        BTC momentum up, YES at 0.35 (market bets down) → edge = 0.15 → would enter YES.
        """
        edge = self._edge_up(0.35)
        self.assertAlmostEqual(edge, 0.15)
        self.assertGreater(edge, 0)

    def test_up_momentum_yes_expensive_has_negative_edge(self):
        """
        BTC momentum up, YES at 0.70 (market agrees) → edge = -0.20 → skip.
        Market already prices in the bullish momentum — no value edge.
        """
        edge = self._edge_up(0.70)
        self.assertAlmostEqual(edge, -0.20)
        self.assertLess(edge, 0)

    def test_up_momentum_yes_at_50_no_edge(self):
        """BTC momentum up, YES at 0.50 → edge = 0 → skip (below any positive threshold)."""
        edge = self._edge_up(0.50)
        self.assertAlmostEqual(edge, 0.0)

    def test_down_momentum_no_cheap_has_positive_edge(self):
        """
        BTC momentum down, YES at 0.65 (market bets up) → NO is cheap → edge = 0.15 → would enter NO.
        """
        edge = self._edge_down(0.65)
        self.assertAlmostEqual(edge, 0.15)
        self.assertGreater(edge, 0)

    def test_down_momentum_no_expensive_has_negative_edge(self):
        """
        BTC momentum down, YES at 0.30 (market already bets down) → edge = -0.20 → skip.
        """
        edge = self._edge_down(0.30)
        self.assertAlmostEqual(edge, -0.20)
        self.assertLess(edge, 0)

    def test_entry_threshold_gates_weak_edge(self):
        """
        Edge 0.03 < default threshold 0.05 → should not enter.
        """
        threshold = 0.05
        self.assertLess(self._edge_up(0.47), threshold)    # YES at 0.47, up momentum
        self.assertLess(self._edge_down(0.53), threshold)  # YES at 0.53, down momentum

    def test_entry_threshold_allows_strong_edge(self):
        """
        Edge 0.10 > threshold 0.05 → should enter.
        """
        threshold = 0.05
        self.assertGreater(self._edge_up(0.40), threshold)    # YES at 0.40, up momentum
        self.assertGreater(self._edge_down(0.60), threshold)  # YES at 0.60, down momentum


if __name__ == "__main__":
    unittest.main(verbosity=2)
