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
                       config_overrides=None, markets_response=None):
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
        "max_slippage": 0.02,
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

    # Default WC allowlist response: covers every market in the plan so
    # existing live-path tests execute unchanged. Override markets_response
    # (dict or Exception) to exercise the WC-scope filter.
    if markets_response is None:
        markets_response = {"markets": [
            {"id": t["market_id"]} for t in plan_response.get("trades", [])
        ]}

    def _request(method, path, **kwargs):
        if "wc/copy-leaders" in path:
            return leaders_response
        if "copytrading/execute" in path:
            return plan_response
        if "/api/sdk/markets" in path:
            if isinstance(markets_response, Exception):
                raise markets_response
            return markets_response
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


class TestWorldCupScope(unittest.TestCase):
    """Live execution is scoped to World Cup markets only (codex pass-2 P1).

    Curated WC leaders can hold non-WC positions; the plan endpoint has no
    market/category scope param (SDKCopytradingRequest), so the skill must
    filter client-side against the tags=world-cup allowlist.
    """

    @staticmethod
    def _mixed_plan():
        return {
            "success": True,
            "wallets_analyzed": 1,
            "positions_found": 3,
            "conflicts_skipped": 0,
            "trades": [
                {"market_id": "mkt-wc-1", "action": "buy", "side": "yes",
                 "shares": 5.0, "estimated_price": 0.6, "estimated_cost": 3.0,
                 "market_title": "Will Brazil win the 2026 World Cup?"},
                {"market_id": "mkt-btc", "action": "buy", "side": "yes",
                 "shares": 5.0, "estimated_price": 0.5, "estimated_cost": 2.5,
                 "market_title": "Will Bitcoin hit $200k in 2026?"},
                {"market_id": "mkt-wc-2", "action": "buy", "side": "no",
                 "shares": 4.0, "estimated_price": 0.4, "estimated_cost": 1.6,
                 "market_title": "Will France reach the WC final?"},
            ],
        }

    def test_non_wc_trade_skipped_wc_trades_execute(self):
        plan = self._mixed_plan()
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(),
            plan_response=plan,
            markets_response={"markets": [{"id": "mkt-wc-1"}, {"id": "mkt-wc-2"}]},
        )
        json_lines, captured = _run_capturing(mod, dry_run=False)
        traded = [c[1]["market_id"] for c in mock_client.trade.call_args_list]
        self.assertEqual(traded, ["mkt-wc-1", "mkt-wc-2"])
        # The skipped trade is recorded with the structured error (plan dict
        # is mutated in place) and a warning is printed
        btc = next(t for t in plan["trades"] if t["market_id"] == "mkt-btc")
        self.assertFalse(btc["success"])
        self.assertEqual(btc["error"], "non_wc_market_skipped")
        self.assertTrue(any("non-WC" in l or "non_wc_market_skipped" in l
                            for l in captured))

    def test_skipped_trade_records_error_and_automaton_count(self):
        plan = self._mixed_plan()
        # All trades non-WC → 0 executed, automaton reports the skips
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(),
            plan_response=plan,
            markets_response={"markets": []},
        )
        json_lines, _ = _run_capturing(mod, dry_run=False)
        mock_client.trade.assert_not_called()
        for t in plan["trades"]:
            self.assertFalse(t["success"])
            self.assertEqual(t["error"], "non_wc_market_skipped")
        self.assertEqual(len(json_lines), 1)
        data = json.loads(json_lines[0])
        self.assertEqual(data["automaton"]["trades_executed"], 0)
        self.assertEqual(data["automaton"]["non_wc_skipped"], 3)
        self.assertIn("non_wc_market_skipped", data["automaton"].get("skip_reason", ""))

    def test_allowlist_fetch_failure_live_fails_closed(self):
        """Live run with no WC allowlist must execute NOTHING."""
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(),
            plan_response=self._mixed_plan(),
            markets_response=Exception("markets endpoint down"),
        )
        json_lines, _ = _run_capturing(mod, dry_run=False)
        mock_client.trade.assert_not_called()
        self.assertEqual(len(json_lines), 1)
        data = json.loads(json_lines[0])
        self.assertEqual(data["automaton"]["skip_reason"], "wc_scope_unavailable")
        self.assertEqual(data["automaton"]["trades_executed"], 0)

    def test_empty_allowlist_live_fails_closed(self):
        """An empty WC allowlist mid-campaign means the fetch is broken — fail closed."""
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(),
            plan_response=self._mixed_plan(),
            markets_response={"markets": []},
        )
        # Empty allowlist → every trade skipped as non-WC (none executed).
        # (Distinct from fetch *failure*; both end with zero spend.)
        json_lines, _ = _run_capturing(mod, dry_run=False)
        mock_client.trade.assert_not_called()

    def test_dry_run_unaffected_by_allowlist_failure(self):
        """Dry-run places nothing, so it must not fetch (or die on) the allowlist."""
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(),
            plan_response=self._mixed_plan(),
            markets_response=Exception("markets endpoint down"),
        )
        mod.run(dry_run=True)
        mock_client.trade.assert_not_called()
        # The plan is still requested and shown
        paths_called = [str(c) for c in mock_client._request.call_args_list]
        self.assertTrue(any("copytrading/execute" in p for p in paths_called))

    def test_allowlist_uses_world_cup_tag(self):
        """The allowlist must come from the structured tags=world-cup surface."""
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(),
            plan_response=self._mixed_plan(),
            markets_response={"markets": [{"id": "mkt-wc-1"}, {"id": "mkt-wc-2"}]},
        )
        mod.run(dry_run=False)
        markets_calls = [c for c in mock_client._request.call_args_list
                         if "/api/sdk/markets" in str(c)]
        self.assertEqual(len(markets_calls), 1)
        params = markets_calls[0][1].get("params") or {}
        self.assertEqual(params.get("tags"), "world-cup")


class TestOrderType(unittest.TestCase):
    """Live copy orders must be non-resting (codex pass-3 P1).

    This is a once-daily fire-and-forget automation: a partially/unfilled GTC
    order rests on the book, and the next day's plan recomputes from POSITIONS
    (not open orders), so stale resting orders can double-fill later and bypass
    MAX_USD/MAX_TRADES. FAK (fill-and-kill) is supported end-to-end (SDK
    ORDER_TYPES + server Literal["GTC","GTD","FOK","FAK"] validation) and
    leaves nothing resting.
    """

    def test_live_trades_use_fak(self):
        mod, mock_client = _make_skill_module(leaders_response=_leaders_response())
        mod.run(dry_run=False)
        mock_client.trade.assert_called_once()
        self.assertEqual(mock_client.trade.call_args[1].get("order_type"), "FAK")

    def test_every_trade_in_multi_trade_plan_uses_fak(self):
        plan = TestClientSideCaps._plan(3)
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(), plan_response=plan)
        mod.run(dry_run=False)
        self.assertEqual(mock_client.trade.call_count, 3)
        for c in mock_client.trade.call_args_list:
            self.assertEqual(c[1].get("order_type"), "FAK")

    def test_no_resting_order_types_in_source(self):
        """Regression guard: the skill must not place GTC/GTD anywhere."""
        src_path = os.path.join(os.path.dirname(__file__), "..", "copytrader.py")
        with open(src_path) as f:
            src = f.read()
        self.assertNotIn('order_type="GTC"', src)
        self.assertNotIn('order_type="GTD"', src)


class TestAutoRedeemGate(unittest.TestCase):
    """auto_redeem() broadcasts real Polymarket redemption txs — it must run
    only on LIVE polymarket runs (codex pass-3 P2). Dry-run announces what it
    would do; sim venue skips entirely (no on-chain redemption exists there).
    """

    def test_dry_run_polymarket_never_calls_auto_redeem(self):
        mod, mock_client = _make_skill_module(leaders_response=_leaders_response())
        _, captured = _run_capturing(mod, dry_run=True, venue="polymarket")
        mock_client.auto_redeem.assert_not_called()
        self.assertTrue(any("would auto-redeem" in l for l in captured))

    def test_dry_run_sim_never_calls_auto_redeem(self):
        mod, mock_client = _make_skill_module(leaders_response=_leaders_response())
        mod.run(dry_run=True, venue="sim")
        mock_client.auto_redeem.assert_not_called()

    def test_live_polymarket_calls_auto_redeem(self):
        mod, mock_client = _make_skill_module(leaders_response=_leaders_response())
        mod.run(dry_run=False, venue="polymarket")
        mock_client.auto_redeem.assert_called_once()

    def test_live_sim_does_not_call_auto_redeem(self):
        mod, mock_client = _make_skill_module(leaders_response=_leaders_response())
        mod.run(dry_run=False, venue="sim")
        mock_client.auto_redeem.assert_not_called()


class TestLivePriceBound(unittest.TestCase):
    """Live FAK orders must carry a price bound (codex pass-4 P1).

    A FAK without a price fills at whatever the book offers at execution
    time — if the market moved between the copytrading plan and this loop,
    the order fills at the worse price while still spending estimated_cost.
    The fix bounds each live polymarket order at the plan's own
    estimated_price ± MAX_SLIPPAGE (fractional, matching the base
    polymarket-copytrading skill's REACTOR_PRICE_BUFFER convention),
    turning the FAK into a marketable limit: fill up to the cap, kill the
    rest. A capped FAK that can't fill records a failed trade server-side
    ("no liquidity at this price") — it never rests.
    """

    @staticmethod
    def _plan(action="buy", estimated_price=0.6, include_price=True):
        trade = {
            "market_id": "mkt-abc",
            "action": action,
            "side": "yes",
            "shares": 5.0,
            "estimated_cost": 3.0,
            "market_title": "Will Brazil win the 2026 WC?",
        }
        if include_price:
            trade["estimated_price"] = estimated_price
        return {"success": True, "wallets_analyzed": 1, "positions_found": 1,
                "conflicts_skipped": 0, "trades": [trade]}

    def test_live_polymarket_buy_carries_bounded_price(self):
        mod, mock_client = _make_skill_module(leaders_response=_leaders_response())
        mod.run(dry_run=False, venue="polymarket")
        mock_client.trade.assert_called_once()
        kwargs = mock_client.trade.call_args[1]
        # default plan: estimated_price 0.6, tol 2% → 0.612 cap
        self.assertEqual(kwargs.get("price"), round(0.6 * 1.02, 4))
        self.assertEqual(kwargs.get("order_type"), "FAK")

    def test_buy_price_capped_at_0999(self):
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(),
            plan_response=self._plan(estimated_price=0.995))
        mod.run(dry_run=False, venue="polymarket")
        self.assertEqual(mock_client.trade.call_args[1].get("price"), 0.999)

    def test_live_polymarket_sell_carries_mirrored_bound(self):
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(),
            plan_response=self._plan(action="sell", estimated_price=0.6))
        mod.run(dry_run=False, venue="polymarket")
        mock_client.trade.assert_called_once()
        kwargs = mock_client.trade.call_args[1]
        self.assertEqual(kwargs.get("price"), round(0.6 * 0.98, 4))

    def test_sell_price_floored_at_0001(self):
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(),
            plan_response=self._plan(action="sell", estimated_price=0.001))
        mod.run(dry_run=False, venue="polymarket")
        self.assertEqual(mock_client.trade.call_args[1].get("price"), 0.001)

    def test_missing_estimated_price_skips_trade(self):
        """Never send an unbounded live order — skip with error=no_price_bound."""
        plan = self._plan(include_price=False)
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(), plan_response=plan)
        lines, _ = _run_capturing(mod, dry_run=False, venue="polymarket")
        mock_client.trade.assert_not_called()
        self.assertEqual(plan["trades"][0].get("error"), "no_price_bound")
        self.assertIs(plan["trades"][0].get("success"), False)
        data = json.loads(lines[0])
        self.assertEqual(data["automaton"]["trades_executed"], 0)

    def test_zero_estimated_price_skips_trade(self):
        plan = self._plan(estimated_price=0)
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(), plan_response=plan)
        mod.run(dry_run=False, venue="polymarket")
        mock_client.trade.assert_not_called()
        self.assertEqual(plan["trades"][0].get("error"), "no_price_bound")

    def test_slippage_tunable_widens_bound(self):
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(),
            config_overrides={"max_slippage": 0.05})
        mod.run(dry_run=False, venue="polymarket")
        self.assertEqual(mock_client.trade.call_args[1].get("price"),
                         round(0.6 * 1.05, 4))

    def test_slippage_out_of_range_clamped(self):
        """A fat-fingered tunable can't disable the price bound."""
        mod, _ = _make_skill_module(
            leaders_response=_leaders_response(),
            config_overrides={"max_slippage": 0.5})
        self.assertEqual(mod.MAX_SLIPPAGE, 0.10)
        mod_lo, _ = _make_skill_module(
            leaders_response=_leaders_response(),
            config_overrides={"max_slippage": 0.0})
        self.assertEqual(mod_lo.MAX_SLIPPAGE, 0.005)

    def test_slippage_malformed_falls_back_to_default(self):
        mod, _ = _make_skill_module(
            leaders_response=_leaders_response(),
            config_overrides={"max_slippage": "banana"})
        self.assertEqual(mod.MAX_SLIPPAGE, 0.02)

    def test_sim_live_trade_has_no_price(self):
        """SDK rejects price on venue='sim' (LMSR — no book to cap against)."""
        mod, mock_client = _make_skill_module(leaders_response=_leaders_response())
        mod.run(dry_run=False, venue="sim")
        mock_client.trade.assert_called_once()
        self.assertIsNone(mock_client.trade.call_args[1].get("price"))

    def test_dry_run_unaffected_by_missing_estimated_price(self):
        plan = self._plan(include_price=False)
        mod, mock_client = _make_skill_module(
            leaders_response=_leaders_response(), plan_response=plan)
        _, captured = _run_capturing(mod, dry_run=True, venue="polymarket")
        mock_client.trade.assert_not_called()
        self.assertNotIn("no_price_bound", str(plan["trades"][0].get("error")))
        self.assertTrue(any("Dry-run complete" in l for l in captured))

    def test_clawhub_manifest_has_slippage_tunable(self):
        manifest = os.path.join(os.path.dirname(__file__), "..", "clawhub.json")
        with open(manifest) as f:
            data = json.load(f)
        envs = {t["env"] for t in data.get("tunables", [])}
        self.assertIn("WC_COPYTRADER_MAX_SLIPPAGE", envs)


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

    def test_clawhub_json_has_machine_readable_sensitivity(self):
        """The publish governance gate (scripts/check-skill-governance.py, PR #184)
        reads sensitivity fields from clawhub.json — not SKILL.md frontmatter."""
        manifest = os.path.join(os.path.dirname(__file__), "..", "clawhub.json")
        with open(manifest) as f:
            data = json.load(f)  # also asserts clawhub.json stays valid JSON
        self.assertEqual(data.get("sensitivity"), "sensitive")
        self.assertTrue(str(data.get("sensitivity_reason") or "").strip(),
                        "sensitive skill must carry a non-empty sensitivity_reason")
        self.assertIs(data.get("sensitivity_approved"), True,
                      "gate requires sensitivity_approved === true (Adrian approval, SIM-3044)")

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
