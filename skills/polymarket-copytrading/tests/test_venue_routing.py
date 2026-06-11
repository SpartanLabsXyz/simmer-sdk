"""Tests for --venue flag end-to-end routing in polymarket-copytrading.

Mirrors the TestVenueRouting shape from polymarket-worldcup-copytrader (#187).

Covers:
- sim-config + --venue polymarket → client.trade() gets venue=polymarket
- polymarket-config + --venue sim → client.trade() gets venue=sim
- --venue polymarket → ensure_can_trade() gets venue=polymarket
- --venue sim → ensure_can_trade() NOT called (sim needs no balance preflight)
- client singleton re-pins when --venue overrides config/env venue
"""

import importlib
import importlib.util
import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Module loading helpers
# ---------------------------------------------------------------------------

def _make_skill_module(config_venue="sim", trade_success=True, plan_trades=None,
                       env_venue=None):
    """Return a fresh copytrading_trader module with mocked SimmerClient.

    config_venue — the 'venue' value in the fake config.json (default 'sim').
    env_venue    — optional TRADING_VENUE env var override.
    """
    skill_stub = types.ModuleType("simmer_sdk.skill")

    class FakeConfigPath:
        def exists(self): return False
        def __str__(self): return "/tmp/fake-config.json"

    base_config = {
        "wallets": "0xwhale1,0xwhale2",
        "top_n": "",
        "max_usd": 30.0,
        "max_trades_per_run": 10,
        "venue": config_venue,
        "order_type": "GTC",
        "cadence_mode": "polling",
    }

    skill_stub.load_config = lambda schema, file, slug=None: dict(base_config)
    skill_stub.get_config_path = lambda file: FakeConfigPath()
    skill_stub.update_config = lambda updates, file: {}

    sdk_stub = types.ModuleType("simmer_sdk")
    sdk_stub.skill = skill_stub

    mock_trade_result = MagicMock()
    mock_trade_result.success = trade_success
    mock_trade_result.trade_id = "test-trade-id"
    mock_trade_result.error = None if trade_success else "test error"
    mock_trade_result.shares_bought = 10.0
    mock_trade_result.retryable = False

    mock_client = MagicMock()
    mock_client.venue = config_venue
    mock_client.auto_redeem.return_value = []
    mock_client.ensure_can_trade.return_value = {
        "ok": True, "max_safe_size": 30.0, "balance": 100.0, "collateral": "USDC",
    }
    mock_client.trade.return_value = mock_trade_result

    if plan_trades is None:
        plan_trades = [
            {
                "market_id": "mkt-abc",
                "action": "buy",
                "side": "yes",
                "shares": 5.0,
                "estimated_price": 0.60,
                "estimated_cost": 3.0,
                "market_title": "Test market",
            }
        ]

    plan_response = {
        "success": True,
        "wallets_analyzed": 2,
        "positions_found": 1,
        "conflicts_skipped": 0,
        "trades": plan_trades,
    }

    def _request(method, path, **kwargs):
        if "copytrading/execute" in path:
            return plan_response
        return {}

    mock_client._request.side_effect = _request

    class MockSimmerClient:
        def __init__(self, api_key, venue):
            pass
        def __new__(cls, *args, **kwargs):
            return mock_client

    sdk_stub.SimmerClient = MockSimmerClient

    sys.modules["simmer_sdk"] = sdk_stub
    sys.modules["simmer_sdk.skill"] = skill_stub

    mod_name = "copytrading_trader"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    spec_path = os.path.join(os.path.dirname(__file__), "..", "copytrading_trader.py")
    spec = importlib.util.spec_from_file_location(mod_name, spec_path)
    mod = importlib.util.module_from_spec(spec)

    env = {"SIMMER_API_KEY": "sk_test"}
    if env_venue:
        env["TRADING_VENUE"] = env_venue

    with patch.dict(os.environ, env, clear=False):
        spec.loader.exec_module(mod)

    mod._client = mock_client
    return mod, mock_client


def _run_copytrading(mod, mock_client, live=True, venue=None, env_overrides=None):
    """Invoke mod.run_copytrading() with standard test wallets."""
    env = {"SIMMER_API_KEY": "sk_test"}
    if env_overrides:
        env.update(env_overrides)
    with patch.dict(os.environ, env, clear=False):
        mod.run_copytrading(
            wallets=["0xwhale1", "0xwhale2"],
            top_n=None,
            max_usd=30.0,
            dry_run=not live,
            buy_only=True,
            detect_whale_exits=True,
            venue=venue,
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestVenueRouting(unittest.TestCase):
    """--venue must be authoritative end-to-end for trade execution and preflight."""

    def test_sim_config_venue_polymarket_arg_trade_gets_polymarket(self):
        """Config venue=sim + run_copytrading(venue='polymarket') → trade kwargs venue=polymarket."""
        mod, mock_client = _make_skill_module(config_venue="sim")
        _run_copytrading(mod, mock_client, live=True, venue="polymarket")
        mock_client.trade.assert_called_once()
        kwargs = mock_client.trade.call_args[1]
        self.assertEqual(kwargs.get("venue"), "polymarket",
                         f"Expected venue='polymarket' in trade kwargs, got: {kwargs}")

    def test_polymarket_config_venue_sim_arg_trade_gets_sim(self):
        """Config venue=polymarket + run_copytrading(venue='sim') → trade kwargs venue=sim."""
        mod, mock_client = _make_skill_module(config_venue="polymarket")
        _run_copytrading(mod, mock_client, live=True, venue="sim")
        mock_client.trade.assert_called_once()
        kwargs = mock_client.trade.call_args[1]
        self.assertEqual(kwargs.get("venue"), "sim",
                         f"Expected venue='sim' in trade kwargs, got: {kwargs}")

    def test_polymarket_venue_preflight_receives_venue_kwarg(self):
        """Live polymarket run → ensure_can_trade() called with venue='polymarket'."""
        mod, mock_client = _make_skill_module(config_venue="sim")
        _run_copytrading(mod, mock_client, live=True, venue="polymarket")
        mock_client.ensure_can_trade.assert_called_once()
        kwargs = mock_client.ensure_can_trade.call_args[1]
        self.assertEqual(kwargs.get("venue"), "polymarket",
                         f"Expected venue='polymarket' in ensure_can_trade kwargs, got: {kwargs}")

    def test_sim_venue_no_polymarket_preflight(self):
        """Live sim run → ensure_can_trade() NOT called (sim has no balance preflight)."""
        mod, mock_client = _make_skill_module(config_venue="sim")
        _run_copytrading(mod, mock_client, live=True, venue="sim")
        mock_client.ensure_can_trade.assert_not_called()

    def test_client_repins_when_venue_overrides_config(self):
        """If client was initialized with sim, passing venue=polymarket must re-pin client.venue."""
        mod, mock_client = _make_skill_module(config_venue="sim")
        mock_client.venue = "sim"
        _run_copytrading(mod, mock_client, live=True, venue="polymarket")
        # After the call, the client's venue attribute should reflect the override
        self.assertEqual(mock_client.venue, "polymarket",
                         "Client singleton must be re-pinned to venue=polymarket")

    def test_dry_run_does_not_call_trade(self):
        """Dry run must never call client.trade() regardless of venue."""
        mod, mock_client = _make_skill_module(config_venue="sim")
        _run_copytrading(mod, mock_client, live=False, venue="polymarket")
        mock_client.trade.assert_not_called()

    def test_env_venue_polymarket_without_cli_flag_used_in_trade(self):
        """TRADING_VENUE=polymarket env without --venue CLI flag → trade gets venue=polymarket."""
        mod, mock_client = _make_skill_module(config_venue="", env_venue="polymarket")
        mock_client.venue = "polymarket"
        # No venue= arg (no CLI flag); env provides TRADING_VENUE=polymarket
        _run_copytrading(mod, mock_client, live=True, venue=None,
                         env_overrides={"TRADING_VENUE": "polymarket"})
        mock_client.trade.assert_called_once()
        kwargs = mock_client.trade.call_args[1]
        self.assertEqual(kwargs.get("venue"), "polymarket")

    def test_constructor_venue_matches_cli_venue_not_env(self):
        """Regression: when TRADING_VENUE=polymarket but --venue sim, the SimmerClient
        constructor must receive venue='sim', not 'polymarket'. Protects against
        Polymarket-specific __init__ side effects firing before the venue re-pin."""
        mod, _ = _make_skill_module(config_venue="", env_venue="polymarket")
        mod._client = None  # force re-construction on the next get_client() call

        construction_venues = []

        class TrackingClient:
            def __new__(cls, api_key, venue):
                construction_venues.append(venue)
                return MagicMock()

        sys.modules["simmer_sdk"].SimmerClient = TrackingClient
        try:
            with patch.dict(os.environ, {"SIMMER_API_KEY": "sk_test", "TRADING_VENUE": "polymarket"},
                            clear=False):
                # Mirrors the fixed main() path: resolve CLI venue BEFORE get_client()
                mod.get_client(mod._resolve_venue("sim"))
        finally:
            mod._client = None  # clean up singleton so other tests are unaffected

        self.assertEqual(construction_venues, ["sim"],
                         f"Constructor received {construction_venues!r}; expected ['sim']. "
                         "TRADING_VENUE=polymarket must not override --venue sim at construction time.")


if __name__ == "__main__":
    unittest.main()
