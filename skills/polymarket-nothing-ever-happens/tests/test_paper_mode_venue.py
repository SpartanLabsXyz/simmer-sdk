"""
Regression tests for the paper-mode live-fire bug (SIM-3058 audit).

Bug: the client was constructed with hardcoded venue="polymarket" while
TRADING_VENUE=="sim" (the documented $SIM paper switch, SKILL.md) skipped the
balance preflight AND forced dry_run off — so "paper mode" live-fired real
Polymarket trades with the preflight disabled.

Verifies:
  - TRADING_VENUE=sim  -> SimmerClient constructed with venue="sim"
  - TRADING_VENUE unset/polymarket -> venue="polymarket"
  - resolve_effective_dry_run(): the sim branch may only disable dry_run when
    the client's effective venue really is "sim"; if the client is on a real
    venue, it aborts loudly (SystemExit) instead of live-firing.
  - TRADING_VENUE unset/polymarket -> dry_run is unaffected by the sim branch.
  - Balance preflight runs for live real-venue runs, is skipped for sim
    (sim has no USDC preflight) and for dry runs.

All tests are pure-unit: no network calls, no SIMMER_API_KEY required.
"""
import io
import os
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

_mock_cfg = {
    "price_cap": 0.10,
    "max_bet_usd": 5.0,
    "max_trades_per_run": 3,
    "daily_budget": 15.0,
    "min_liquidity": 500.0,
    "min_volume_24h": 100.0,
    "candidate_pages": 20,
}

# Stub simmer_sdk before importing the skill module. Installed persistently in
# sys.modules (not a context manager) because get_client() does a lazy
# `from simmer_sdk import SimmerClient` at call time, inside each test.
_sdk_stub = types.ModuleType("simmer_sdk")
_sdk_stub.SimmerClient = MagicMock(name="SimmerClient")
_skill_stub = types.ModuleType("simmer_sdk.skill")
_skill_stub.load_config = lambda schema, file, slug=None: _mock_cfg.copy()
_skill_stub.update_config = lambda updates, file, slug=None: None
_skill_stub.get_config_path = lambda file: "/tmp/config.json"
sys.modules["simmer_sdk"] = _sdk_stub
sys.modules["simmer_sdk.skill"] = _skill_stub

import nothing_ever_happens as neh  # noqa: E402


class VenueThreadingTests(unittest.TestCase):
    """TRADING_VENUE must reach the SimmerClient constructor."""

    def setUp(self):
        neh._client = None
        _sdk_stub.SimmerClient.reset_mock()

    def tearDown(self):
        neh._client = None

    def test_sim_venue_constructs_client_with_sim(self):
        env = {"SIMMER_API_KEY": "test-key", "TRADING_VENUE": "sim"}
        with patch.dict(os.environ, env, clear=False):
            neh.get_client(live=True)
        _, kwargs = _sdk_stub.SimmerClient.call_args
        self.assertEqual(kwargs.get("venue"), "sim")

    def test_unset_venue_defaults_to_polymarket(self):
        env = {"SIMMER_API_KEY": "test-key"}
        with patch.dict(os.environ, env, clear=False):
            os.environ.pop("TRADING_VENUE", None)
            neh.get_client(live=True)
        _, kwargs = _sdk_stub.SimmerClient.call_args
        self.assertEqual(kwargs.get("venue"), "polymarket")

    def test_explicit_polymarket_venue(self):
        env = {"SIMMER_API_KEY": "test-key", "TRADING_VENUE": "polymarket"}
        with patch.dict(os.environ, env, clear=False):
            neh.get_client(live=True)
        _, kwargs = _sdk_stub.SimmerClient.call_args
        self.assertEqual(kwargs.get("venue"), "polymarket")


class EffectiveDryRunTests(unittest.TestCase):
    """The sim paper branch must never disable dry_run on a real venue."""

    def test_sim_branch_disables_dry_run_when_client_is_sim(self):
        with patch.dict(os.environ, {"TRADING_VENUE": "sim"}, clear=False):
            self.assertFalse(
                neh.resolve_effective_dry_run(dry_run=True, client_venue="sim")
            )

    def test_sim_branch_aborts_when_client_is_polymarket(self):
        # Belt-and-braces: TRADING_VENUE=sim would disable dry_run, but the
        # client's effective venue is a real venue -> abort loudly.
        with patch.dict(os.environ, {"TRADING_VENUE": "sim"}, clear=False):
            buf = io.StringIO()
            with self.assertRaises(SystemExit), redirect_stdout(buf):
                neh.resolve_effective_dry_run(dry_run=True, client_venue="polymarket")
            self.assertIn("refus", buf.getvalue().lower())

    def test_sim_branch_aborts_when_client_venue_unknown(self):
        with patch.dict(os.environ, {"TRADING_VENUE": "sim"}, clear=False):
            with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()):
                neh.resolve_effective_dry_run(dry_run=True, client_venue=None)

    def test_unset_venue_leaves_dry_run_untouched(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TRADING_VENUE", None)
            self.assertTrue(
                neh.resolve_effective_dry_run(dry_run=True, client_venue="polymarket")
            )

    def test_polymarket_venue_leaves_dry_run_untouched(self):
        with patch.dict(os.environ, {"TRADING_VENUE": "polymarket"}, clear=False):
            self.assertTrue(
                neh.resolve_effective_dry_run(dry_run=True, client_venue="polymarket")
            )

    def test_explicit_live_stays_live_on_polymarket_without_abort(self):
        # --live on a real venue is the user's explicit choice; no sim branch
        # involvement, no abort.
        with patch.dict(os.environ, {"TRADING_VENUE": "polymarket"}, clear=False):
            self.assertFalse(
                neh.resolve_effective_dry_run(dry_run=False, client_venue="polymarket")
            )


class PreflightGateTests(unittest.TestCase):
    """Balance preflight runs for live real-venue runs only."""

    def test_preflight_runs_live_polymarket(self):
        with patch.dict(os.environ, {"TRADING_VENUE": "polymarket"}, clear=False):
            self.assertTrue(neh.should_run_balance_preflight(dry_run=False))

    def test_preflight_runs_live_unset_venue(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TRADING_VENUE", None)
            self.assertTrue(neh.should_run_balance_preflight(dry_run=False))

    def test_preflight_skipped_for_sim(self):
        # sim has no USDC preflight — skipping is correct once venue is real.
        with patch.dict(os.environ, {"TRADING_VENUE": "sim"}, clear=False):
            self.assertFalse(neh.should_run_balance_preflight(dry_run=False))

    def test_preflight_skipped_for_dry_run(self):
        with patch.dict(os.environ, {"TRADING_VENUE": "polymarket"}, clear=False):
            self.assertFalse(neh.should_run_balance_preflight(dry_run=True))


class PositionsVenueTests(unittest.TestCase):
    """Position dedup must look at the effective venue, not hardcoded polymarket."""

    def tearDown(self):
        neh._client = None

    def test_get_positions_uses_effective_venue(self):
        fake_client = MagicMock()
        fake_client.get_positions.return_value = []
        neh._client = fake_client
        with patch.dict(os.environ, {"TRADING_VENUE": "sim"}, clear=False):
            neh.get_positions()
        _, kwargs = fake_client.get_positions.call_args
        self.assertEqual(kwargs.get("venue"), "sim")


class TestSimEnvExplicitLiveGuard(unittest.TestCase):
    """TRADING_VENUE=sim with --live (dry_run already False) must STILL
    assert the client venue is sim before any live path (codex P1)."""

    def test_sim_env_live_with_real_client_venue_aborts(self):
        with patch.dict(os.environ, {"TRADING_VENUE": "sim"}):
            with self.assertRaises(SystemExit):
                neh.resolve_effective_dry_run(False, "polymarket")

    def test_sim_env_live_with_sim_client_venue_proceeds(self):
        with patch.dict(os.environ, {"TRADING_VENUE": "sim"}):
            self.assertFalse(neh.resolve_effective_dry_run(False, "sim"))

    def test_real_env_live_unaffected(self):
        with patch.dict(os.environ, {"TRADING_VENUE": "polymarket"}):
            self.assertFalse(neh.resolve_effective_dry_run(False, "polymarket"))


if __name__ == "__main__":
    unittest.main()


class TestAutoRedeemGating(unittest.TestCase):
    """auto_redeem submits REAL Polymarket transactions regardless of client
    venue — only live polymarket runs (not sim/paper, dry-run, or --scan)
    may reach it (codex pass-2 P1)."""

    def _run_main(self, argv, env):
        with patch.dict(os.environ, env):
            with patch.object(sys, "argv", ["nothing_ever_happens.py"] + argv):
                neh._client = None
                with patch.object(neh, "fetch_candidate_markets", return_value=[]):
                    with patch.object(_sdk_stub, "SimmerClient") as MockClient:
                        mock = MockClient.return_value
                        mock.venue = env.get("TRADING_VENUE", "polymarket")
                        mock.ensure_can_trade.return_value = {
                            "ok": True, "max_safe_size": 999.0, "balance": 100.0}
                        mock.auto_redeem.return_value = []
                        try:
                            neh.main()
                        except SystemExit:
                            pass
                        return mock

    def test_sim_env_never_calls_auto_redeem(self):
        mock = self._run_main(["--live", "--quiet"], {"TRADING_VENUE": "sim", "SIMMER_API_KEY": "sk"})
        mock.auto_redeem.assert_not_called()

    def test_dry_run_never_calls_auto_redeem(self):
        mock = self._run_main(["--quiet"], {"TRADING_VENUE": "polymarket", "SIMMER_API_KEY": "sk"})
        mock.auto_redeem.assert_not_called()

    def test_scan_never_calls_auto_redeem(self):
        mock = self._run_main(["--scan", "--live", "--quiet"], {"TRADING_VENUE": "polymarket", "SIMMER_API_KEY": "sk"})
        mock.auto_redeem.assert_not_called()

    def test_live_polymarket_calls_auto_redeem(self):
        mock = self._run_main(["--live", "--quiet"], {"TRADING_VENUE": "polymarket", "SIMMER_API_KEY": "sk"})
        mock.auto_redeem.assert_called_once()
