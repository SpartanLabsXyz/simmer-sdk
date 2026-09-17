"""SIM-5499: per-market position cap.

Replay filled every DCA attempt into an underpriced bucket (mean 34
buys/market, max 137, 94.8% max DD on the 2026-09-17 gate run) while live
lands 1-2 because balance/backoffs throttle it — replay and live were
measuring different strategies. `MAX_BUYS_PER_MARKET` checks held positions
(`get_positions()`) before entry so both agree.

Also pins the skip-reason breakdown fix: `skip_reasons` was collected but
never surfaced, so a location that silently entered zero markets (the
London 0/36 half of this ticket) could only be diagnosed by re-reading
per-tick logs.

Pure-unit: no network, no SIMMER_API_KEY.
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

_mock_cfg = {
    "entry_threshold": 0.15,
    "min_entry_price": 0.0,
    "min_hours_to_resolve": 2,
    "exit_threshold": 0.45,
    "max_position_usd": 2.00,
    "sizing_pct": 0.05,
    "max_trades_per_run": 5,
    "max_buys_per_market": 1,
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

REPLAY_NOW = "2026-04-30T12:00:00+00:00"


def _replay_market(**overrides):
    row = {
        "id": "wx-nyc-72",
        "question": "Highest temperature in New York City on April 30",
        "event_name": "Highest temperature in New York City on April 30",
        "outcome_name": "72-73°F",
        "yes_price": 0.10,
        "current_probability": 0.10,
    }
    row.update(overrides)
    return row


def _trade_ok():
    r = MagicMock()
    r.success = True
    r.trade_id = "replay-1"
    r.shares_bought = 20.0
    r.error = None
    r.simulated = True
    r.order_status = "filled"
    return r


class _BaseRunHarness(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("SIMMER_REPLAY", None)
        os.environ.pop("SIMMER_REPLAY_NOW", None)
        wt.reset_replay_forecasts()
        wt._client = None

    def _run(self, markets, positions, execute=None, quiet=True):
        client = MagicMock()
        client.auto_redeem.return_value = []
        client.live = True
        execute = execute or MagicMock(return_value={
            "success": True, "trade_id": "replay-1", "shares_bought": 20,
            "simulated": True,
        })
        with patch.object(wt, "get_client", MagicMock(return_value=client)), \
             patch.object(wt, "discover_and_import_weather_markets", MagicMock(return_value=0)), \
             patch.object(wt, "fetch_weather_markets", MagicMock(return_value=markets)), \
             patch.object(wt, "get_market_context", MagicMock(return_value=None)), \
             patch.object(wt, "get_price_history", MagicMock(return_value=[])), \
             patch.object(wt, "get_positions", MagicMock(return_value=positions)), \
             patch.object(wt, "execute_trade", execute):
            wt.run_weather_strategy(dry_run=True, quiet=quiet)
        return execute


class TestPerMarketCap(_BaseRunHarness):
    def setUp(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        wt._REPLAY_FORECASTS["KLGA"] = {"2026-04-30": {"high": 72, "low": 50}}
        wt.MAX_BUYS_PER_MARKET = 1

    def tearDown(self):
        super().tearDown()
        wt.MAX_BUYS_PER_MARKET = _mock_cfg["max_buys_per_market"]

    def test_no_existing_position_enters(self):
        execute = self._run([_replay_market()], positions=[])
        execute.assert_called_once()

    def test_existing_position_blocks_reentry_default_cap(self):
        """Default cap=1: any held position on the market blocks a re-buy."""
        held = {"market_id": "wx-nyc-72", "shares_yes": 20.0, "cost_basis": 2.0}
        execute = self._run([_replay_market()], positions=[held])
        execute.assert_not_called()

    def test_different_market_id_not_blocked(self):
        """The cap is per-market — a position in a different market must not
        block this one (would be a bug: capping the whole location/run)."""
        held = {"market_id": "some-other-market", "shares_yes": 20.0, "cost_basis": 2.0}
        execute = self._run([_replay_market()], positions=[held])
        execute.assert_called_once()

    def test_cap_disabled_allows_dca(self):
        """max_buys_per_market=0 keeps the pre-SIM-5499 unbounded-DCA behavior."""
        wt.MAX_BUYS_PER_MARKET = 0
        held = {"market_id": "wx-nyc-72", "shares_yes": 20.0, "cost_basis": 2.0}
        execute = self._run([_replay_market()], positions=[held])
        execute.assert_called_once()

    def test_cap_raised_permits_configured_dca_depth(self):
        """max_buys_per_market=3 with 1 buy's worth of cost_basis still enters."""
        wt.MAX_BUYS_PER_MARKET = 3
        held = {"market_id": "wx-nyc-72", "shares_yes": 20.0, "cost_basis": 2.0}
        execute = self._run([_replay_market()], positions=[held])
        execute.assert_called_once()

    def test_cap_raised_blocks_once_depth_reached(self):
        """max_buys_per_market=3 with cost_basis for 3 buys already blocks the 4th."""
        wt.MAX_BUYS_PER_MARKET = 3
        held = {"market_id": "wx-nyc-72", "shares_yes": 60.0, "cost_basis": 6.0}
        execute = self._run([_replay_market()], positions=[held])
        execute.assert_not_called()


class TestSkipReasonBreakdown(_BaseRunHarness):
    """SIM-5499: skip_reasons was collected but never printed."""

    def setUp(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        # No forecast injected for KLGA — every event hits "no forecast".

    def test_skip_breakdown_prints_per_location_reason(self):
        import io
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            self._run([_replay_market()], positions=[], quiet=False)
        text = buf.getvalue()
        self.assertIn("Skip reasons by location", text)
        self.assertIn("NYC", text)
        self.assertIn("no forecast", text)

    def test_skip_breakdown_survives_quiet_mode(self):
        """force=True must push this past --quiet, like the coverage guard."""
        import io
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            self._run([_replay_market()], positions=[], quiet=True)
        text = buf.getvalue()
        self.assertIn("Skip reasons by location", text)

    def test_unparseable_event_name_tracked_separately(self):
        """Events that never yield a location (no alias / no date match)
        still show up somewhere — not silently dropped."""
        import io
        buf = io.StringIO()
        bad = _replay_market(
            id="wx-bad-1",
            question="a market with no location or date",
            event_name="a market with no location or date",
        )
        with patch("sys.stdout", buf):
            self._run([bad], positions=[], quiet=False)
        text = buf.getvalue()
        self.assertIn("(unparsed event name)", text)


class TestBuysSoFar(unittest.TestCase):
    def setUp(self):
        wt.MAX_POSITION_USD = 2.0

    def test_no_cost_basis_any_shares_counts_as_one(self):
        self.assertEqual(wt._buys_so_far({"shares_yes": 5.0}), 1)

    def test_no_cost_basis_no_shares_counts_as_zero(self):
        self.assertEqual(wt._buys_so_far({"shares_yes": 0}), 0)

    def test_cost_basis_divides_by_configured_max_position(self):
        self.assertEqual(wt._buys_so_far({"cost_basis": 6.0}), 3)
        self.assertEqual(wt._buys_so_far({"cost_basis": 2.0}), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
