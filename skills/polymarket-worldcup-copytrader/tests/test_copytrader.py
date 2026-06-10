"""Tests for polymarket-worldcup-copytrader.

Covers:
- fetch_leaders parses a valid copy-leaders response
- fetch_leaders returns [] on the real HTTP 503 (cache not yet computed);
  unrelated errors re-raise
- run() exits cleanly when fetch_leaders returns []
- run() emits automaton JSON on exit when AUTOMATON_MANAGED is set
- run() calls copytrading/execute with wallets from the leader set
- automaton block emitted when dry_run + positions found
- --venue is authoritative end-to-end (trade + preflight venue kwargs)
- live polymarket preflight failure aborts fail-closed
- --dry-run overrides --live
- client-side MAX_TRADES truncation + per-position cap skip
- WC_COPYTRADER_MIN_LEADERS partial-leader-set gate
- malformed AUTOMATON_MAX_BET falls back safely
- clawhub.json tunables cover documented env knobs
"""

import importlib
import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Module loading helpers
# ---------------------------------------------------------------------------

def _make_skill_module(leaders_response, plan_response=None, trade_success=True,
                       config_overrides=None):
    """Return a fresh copytrader module with mocked SimmerClient."""
    # Stub simmer_sdk.skill so the import succeeds without the real package
    skill_stub = types.ModuleType("simmer_sdk.skill")

    class FakeConfigPath:
        def exists(self):
            return False
        def __str__(self):
            return "/tmp/fake-config.json"

    base_config = {
        "max_usd": 30.0,
        "max_trades": 10,
        "venue": "sim",
        "buy_only": "true",
        "detect_exits": "true",
        "min_leaders": 1,
    }
    if config_overrides:
        base_config.update(config_overrides)

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

    mock_client = MagicMock()
    mock_client.venue = "sim"
    mock_client.auto_redeem.return_value = []
    mock_client.ensure_can_trade.return_value = {"ok": True, "max_safe_size": 30.0, "balance": 100.0}
    mock_client.trade.return_value = mock_trade_result

    if plan_response is None:
        plan_response = {
            "success": True,
            "wallets_analyzed": 3,
            "positions_found": 2,
            "conflicts_skipped": 0,
            "trades": [
                {
                    "market_id": "mkt-abc",
                    "action": "buy",
                    "side": "yes",
                    "shares": 5.0,
                    "estimated_price": 0.6,
                    "estimated_cost": 3.0,
                    "market_title": "Will Brazil win the 2026 WC?",
                },
            ],
        }

    def _request(method, path, **kwargs):
        if "wc/copy-leaders" in path:
            return leaders_response
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

    # Force reimport of the skill module
    mod_name = "copytrader"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    spec_path = os.path.join(os.path.dirname(__file__), "..", "copytrader.py")
    spec = importlib.util.spec_from_file_location(mod_name, spec_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._client = mock_client
    return mod, mock_client


def _leaders_response(n=1):
    """Valid copy-leaders response with n leader wallets."""
    return {
        "refreshed_at": "2026-06-10T02:00:00+00:00",
        "leaders": [
            {"wallet": f"0xwallet{i}", "backtest_copy_pnl_usdc": 50.0,
             "slippage_cost_rate_pct": 1.0, "trade_count": 12}
            for i in range(n)
        ],
    }


def _run_capturing(mod, automaton=True, **run_kwargs):
    """Run mod.run() capturing printed lines; return the automaton JSON lines."""
    env = {"SIMMER_API_KEY": "sk_test"}
    if automaton:
        env["AUTOMATON_MANAGED"] = "1"
    with patch.dict(os.environ, env):
        mod._automaton_reported = False
        captured = []
        original_print = print

        def capturing_print(*args, **kwargs):
            line = " ".join(str(a) for a in args)
            captured.append(line)
            original_print(*args, **kwargs)

        with patch("builtins.print", side_effect=capturing_print):
            mod.run(**run_kwargs)

    json_lines = [l for l in captured if l.strip().startswith('{"automaton"')]
    return json_lines, captured


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestVenueRouting(unittest.TestCase):
    """--venue must be authoritative end-to-end (codex P1)."""

    def test_cli_polymarket_overrides_sim_default(self):
        """Config venue sim + --venue polymarket → trade + preflight on polymarket."""
        mod, mock_client = _make_skill_module(leaders_response=_leaders_response())
        mod.run(dry_run=False, venue="polymarket")
        mock_client.trade.assert_called_once()
        self.assertEqual(mock_client.trade.call_args[1].get("venue"), "polymarket")
        mock_client.ensure_can_trade.assert_called_once()
        self.assertEqual(
            mock_client.ensure_can_trade.call_args[1].get("venue"), "polymarket")

    def test_cli_sim_overrides_polymarket_env(self):
        """Config/env venue polymarket + --venue sim → trade executes on sim."""
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(),
            config_overrides={"venue": "polymarket"},
        )
        mod.run(dry_run=False, venue="sim")
        mock_client.trade.assert_called_once()
        self.assertEqual(mock_client.trade.call_args[1].get("venue"), "sim")
        # sim runs must not be gated by the polymarket balance preflight
        mock_client.ensure_can_trade.assert_not_called()


class TestPreflightFailClosed(unittest.TestCase):
    """LIVE polymarket runs abort when the balance preflight raises (codex P1)."""

    def test_live_polymarket_aborts_when_preflight_raises(self):
        mod, mock_client = _make_skill_module(leaders_response=_leaders_response())
        mock_client.ensure_can_trade.side_effect = Exception("status fetch timed out")
        json_lines, _ = _run_capturing(mod, dry_run=False, venue="polymarket")
        # Zero trades executed, no fallback to full MAX_USD
        mock_client.trade.assert_not_called()
        # No trade plan should even be requested
        for call in mock_client._request.call_args_list:
            self.assertNotIn("copytrading/execute", str(call))
        # Automaton skip block emitted with the right reason
        self.assertEqual(len(json_lines), 1)
        data = json.loads(json_lines[0])
        self.assertEqual(data["automaton"]["skip_reason"], "preflight_unavailable")
        self.assertEqual(data["automaton"]["trades_executed"], 0)

    def test_dry_run_proceeds_when_preflight_unavailable(self):
        """Dry-run places nothing, so it may proceed without the preflight."""
        mod, mock_client = _make_skill_module(leaders_response=_leaders_response())
        mock_client.ensure_can_trade.side_effect = Exception("status fetch timed out")
        mod.run(dry_run=True, venue="polymarket")
        mock_client.trade.assert_not_called()
        paths_called = [str(c) for c in mock_client._request.call_args_list]
        self.assertTrue(any("copytrading/execute" in p for p in paths_called))


class TestFetchLeaders(unittest.TestCase):
    """fetch_leaders() response parsing."""

    def test_parses_valid_response(self):
        response = {
            "refreshed_at": "2026-06-10T02:00:00+00:00",
            "leaders": [
                {"wallet": "0xabc", "backtest_copy_pnl_usdc": 120.0,
                 "slippage_cost_rate_pct": 1.5, "trade_count": 15},
                {"wallet": "0xdef", "backtest_copy_pnl_usdc": 85.0,
                 "slippage_cost_rate_pct": 2.0, "trade_count": 11},
            ],
        }
        mod, _ = _make_skill_module(leaders_response=response)
        wallets = mod.fetch_leaders()
        self.assertEqual(wallets, ["0xabc", "0xdef"])

    def test_returns_empty_on_503_http_error(self):
        """The real endpoint raises HTTP 503 when the cache isn't populated
        (simmer_v3/api/routers/wc.py) — there is no cache_empty:true on 200."""
        mod, mock_client = _make_skill_module(leaders_response={})

        def _raise_503(method, path, **kwargs):
            if "wc/copy-leaders" in path:
                raise Exception(
                    "503 Server Error: Service Unavailable for url: "
                    "https://api.simmer.markets/api/sdk/wc/copy-leaders")
            return {}

        mock_client._request.side_effect = _raise_503
        wallets = mod.fetch_leaders()
        self.assertEqual(wallets, [])

    def test_returns_empty_on_not_yet_computed_message(self):
        mod, mock_client = _make_skill_module(leaders_response={})

        def _raise_503(method, path, **kwargs):
            if "wc/copy-leaders" in path:
                raise Exception("503 leader set not yet computed")
            return {}

        mock_client._request.side_effect = _raise_503
        wallets = mod.fetch_leaders()
        self.assertEqual(wallets, [])

    def test_does_not_swallow_unrelated_cache_errors(self):
        """Only 503 / not-yet-computed is a clean skip; other errors re-raise."""
        mod, mock_client = _make_skill_module(leaders_response={})

        def _raise_other(method, path, **kwargs):
            if "wc/copy-leaders" in path:
                raise Exception("connection cache poisoned: upstream DNS failure")
            return {}

        mock_client._request.side_effect = _raise_other
        with self.assertRaises(Exception):
            mod.fetch_leaders()

    def test_drops_entries_without_wallet(self):
        response = {
            "refreshed_at": "2026-06-10T02:00:00+00:00",
            "leaders": [
                {"wallet": "0xabc", "backtest_copy_pnl_usdc": 50.0,
                 "slippage_cost_rate_pct": 1.0, "trade_count": 12},
                {"backtest_copy_pnl_usdc": 99.0},  # missing wallet
            ],
        }
        mod, _ = _make_skill_module(leaders_response=response)
        wallets = mod.fetch_leaders()
        self.assertEqual(wallets, ["0xabc"])


class TestRunDryRun(unittest.TestCase):
    """run() in dry-run mode."""

    def test_exits_cleanly_on_empty_leaders(self):
        """Unpopulated cache = real HTTP 503 from the server → clean exit."""
        mod, mock_client = _make_skill_module(leaders_response={})

        def _raise_503(method, path, **kwargs):
            if "wc/copy-leaders" in path:
                raise Exception(
                    "503 Server Error: Service Unavailable for url: "
                    "https://api.simmer.markets/api/sdk/wc/copy-leaders")
            return {}

        mock_client._request.side_effect = _raise_503
        mod.run(dry_run=True)
        mock_client._request.assert_any_call("GET", "/api/sdk/wc/copy-leaders")
        # copytrading/execute must NOT be called
        for call in mock_client._request.call_args_list:
            self.assertNotIn("copytrading/execute", str(call))

    def test_dry_run_does_not_call_trade(self):
        response = {
            "refreshed_at": "2026-06-10T02:00:00+00:00",
            "leaders": [{"wallet": "0xabc", "backtest_copy_pnl_usdc": 50.0,
                         "slippage_cost_rate_pct": 1.0, "trade_count": 12}],
        }
        mod, mock_client = _make_skill_module(leaders_response=response)
        mod.run(dry_run=True)
        mock_client.trade.assert_not_called()

    def test_dry_run_calls_copytrading_execute(self):
        response = {
            "refreshed_at": "2026-06-10T02:00:00+00:00",
            "leaders": [{"wallet": "0xwallet1", "backtest_copy_pnl_usdc": 50.0,
                         "slippage_cost_rate_pct": 1.0, "trade_count": 12}],
        }
        mod, mock_client = _make_skill_module(leaders_response=response)
        mod.run(dry_run=True)
        paths_called = [str(c) for c in mock_client._request.call_args_list]
        self.assertTrue(any("copytrading/execute" in p for p in paths_called))

    def test_execute_payload_contains_leaders(self):
        wallets_expected = ["0xwallet1", "0xwallet2"]
        response = {
            "refreshed_at": "2026-06-10T02:00:00+00:00",
            "leaders": [
                {"wallet": w, "backtest_copy_pnl_usdc": 50.0,
                 "slippage_cost_rate_pct": 1.0, "trade_count": 12}
                for w in wallets_expected
            ],
        }
        mod, mock_client = _make_skill_module(leaders_response=response)
        mod.run(dry_run=True)
        for call_args in mock_client._request.call_args_list:
            args, kwargs = call_args
            if "copytrading/execute" in str(args):
                payload = kwargs.get("json") or {}
                self.assertEqual(set(payload.get("wallets", [])), set(wallets_expected))
                break


class TestDryRunFlag(unittest.TestCase):
    """--dry-run is authoritative even when combined with --live (codex P2)."""

    def test_live_plus_dry_run_runs_dry(self):
        mod, mock_client = _make_skill_module(leaders_response=_leaders_response())
        with patch.object(sys, "argv", ["copytrader.py", "--live", "--dry-run"]):
            mod.main()
        mock_client.trade.assert_not_called()
        # The dry-run plan is still requested
        paths_called = [str(c) for c in mock_client._request.call_args_list]
        self.assertTrue(any("copytrading/execute" in p for p in paths_called))

    def test_live_alone_executes(self):
        mod, mock_client = _make_skill_module(leaders_response=_leaders_response())
        with patch.object(sys, "argv", ["copytrader.py", "--live"]):
            mod.main()
        mock_client.trade.assert_called_once()


class TestRunLive(unittest.TestCase):
    """run() in live mode."""

    def test_live_calls_trade(self):
        response = {
            "refreshed_at": "2026-06-10T02:00:00+00:00",
            "leaders": [{"wallet": "0xabc", "backtest_copy_pnl_usdc": 50.0,
                         "slippage_cost_rate_pct": 1.0, "trade_count": 12}],
        }
        mod, mock_client = _make_skill_module(leaders_response=response)
        mod.run(dry_run=False)
        mock_client.trade.assert_called_once()
        kwargs = mock_client.trade.call_args[1]
        self.assertEqual(kwargs["market_id"], "mkt-abc")
        self.assertEqual(kwargs["side"], "yes")
        self.assertEqual(kwargs["skill_slug"], "polymarket-worldcup-copytrader")


class TestPartialLeaderSetGate(unittest.TestCase):
    """WC_COPYTRADER_MIN_LEADERS gates degraded curation caches (codex P2)."""

    def test_partial_leader_set_exits_cleanly(self):
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(2),
            config_overrides={"min_leaders": 5},
        )
        json_lines, captured = _run_capturing(mod, dry_run=True)
        # No trade plan requested, no trades
        for call in mock_client._request.call_args_list:
            self.assertNotIn("copytrading/execute", str(call))
        mock_client.trade.assert_not_called()
        # Automaton skip block with the right reason
        self.assertEqual(len(json_lines), 1)
        data = json.loads(json_lines[0])
        self.assertEqual(data["automaton"]["skip_reason"], "partial_leader_set")
        # Message mentions the degraded curation cache
        self.assertTrue(any("degraded" in l for l in captured))

    def test_full_leader_set_proceeds(self):
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(5),
            config_overrides={"min_leaders": 5},
        )
        mod.run(dry_run=True)
        paths_called = [str(c) for c in mock_client._request.call_args_list]
        self.assertTrue(any("copytrading/execute" in p for p in paths_called))


class TestClientSideCaps(unittest.TestCase):
    """Belt-and-braces client-side enforcement of MAX_TRADES + per-position cap (codex P2)."""

    @staticmethod
    def _plan(n, cost=3.0):
        return {
            "success": True,
            "wallets_analyzed": 1,
            "positions_found": n,
            "conflicts_skipped": 0,
            "trades": [
                {"market_id": f"mkt-{i}", "action": "buy", "side": "yes",
                 "shares": 5.0, "estimated_price": 0.6, "estimated_cost": cost,
                 "market_title": f"Market {i}"}
                for i in range(n)
            ],
        }

    def test_truncates_plan_to_max_trades(self):
        """Server returning MAX_TRADES+3 trades executes only MAX_TRADES."""
        plan = self._plan(13)  # MAX_TRADES (10) + 3
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(), plan_response=plan)
        mod.run(dry_run=False)
        self.assertEqual(mock_client.trade.call_count, 10)

    def test_skips_oversized_trade_and_executes_rest(self):
        """A trade whose cost exceeds the per-position cap is skipped, not placed."""
        plan = self._plan(3)
        plan["trades"][1]["estimated_cost"] = 999.0  # > effective cap of 30
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(), plan_response=plan)
        mod.run(dry_run=False)
        self.assertEqual(mock_client.trade.call_count, 2)
        traded = [c[1]["market_id"] for c in mock_client.trade.call_args_list]
        self.assertEqual(traded, ["mkt-0", "mkt-2"])


class TestAutomatonEmission(unittest.TestCase):
    """Automaton JSON emitted correctly."""

    def _run_with_automaton(self, leaders_response, plan_response=None, dry_run=True):
        mod, mock_client = _make_skill_module(
            leaders_response=leaders_response,
            plan_response=plan_response,
        )
        with patch.dict(os.environ, {"AUTOMATON_MANAGED": "1", "SIMMER_API_KEY": "sk_test"}):
            mod._automaton_reported = False
            captured = []
            original_print = print

            def capturing_print(*args, **kwargs):
                line = " ".join(str(a) for a in args)
                captured.append(line)
                original_print(*args, **kwargs)

            with patch("builtins.print", side_effect=capturing_print):
                mod.run(dry_run=dry_run)

        json_lines = [l for l in captured if l.strip().startswith('{"automaton"')]
        return json_lines

    def test_emits_automaton_on_empty_leaders(self):
        """Unpopulated cache = real HTTP 503 → automaton skip block emitted."""
        mod, mock_client = _make_skill_module(leaders_response={})

        def _raise_503(method, path, **kwargs):
            if "wc/copy-leaders" in path:
                raise Exception(
                    "503 Server Error: Service Unavailable for url: "
                    "https://api.simmer.markets/api/sdk/wc/copy-leaders")
            return {}

        mock_client._request.side_effect = _raise_503
        lines, _ = _run_capturing(mod, dry_run=True)
        self.assertEqual(len(lines), 1)
        data = json.loads(lines[0])
        self.assertEqual(data["automaton"]["skip_reason"], "leaders_cache_empty")

    def test_emits_automaton_on_dry_run(self):
        response = {
            "refreshed_at": "2026-06-10T02:00:00+00:00",
            "leaders": [{"wallet": "0xabc", "backtest_copy_pnl_usdc": 50.0,
                         "slippage_cost_rate_pct": 1.0, "trade_count": 12}],
        }
        lines = self._run_with_automaton(response, dry_run=True)
        self.assertEqual(len(lines), 1)
        data = json.loads(lines[0])
        self.assertIn("automaton", data)
        self.assertEqual(data["automaton"]["trades_executed"], 0)


class TestAutomatonMaxBetParse(unittest.TestCase):
    """Malformed AUTOMATON_MAX_BET must not crash the skill at import time."""

    def test_malformed_value_falls_back_to_config_default(self):
        with patch.dict(os.environ, {"AUTOMATON_MAX_BET": "banana"}):
            mod, _ = _make_skill_module(leaders_response={})
        self.assertEqual(mod.MAX_USD, 30.0)

    def test_valid_value_caps_max_usd(self):
        with patch.dict(os.environ, {"AUTOMATON_MAX_BET": "5"}):
            mod, _ = _make_skill_module(leaders_response={})
        self.assertEqual(mod.MAX_USD, 5.0)


class TestClawhubManifest(unittest.TestCase):
    """clawhub.json tunables cover the documented env knobs."""

    def test_tunables_include_documented_envs(self):
        manifest = os.path.join(os.path.dirname(__file__), "..", "clawhub.json")
        with open(manifest) as f:
            data = json.load(f)
        envs = {t["env"] for t in data.get("tunables", [])}
        self.assertIn("WC_COPYTRADER_DETECT_EXITS", envs)
        self.assertIn("WC_COPYTRADER_MIN_LEADERS", envs)


class TestSensitivityManifest(unittest.TestCase):
    """SKILL.md carries sensitivity marker."""

    def test_skill_md_has_sensitivity_sensitive(self):
        skill_md = os.path.join(os.path.dirname(__file__), "..", "SKILL.md")
        with open(skill_md) as f:
            content = f.read()
        self.assertIn("sensitivity: sensitive", content)
        self.assertIn("sensitivity_reason", content)

    def test_skill_md_has_world_cup_category(self):
        skill_md = os.path.join(os.path.dirname(__file__), "..", "SKILL.md")
        with open(skill_md) as f:
            content = f.read()
        self.assertIn("category: world-cup", content)

    def test_disclaimer_no_roi_claims(self):
        disclaimer = os.path.join(os.path.dirname(__file__), "..", "DISCLAIMER.md")
        with open(disclaimer) as f:
            content = f.read().lower()
        forbidden = ["guaranteed profit", "risk-free", "guaranteed return", "win rate guarantee"]
        for phrase in forbidden:
            self.assertNotIn(phrase, content, f"DISCLAIMER.md must not contain: {phrase}")

    def test_skill_md_no_roi_claims(self):
        skill_md = os.path.join(os.path.dirname(__file__), "..", "SKILL.md")
        with open(skill_md) as f:
            content = f.read().lower()
        forbidden = ["guaranteed profit", "risk-free returns", "win rate:", "roi:"]
        for phrase in forbidden:
            self.assertNotIn(phrase, content, f"SKILL.md must not contain: {phrase}")


if __name__ == "__main__":
    unittest.main()
