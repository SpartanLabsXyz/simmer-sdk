"""Tests for polymarket-worldcup-copytrader.

Covers:
- fetch_leaders parses a valid copy-leaders response
- fetch_leaders returns [] on cache_empty (503)
- fetch_leaders returns [] on exception containing '503'
- run() exits cleanly when fetch_leaders returns []
- run() emits automaton JSON on exit when AUTOMATON_MANAGED is set
- run() calls copytrading/execute with wallets from the leader set
- automaton block emitted when dry_run + positions found
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

def _make_skill_module(leaders_response, plan_response=None, trade_success=True):
    """Return a fresh copytrader module with mocked SimmerClient."""
    # Stub simmer_sdk.skill so the import succeeds without the real package
    skill_stub = types.ModuleType("simmer_sdk.skill")

    class FakeConfigPath:
        def exists(self):
            return False
        def __str__(self):
            return "/tmp/fake-config.json"

    skill_stub.load_config = lambda schema, file, slug=None: {
        "max_usd": 30.0,
        "max_trades": 10,
        "venue": "sim",
        "buy_only": "true",
        "detect_exits": "true",
    }
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

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

    def test_returns_empty_on_cache_empty(self):
        response = {"cache_empty": True, "refreshed_at": None, "leaders": []}
        mod, _ = _make_skill_module(leaders_response=response)
        wallets = mod.fetch_leaders()
        self.assertEqual(wallets, [])

    def test_returns_empty_on_503_exception(self):
        mod, mock_client = _make_skill_module(leaders_response={})

        def _raise_503(method, path, **kwargs):
            if "wc/copy-leaders" in path:
                raise Exception("503 leader set not yet computed")
            return {}

        mock_client._request.side_effect = _raise_503
        wallets = mod.fetch_leaders()
        self.assertEqual(wallets, [])

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
        response = {"cache_empty": True, "refreshed_at": None, "leaders": []}
        mod, mock_client = _make_skill_module(leaders_response=response)
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
        response = {"cache_empty": True, "refreshed_at": None, "leaders": []}
        lines = self._run_with_automaton(response)
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
