"""Pinned proofs for SIM-5443: nothing-ever-happens replay plane.

Under SIMMER_REPLAY=1 the skill must:
  - list markets from the Simmer tape (no tags/status — those 422)
  - never hit live Gamma
  - honor SIMMER_REPLAY_NOW for daily-spend
  - accept import status=active (replay tape already holds the market)
  - keep sports off the book when tape tags are empty
  - skip_preflight so WALLET_UNVERIFIED cannot block SimState fills

Pure-unit: no network, no live Polymarket, no SIMMER_API_KEY.
"""

import os
import sys
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _SKILL_DIR)

# test_paper_mode_venue.py already stubs simmer_sdk and imports the skill
# when CI runs the whole tests/ dir. Replacing that stub here makes
# get_client() bind a different MagicMock and the paper suite dies.
if "nothing_ever_happens" not in sys.modules:
    _mock_cfg = {
        "price_cap": 0.10,
        "max_bet_usd": 5.0,
        "max_trades_per_run": 3,
        "daily_budget": 15.0,
        "min_liquidity": 500.0,
        "min_volume_24h": 100.0,
        "candidate_pages": 20,
    }
    _sdk_stub = types.ModuleType("simmer_sdk")
    _sdk_stub.SimmerClient = MagicMock(name="SimmerClient")
    _skill_stub = types.ModuleType("simmer_sdk.skill")
    _skill_stub.load_config = lambda schema, file, slug=None: _mock_cfg.copy()
    _skill_stub.update_config = lambda updates, file, slug=None: None
    _skill_stub.get_config_path = lambda file: "/tmp/config.json"
    sys.modules["simmer_sdk"] = _sdk_stub
    sys.modules["simmer_sdk.skill"] = _skill_stub

import nothing_ever_happens as neh  # noqa: E402


REPLAY_NOW = "2026-04-14T12:00:00+00:00"
REPLAY_DT = datetime(2026, 4, 14, 12, tzinfo=timezone.utc)


def _tape_market(**overrides):
    """Replay /api/sdk/markets row: prices + volume, no Gamma tags."""
    row = {
        "id": "neh-geo-2026-04",
        "question": "Will the US recognize a new Pacific island state in 2026?",
        "slug": "us-recognize-pacific-island-2026",
        "yes_price": 0.94,
        "no_price": 0.06,
        "current_probability": 0.94,
        "volume": 2500.0,
        "resolves_at": "2026-12-31T00:00:00+00:00",
        "polymarket_condition_id": "0xcond",
        "event_id": "evt-geo-2026",
        "status": "active",
        "tags": [],
        "category": "",
    }
    row.update(overrides)
    return row


def _sports_tape(**overrides):
    row = _tape_market(
        id="nba-lakers",
        event_id="evt-nba-finals",
        question="Lakers vs Celtics NBA finals game 1",
        slug="nba-lakers-celtics-game-1",
        no_price=0.05,
        yes_price=0.95,
    )
    row.update(overrides)
    return row


class _ReplayEnvMixin:
    def tearDown(self):
        os.environ.pop("SIMMER_REPLAY", None)
        os.environ.pop("SIMMER_REPLAY_NOW", None)
        neh._client = None


class TestReplayFlag(_ReplayEnvMixin, unittest.TestCase):
    def test_replay_flag_must_be_exactly_one(self):
        os.environ["SIMMER_REPLAY"] = "true"
        self.assertFalse(neh._is_replay())
        params = neh._neh_markets_params()
        self.assertNotIn("tags", params)
        self.assertNotIn("status", params)

    def test_is_replay_true_only_for_one(self):
        os.environ["SIMMER_REPLAY"] = "1"
        self.assertTrue(neh._is_replay())


class TestReplayDiscoveryParams(_ReplayEnvMixin, unittest.TestCase):
    def test_replay_params_omit_filters_that_422(self):
        os.environ["SIMMER_REPLAY"] = "1"
        params = neh._neh_markets_params()
        self.assertNotIn("tags", params)
        self.assertNotIn("status", params)
        self.assertNotIn("q", params)
        self.assertEqual(params["limit"], 1000)


class TestFetchReplayMarkets(_ReplayEnvMixin, unittest.TestCase):
    def test_replay_request_uses_tape_omits_filters_that_422(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        client = MagicMock()
        client._request.return_value = {"markets": [_tape_market()]}
        live_gamma = MagicMock(side_effect=AssertionError("live Gamma is look-ahead"))
        with patch.object(neh, "get_client", return_value=client), \
             patch.object(neh, "_fetch_candidate_markets_live", live_gamma):
            markets = neh.fetch_candidate_markets()
        self.assertEqual(len(markets), 1)
        self.assertEqual(markets[0]["slug"], "us-recognize-pacific-island-2026")
        self.assertAlmostEqual(markets[0]["no_price"], 0.06)
        sent = client._request.call_args.kwargs["params"]
        self.assertNotIn("tags", sent)
        self.assertNotIn("status", sent)
        live_gamma.assert_not_called()
        path = client._request.call_args.args[1]
        self.assertEqual(path, "/api/sdk/markets")

    def test_replay_422_raises_not_empty_list(self):
        os.environ["SIMMER_REPLAY"] = "1"
        client = MagicMock()
        client._request.side_effect = Exception("422 replay does not filter on 'tags'")
        with patch.object(neh, "get_client", return_value=client), \
             self.assertRaises(neh.MarketFetchError):
            neh.fetch_candidate_markets()

    def test_empty_200_is_honest_empty_tape_not_failure(self):
        os.environ["SIMMER_REPLAY"] = "1"
        client = MagicMock()
        client._request.return_value = {"markets": []}
        with patch.object(neh, "get_client", return_value=client):
            self.assertEqual(neh.fetch_candidate_markets(), [])

    def test_live_still_uses_gamma_not_simmer_listing(self):
        os.environ.pop("SIMMER_REPLAY", None)
        event = {
            "slug": "will-x-happen",
            "tags": [],
            "category": "",
            "liquidity": 800,
            "volume_24h": 200,
            "markets": [{
                "question": "Will X happen?",
                "outcomes": ["Yes", "No"],
                "condition_id": "live-cond",
                "no_price": 0.05,
                "yes_price": 0.95,
                "end_date": "2099-01-01T00:00:00+00:00",
                "category": "",
                "tags": [],
            }],
        }
        gamma = MagicMock()
        gamma.get_events.return_value = ([event], None)
        gamma_mod = types.ModuleType("gamma_api")
        gamma_mod.GammaClient = MagicMock(return_value=gamma)
        with patch.dict(sys.modules, {"gamma_api": gamma_mod}):
            markets = neh.fetch_candidate_markets(pages=1)
        self.assertEqual(len(markets), 1)
        self.assertEqual(markets[0]["slug"], "will-x-happen")
        gamma.get_events.assert_called_once()

    def test_sports_filter_holds_when_tape_tags_empty(self):
        os.environ["SIMMER_REPLAY"] = "1"
        rows = [_tape_market(), _sports_tape()]
        client = MagicMock()
        client._request.return_value = {"markets": rows}
        with patch.object(neh, "get_client", return_value=client):
            markets = neh.fetch_candidate_markets()
        self.assertEqual([m["id"] if "id" in m else m["slug"] for m in markets], [
            "us-recognize-pacific-island-2026",
        ])
        self.assertEqual(len(markets), 1)
        self.assertNotIn("nba", markets[0]["slug"])

    def test_ambiguous_text_is_not_sports(self):
        """electoral college / sports betting / Trump golf must stay eligible."""
        os.environ["SIMMER_REPLAY"] = "1"
        self.assertFalse(neh._sports_in_text("Will the electoral college flip?"))
        self.assertFalse(neh._sports_in_text("Sports betting legal in Texas?"))
        self.assertFalse(neh._sports_in_text("Will Trump play golf this week?"))
        self.assertTrue(neh._sports_in_text("Lakers vs Celtics NBA finals"))
        self.assertTrue(neh._is_sports([], "", "Chiefs vs Bills", "nfl-week-1"))

    def test_live_sports_stays_tag_and_category_only(self):
        os.environ.pop("SIMMER_REPLAY", None)
        self.assertFalse(
            neh._is_sports([], "", "Will the president attend the NBA finals?", "")
        )
        self.assertTrue(neh._is_sports([{"slug": "nba"}], "", "any", ""))

    def test_grouped_event_legs_are_dropped(self):
        os.environ["SIMMER_REPLAY"] = "1"
        rows = [
            _tape_market(
                id="race-a",
                event_id="who-wins-iowa",
                question="Will Alice win Iowa?",
                slug="alice-wins-iowa",
                no_price=0.04,
                yes_price=0.96,
            ),
            _tape_market(
                id="race-b",
                event_id="who-wins-iowa",
                question="Will Bob win Iowa?",
                slug="bob-wins-iowa",
                no_price=0.05,
                yes_price=0.95,
            ),
        ]
        client = MagicMock()
        client._request.return_value = {"markets": rows}
        with patch.object(neh, "get_client", return_value=client):
            markets = neh.fetch_candidate_markets()
        self.assertEqual(markets, [])

    def test_singleton_event_id_stays(self):
        os.environ["SIMMER_REPLAY"] = "1"
        client = MagicMock()
        client._request.return_value = {"markets": [_tape_market()]}
        with patch.object(neh, "get_client", return_value=client):
            markets = neh.fetch_candidate_markets()
        self.assertEqual(len(markets), 1)


class TestReplayClockAndImport(_ReplayEnvMixin, unittest.TestCase):
    def test_clock_uses_replay_now(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        self.assertEqual(neh._clock(), REPLAY_DT)

    def test_clock_unparseable_raises_not_wall_clock(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = "not-a-date"
        with self.assertRaises(neh.ReplayClockError):
            neh._clock()

    def test_clock_missing_now_raises_not_wall_clock(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ.pop("SIMMER_REPLAY_NOW", None)
        with self.assertRaises(neh.ReplayClockError):
            neh._clock()

    def test_daily_spend_uses_replay_now_not_wall_clock(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        with patch.object(neh, "_get_spend_path") as path:
            path.return_value.exists.return_value = False
            data = neh._load_daily_spend()
        self.assertEqual(data["date"], "2026-04-14")
        self.assertEqual(data["spent"], 0.0)

    def test_import_accepts_replay_active_status(self):
        os.environ["SIMMER_REPLAY"] = "1"
        client = MagicMock()
        client.import_market.return_value = {
            "market_id": "neh-geo-2026-04",
            "question": "Will the US recognize a new Pacific island state in 2026?",
            "status": "active",
            "already_imported": True,
        }
        with patch.object(neh, "get_client", return_value=client):
            market_id, err = neh.import_market("us-recognize-pacific-island-2026")
        self.assertEqual(market_id, "neh-geo-2026-04")
        self.assertIsNone(err)

    def test_import_still_rejects_resolved(self):
        client = MagicMock()
        client.import_market.return_value = {"market_id": "x", "status": "resolved"}
        with patch.object(neh, "get_client", return_value=client):
            market_id, err = neh.import_market("done")
        self.assertIsNone(market_id)
        self.assertEqual(err, "Market already resolved")

    def test_import_active_rejected_when_not_replay(self):
        os.environ.pop("SIMMER_REPLAY", None)
        client = MagicMock()
        client.import_market.return_value = {
            "market_id": "live-id",
            "status": "active",
            "already_imported": True,
        }
        with patch.object(neh, "get_client", return_value=client):
            market_id, err = neh.import_market("live-slug")
        self.assertIsNone(market_id)
        self.assertIn("Unexpected import status: active", err)

    def test_import_unknown_status_without_id_still_errors(self):
        client = MagicMock()
        client.import_market.return_value = {"status": "pending"}
        with patch.object(neh, "get_client", return_value=client):
            market_id, err = neh.import_market("pending")
        self.assertIsNone(market_id)
        self.assertIn("Unexpected import status: pending", err)


class TestReplayEntryAndPreflight(_ReplayEnvMixin, unittest.TestCase):
    def test_replay_skips_balance_preflight(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["TRADING_VENUE"] = "polymarket"
        try:
            self.assertFalse(neh.should_run_balance_preflight(dry_run=False))
        finally:
            os.environ.pop("TRADING_VENUE", None)

    def test_replay_trade_skips_preflight(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        client = MagicMock()
        result = MagicMock()
        result.success = True
        result.trade_id = "t1"
        result.shares_bought = 10.0
        result.error = None
        result.simulated = False
        client.trade.return_value = result
        with patch.object(neh, "get_client", return_value=client):
            out = neh.execute_trade("neh-geo-2026-04", 5.0)
        self.assertTrue(out["success"])
        self.assertTrue(client.trade.call_args.kwargs["skip_preflight"])

    def test_live_trade_does_not_skip_preflight(self):
        os.environ.pop("SIMMER_REPLAY", None)
        client = MagicMock()
        result = MagicMock()
        result.success = True
        result.trade_id = "t1"
        result.shares_bought = 10.0
        result.error = None
        result.simulated = False
        client.trade.return_value = result
        with patch.object(neh, "get_client", return_value=client):
            neh.execute_trade("live-mkt", 5.0)
        self.assertFalse(client.trade.call_args.kwargs.get("skip_preflight", False))

    def test_replay_run_trades_imports_active_and_skips_gamma(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        candidate = {
            "slug": "us-recognize-pacific-island-2026",
            "question": "Will the US recognize a new Pacific island state in 2026?",
            "no_price": 0.06,
            "yes_price": 0.94,
            "liquidity": 2500.0,
            "volume_24h": 2500.0,
        }
        client = MagicMock()
        client.import_market.return_value = {
            "market_id": "neh-geo-2026-04",
            "status": "active",
            "already_imported": True,
        }
        client.get_market_context.return_value = {
            "market": {"fee_rate_bps": 0},
            "discipline": {},
        }
        result = MagicMock()
        result.success = True
        result.trade_id = "t1"
        result.shares_bought = 80.0
        result.error = None
        result.simulated = False
        client.trade.return_value = result
        live_gamma = MagicMock(side_effect=AssertionError("live Gamma is look-ahead"))
        with patch.object(neh, "get_client", return_value=client), \
             patch.object(neh, "get_positions", return_value=[]), \
             patch.object(neh, "_load_daily_spend", return_value={
                 "date": "2026-04-14", "spent": 0.0, "trades": 0,
             }), \
             patch.object(neh, "_save_daily_spend"), \
             patch.object(neh, "_fetch_candidate_markets_live", live_gamma):
            signals, attempted, executed, skips, total_usd, errors = neh.run_trades(
                [candidate], dry_run=False, quiet=True
            )
        self.assertEqual(signals, 1)
        self.assertEqual(executed, 1)
        self.assertEqual(errors, [])
        live_gamma.assert_not_called()
        self.assertTrue(client.trade.call_args.kwargs["skip_preflight"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestStandaloneNoEventId(unittest.TestCase):
    """Pass-2 P1: unknown event membership is not standalone."""

    def test_rows_without_event_id_are_dropped(self):
        rows = [
            {"id": "a", "question": "Will X happen?", "slug": "x", "event_id": None},
            {"id": "b", "question": "Will Y happen?", "slug": "y"},
            {"id": "c", "question": "Will Z happen?", "slug": "z", "event_id": "evt-z"},
        ]
        kept = neh._standalone_tape_rows(rows)
        self.assertEqual([r["id"] for r in kept], ["c"])
