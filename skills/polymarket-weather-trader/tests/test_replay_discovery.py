"""Pinned proofs for #368 + SIM-5428: replay discovery/entry gate.

Replay rejects tags/status (sdk 0.25.3/0.25.4, 422). The skill uses the
existing replay q= path under SIMMER_REPLAY=1. A failed listing must raise
so the harness can mark failed_ticks — returning [] exits 0 and paints
bundle.clean=true on a 0-eval tick.

SIM-5428 extends that into an explicit keep/kill gate: the discovery+entry
path must work under replay (frozen clock, replay price fields, city
fallback when the tape omits resolution_criteria, no live NOAA look-ahead,
replay-aware SDK preflight so WALLET_UNVERIFIED cannot block SimState fills).

SIM-5429 adds the archive loader (`SIMMER_REPLAY_FORECASTS` / user-supplied
fixtures/replay_forecasts.json). The committed .sample.json is never auto-loaded.
SIM-5434 adds the builder; the loader ignores a `_meta` provenance block.
When a day has ``leads``, replay picks the smallest N in 1–3 such that
every hourly issuance precedes the tick (station-local 23:59:59 → UTC).
Archives without ``leads`` keep top-level high/low.

Pure-unit: no network, no live Polymarket, no SIMMER_API_KEY.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


_SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SAMPLE_ARCHIVE = os.path.join(
    _SKILL_DIR, "fixtures", "replay_forecasts.sample.json"
)
_USER_ARCHIVE = os.path.join(_SKILL_DIR, "fixtures", "replay_forecasts.json")
sys.path.insert(0, _SKILL_DIR)


def _write_archive(data: dict) -> str:
    fd, path = tempfile.mkstemp(suffix=".json", prefix="wx-replay-fcst-")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def _empty_archive() -> str:
    return _write_archive({})

_mock_cfg = {
    "entry_threshold": 0.15,
    "min_entry_price": 0.0,
    "min_hours_to_resolve": 2,
    "exit_threshold": 0.45,
    "max_position_usd": 2.00,
    "sizing_pct": 0.05,
    "max_trades_per_run": 5, "max_buys_per_market": 1,
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


class _PatchDefaultArchiveMixin:
    """Empty-archive tests must not read the real user archive path.

    KEEP docs say ``cp <window> fixtures/replay_forecasts.json``. If the
    pinned gate asserts that file is absent, following the docs turns
    ``run_backtest_gate.py`` red (KILL). Point the default at a missing
    temp path instead.
    """

    def setUp(self):
        self._archive_tmpdir = tempfile.mkdtemp(prefix="wx-replay-default-")
        self._missing_default = os.path.join(
            self._archive_tmpdir, "replay_forecasts.json"
        )
        p = patch.object(wt, "_DEFAULT_REPLAY_FORECASTS_PATH", self._missing_default)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(
            lambda: shutil.rmtree(self._archive_tmpdir, ignore_errors=True)
        )


class TestWeatherMarketsParams(unittest.TestCase):
    """Live keeps tags=weather; replay uses q=temperature only."""

    def tearDown(self):
        os.environ.pop("SIMMER_REPLAY", None)

    def test_live_sends_tags_and_status_not_q(self):
        os.environ.pop("SIMMER_REPLAY", None)
        params = wt._weather_markets_params()
        self.assertEqual(params["tags"], "weather")
        self.assertEqual(params["status"], "active")
        self.assertNotIn("q", params)
        self.assertEqual(params["include"], "resolution_criteria")

    def test_replay_uses_q_temperature_omits_tags_status(self):
        os.environ["SIMMER_REPLAY"] = "1"
        params = wt._weather_markets_params()
        self.assertEqual(params["q"], "temperature")
        self.assertNotIn("tags", params)
        self.assertNotIn("status", params)
        self.assertEqual(params["include"], "resolution_criteria")

    def test_replay_flag_must_be_exactly_one(self):
        os.environ["SIMMER_REPLAY"] = "true"
        self.assertFalse(wt._is_replay())
        params = wt._weather_markets_params()
        self.assertEqual(params.get("tags"), "weather")


class TestFetchWeatherMarketsFailClosed(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("SIMMER_REPLAY", None)
        wt._client = None

    def test_live_request_uses_live_params(self):
        client = MagicMock()
        client._request.return_value = {"markets": [{"id": "m1"}]}
        wt.get_client = MagicMock(return_value=client)
        os.environ.pop("SIMMER_REPLAY", None)

        markets = wt.fetch_weather_markets()

        # Same market on every horizon page dedupes to one row.
        self.assertEqual(markets, [{"id": "m1"}])
        calls = client._request.call_args_list
        # Base page first (no q), then one dated page per horizon day.
        self.assertGreaterEqual(len(calls), 2)
        for call in calls:
            _method, path = call.args[:2]
            self.assertEqual(_method, "GET")
            self.assertEqual(path, "/api/sdk/markets")
            sent = call.kwargs["params"]
            self.assertEqual(sent["tags"], "weather")
            self.assertEqual(sent["status"], "active")
        self.assertNotIn("q", calls[0].kwargs["params"])
        for call in calls[1:]:
            self.assertIn("q", call.kwargs["params"])

    def test_replay_request_omits_filters_that_422(self):
        client = MagicMock()
        client._request.return_value = {"markets": []}
        wt.get_client = MagicMock(return_value=client)
        os.environ["SIMMER_REPLAY"] = "1"

        markets = wt.fetch_weather_markets()

        self.assertEqual(markets, [])
        sent = client._request.call_args.kwargs["params"]
        self.assertEqual(sent["q"], "temperature")
        self.assertNotIn("tags", sent)
        self.assertNotIn("status", sent)

    def test_replay_422_raises_not_empty_list(self):
        """The dogfood 422 must not become [] / exit 0."""
        client = MagicMock()
        client._request.side_effect = Exception(
            "422 replay does not filter on 'tags'"
        )
        wt.get_client = MagicMock(return_value=client)
        os.environ["SIMMER_REPLAY"] = "1"

        with self.assertRaises(wt.MarketFetchError) as ctx:
            wt.fetch_weather_markets()
        self.assertIn("Failed to fetch markets", str(ctx.exception))

    def test_any_listing_failure_raises_market_fetch_error(self):
        client = MagicMock()
        client._request.side_effect = RuntimeError("connection refused")
        wt.get_client = MagicMock(return_value=client)

        with self.assertRaises(wt.MarketFetchError):
            wt.fetch_weather_markets()

    def test_empty_200_is_honest_empty_tape_not_failure(self):
        client = MagicMock()
        client._request.return_value = {"markets": []}
        wt.get_client = MagicMock(return_value=client)

        self.assertEqual(wt.fetch_weather_markets(), [])


class TestRunFailClosedOnFetch(unittest.TestCase):
    """run_weather_strategy must not swallow MarketFetchError (exit 0 / clean)."""

    def tearDown(self):
        wt._client = None

    def test_run_propagates_fetch_failure(self):
        client = MagicMock()
        client.auto_redeem.return_value = []
        fetch = MagicMock(
            side_effect=wt.MarketFetchError("Failed to fetch markets from Simmer API")
        )
        # patch.object restores the module attributes after the test; a bare
        # assignment leaks the raising mock into whichever test runs next.
        with patch.object(wt, "get_client", MagicMock(return_value=client)), \
             patch.object(wt, "discover_and_import_weather_markets", MagicMock(return_value=0)), \
             patch.object(wt, "fetch_weather_markets", fetch), \
             self.assertRaises(wt.MarketFetchError):
            wt.run_weather_strategy(dry_run=True, quiet=True)


# ---------------------------------------------------------------------------
# SIM-5428: discovery+entry gate (same harness, explicit pass/fail)
# ---------------------------------------------------------------------------

REPLAY_NOW = "2026-04-30T12:00:00+00:00"
REPLAY_DT = datetime(2026, 4, 30, 12, tzinfo=timezone.utc)


def _replay_market(**overrides):
    """Replay /api/sdk/markets row: yes_price, no resolution_criteria."""
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


class TestReplayClockAndPrice(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("SIMMER_REPLAY", None)
        os.environ.pop("SIMMER_REPLAY_NOW", None)

    def test_clock_uses_replay_now(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        self.assertEqual(wt._clock(), REPLAY_DT)

    def test_horizon_and_parse_use_replay_now_not_wall_clock(self):
        """Wall-clock Sept 2026 would roll April 30 → 2027 and drop the tape."""
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        self.assertEqual(wt.discovery_event_dates()[0], "2026-04-30")
        parsed = wt.parse_weather_event(
            "Highest temperature in New York City on April 30"
        )
        self.assertEqual(parsed["date"], "2026-04-30")

    def test_live_price_is_external_or_half(self):
        """Live path is `external_price_yes or 0.5` — None and 0.0 stay 0.5."""
        os.environ.pop("SIMMER_REPLAY", None)
        self.assertEqual(
            wt._market_yes_price(
                {"external_price_yes": None, "current_probability": 0.10}
            ),
            0.5,
        )
        self.assertEqual(wt._market_yes_price({"external_price_yes": 0.0}), 0.5)
        self.assertEqual(wt._market_yes_price({"external_price_yes": 0.12}), 0.12)

    def test_replay_yes_price_not_default_half(self):
        os.environ["SIMMER_REPLAY"] = "1"
        self.assertEqual(wt._market_yes_price({"yes_price": 0.10}), 0.10)
        self.assertEqual(wt._market_yes_price({"current_probability": 0.11}), 0.11)
        self.assertEqual(wt._market_yes_price({}), 0.5)

    def test_seconds_to_resolution_honors_hours_floor(self):
        """Replay context has seconds_to_resolution, not '10h'."""
        wt.TIME_TO_RESOLUTION_MIN_HOURS = 24
        try:
            ctx = {
                "market": {"seconds_to_resolution": 10 * 3600},
                "warnings": [],
                "discipline": {},
                "slippage": {},
                "edge": {},
            }
            ok, reasons = wt.check_context_safeguards(ctx)
            self.assertFalse(ok)
            self.assertTrue(any("too soon" in r for r in reasons))
        finally:
            wt.TIME_TO_RESOLUTION_MIN_HOURS = 2


class TestReplayStationAndForecast(_PatchDefaultArchiveMixin, unittest.TestCase):
    def tearDown(self):
        os.environ.pop("SIMMER_REPLAY", None)
        os.environ.pop("SIMMER_REPLAY_FORECASTS", None)
        wt.reset_replay_forecasts()

    def test_city_fallback_nyc_not_dallas(self):
        station, is_intl = wt._city_fallback_station("NYC")
        self.assertEqual(station, "KLGA")
        self.assertFalse(is_intl)
        # Dallas stays out of LOCATIONS — same KDFW/KDAL rule as live.
        self.assertIsNone(wt._city_fallback_station("Dallas")[0])

    def test_replay_forecast_never_calls_live_noaa(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_FORECASTS"] = _empty_archive()
        wt.reset_replay_forecasts()
        noaa = MagicMock(side_effect=AssertionError("live NOAA is look-ahead"))
        with patch.object(wt, "get_noaa_forecast_for_station", noaa), \
             patch.object(wt, "get_openmeteo_forecast_for_station", noaa):
            self.assertEqual(wt._station_forecast("KLGA", False), {})
        noaa.assert_not_called()

    def test_replay_forecast_uses_inject(self):
        os.environ["SIMMER_REPLAY"] = "1"
        wt._REPLAY_FORECASTS["KLGA"] = {"2026-04-30": {"high": 99, "low": 50}}
        self.assertEqual(
            wt._station_forecast("KLGA", False)["2026-04-30"]["high"], 99
        )


class TestReplayPreflight(unittest.TestCase):
    """Replay trades must not depend on the deprecated skip_preflight valve."""

    def tearDown(self):
        os.environ.pop("SIMMER_REPLAY", None)
        wt._client = None

    def test_replay_trade_omits_skip_preflight_and_emits_no_deprecation(self):
        os.environ["SIMMER_REPLAY"] = "1"
        client = MagicMock()
        client.live = True
        client.venue = "polymarket"
        client.trade.return_value = _trade_ok()
        with patch.object(wt, "get_client", return_value=client), \
             patch("warnings.warn") as warn:
            result = wt.execute_trade("wx-nyc-72", "yes", 2.0)
        client.preflight.assert_not_called()
        self.assertNotIn("skip_preflight", client.trade.call_args.kwargs)
        warn.assert_not_called()
        self.assertTrue(result["success"])

    def test_replay_sell_omits_skip_preflight_and_emits_no_deprecation(self):
        os.environ["SIMMER_REPLAY"] = "1"
        client = MagicMock()
        client.live = True
        client.venue = "polymarket"
        client.trade.return_value = _trade_ok()
        with patch.object(wt, "get_client", return_value=client), \
             patch("warnings.warn") as warn:
            result = wt.execute_sell("wx-nyc-72", 3.0)
        client.preflight.assert_not_called()
        self.assertNotIn("skip_preflight", client.trade.call_args.kwargs)
        warn.assert_not_called()
        self.assertTrue(result["success"])

    def test_live_still_runs_preflight(self):
        os.environ.pop("SIMMER_REPLAY", None)
        client = MagicMock()
        client.live = True
        client.venue = "polymarket"
        pf = MagicMock()
        pf.ok_to_trade = False
        pf.blockers = ["WALLET_UNVERIFIED"]
        client.preflight.return_value = pf
        with patch.object(wt, "get_client", return_value=client):
            result = wt.execute_trade("wx-nyc-72", "yes", 2.0)
        client.trade.assert_not_called()
        self.assertEqual(result, {"error": "preflight_blocked: WALLET_UNVERIFIED"})


class TestReplayEntryPath(_PatchDefaultArchiveMixin, unittest.TestCase):
    """Pinned: discovery+entry reaches trade() under replay-shaped listings."""

    def tearDown(self):
        os.environ.pop("SIMMER_REPLAY", None)
        os.environ.pop("SIMMER_REPLAY_NOW", None)
        os.environ.pop("SIMMER_REPLAY_FORECASTS", None)
        wt.reset_replay_forecasts()
        wt._client = None

    def _run(self, markets, execute, import_fn=None):
        client = MagicMock()
        client.auto_redeem.return_value = []
        client.live = True
        noaa = MagicMock(side_effect=AssertionError("live NOAA is look-ahead"))
        import_fn = import_fn or MagicMock(
            side_effect=AssertionError("import must not run under replay")
        )
        with patch.object(wt, "get_client", MagicMock(return_value=client)), \
             patch.object(wt, "discover_and_import_weather_markets", import_fn), \
             patch.object(wt, "fetch_weather_markets", MagicMock(return_value=markets)), \
             patch.object(wt, "get_noaa_forecast_for_station", noaa), \
             patch.object(wt, "get_openmeteo_forecast_for_us_station", noaa), \
             patch.object(wt, "get_openmeteo_forecast_for_station", noaa), \
             patch.object(wt, "get_market_context", MagicMock(return_value=None)), \
             patch.object(wt, "get_price_history", MagicMock(return_value=[])), \
             patch.object(wt, "get_positions", MagicMock(return_value=[])), \
             patch.object(wt, "execute_trade", execute):
            wt.run_weather_strategy(dry_run=True, quiet=True)
        return import_fn, noaa

    def test_injected_forecast_reaches_trade(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        wt._REPLAY_FORECASTS["KLGA"] = {"2026-04-30": {"high": 72, "low": 50}}
        execute = MagicMock(return_value={
            "success": True, "trade_id": "replay-1", "shares_bought": 20,
            "simulated": True,
        })
        import_fn, noaa = self._run([_replay_market()], execute)
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[0], "wx-nyc-72")
        self.assertEqual(execute.call_args.args[1], "yes")
        import_fn.assert_not_called()
        noaa.assert_not_called()

    def test_injected_forecast_not_clobbered_by_startup_load(self):
        """99°F inject must survive startup; sample 72°F must not replace it."""
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        wt._REPLAY_FORECASTS["KLGA"] = {"2026-04-30": {"high": 99, "low": 50}}
        execute = MagicMock()
        self._run([_replay_market()], execute)
        self.assertEqual(wt._REPLAY_FORECASTS["KLGA"]["2026-04-30"]["high"], 99)
        execute.assert_not_called()

    def test_no_inject_is_honest_no_entry_not_look_ahead(self):
        """Only .sample.json exists → empty plane, 0 entries, NOAA dark."""
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        os.environ.pop("SIMMER_REPLAY_FORECASTS", None)
        execute = MagicMock()
        _import_fn, noaa = self._run([_replay_market()], execute)
        execute.assert_not_called()
        noaa.assert_not_called()
        self.assertEqual(wt._REPLAY_FORECASTS, {})

    def test_archive_file_reaches_trade(self):
        """SIM-5429: loader fills the inject plane; entry sees an explicit archive."""
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        os.environ["SIMMER_REPLAY_FORECASTS"] = _SAMPLE_ARCHIVE
        execute = MagicMock(return_value={
            "success": True, "trade_id": "replay-1", "shares_bought": 20,
            "simulated": True,
        })
        import_fn, noaa = self._run([_replay_market()], execute)
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[0], "wx-nyc-72")
        import_fn.assert_not_called()
        noaa.assert_not_called()

    def test_forced_archive_log_when_loaded(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        os.environ["SIMMER_REPLAY_FORECASTS"] = _SAMPLE_ARCHIVE
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            self._run([_replay_market()], MagicMock(return_value={
                "success": True, "trade_id": "replay-1", "shares_bought": 20,
                "simulated": True,
            }))
        text = buf.getvalue()
        self.assertIn("Replay: archive", text)
        self.assertIn(_SAMPLE_ARCHIVE, text)
        self.assertIn("stations=1", text)
        self.assertIn("2026-04-30", text)

    def test_forced_empty_archive_log_when_sample_only(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        os.environ.pop("SIMMER_REPLAY_FORECASTS", None)
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            self._run([_replay_market()], MagicMock())
        text = buf.getvalue()
        self.assertIn("no archive: NOAA dark, 0 entries is FIX", text)

    def test_unparseable_criteria_does_not_city_fallback(self):
        """Present-but-unreadable criteria still skips — not KLGA."""
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = REPLAY_NOW
        wt._REPLAY_FORECASTS["KLGA"] = {"2026-04-30": {"high": 72, "low": 50}}
        execute = MagicMock()
        self._run(
            [_replay_market(resolution_criteria="This market resolves on a coin flip.")],
            execute,
        )
        execute.assert_not_called()


class TestReplayForecastLoader(_PatchDefaultArchiveMixin, unittest.TestCase):
    """SIM-5429: env / fixture archive fills `_REPLAY_FORECASTS`; never NOAA."""

    def tearDown(self):
        os.environ.pop("SIMMER_REPLAY", None)
        os.environ.pop("SIMMER_REPLAY_NOW", None)
        os.environ.pop("SIMMER_REPLAY_FORECASTS", None)
        wt.reset_replay_forecasts()

    def test_loader_ignores_meta_block(self):
        """Builder writes `_meta`; it is provenance, not a station."""
        os.environ["SIMMER_REPLAY"] = "1"
        path = _write_archive({
            "_meta": {
                "source": "open-meteo-previous-runs",
                "fetched_at": "2026-09-16T00:00:00Z",
                "lead": "previous_day1",
            },
            "KLGA": {"2026-04-30": {"high": 72, "low": 50}},
        })
        loaded = wt.load_replay_forecasts(path)
        self.assertNotIn("_meta", loaded)
        self.assertNotIn("_meta", wt._REPLAY_FORECASTS)
        self.assertEqual(loaded["KLGA"]["2026-04-30"]["high"], 72)
        self.assertIn("stations=1", wt._replay_forecast_provenance_line())

    def test_loader_fills_dict_from_env_path(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_FORECASTS"] = _SAMPLE_ARCHIVE
        loaded = wt.load_replay_forecasts()
        self.assertEqual(loaded["KLGA"]["2026-04-30"]["high"], 72)
        self.assertEqual(wt._REPLAY_FORECASTS["KLGA"]["2026-04-30"]["low"], 50)
        self.assertNotIn("_meta", loaded)

    def test_loader_fills_dict_from_explicit_path(self):
        os.environ["SIMMER_REPLAY"] = "1"
        loaded = wt.load_replay_forecasts(_SAMPLE_ARCHIVE)
        self.assertEqual(loaded["KLGA"]["2026-04-30"]["high"], 72)

    def test_loader_is_noop_outside_replay(self):
        os.environ.pop("SIMMER_REPLAY", None)
        os.environ["SIMMER_REPLAY_FORECASTS"] = _SAMPLE_ARCHIVE
        self.assertEqual(wt.load_replay_forecasts(), {})
        self.assertEqual(wt._REPLAY_FORECASTS, {})

    def test_live_station_forecast_still_calls_noaa_not_archive(self):
        os.environ.pop("SIMMER_REPLAY", None)
        os.environ["SIMMER_REPLAY_FORECASTS"] = _SAMPLE_ARCHIVE
        noaa = MagicMock(return_value={"2026-04-30": {"high": 1, "low": 0}})
        with patch.object(wt, "get_noaa_forecast_for_station", noaa):
            out = wt._station_forecast("KLGA", False)
        noaa.assert_called_once_with("KLGA")
        self.assertEqual(out["2026-04-30"]["high"], 1)
        self.assertEqual(wt._REPLAY_FORECASTS, {})

    def test_missing_env_path_raises_not_look_ahead(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_FORECASTS"] = "/no/such/wx-replay-archive.json"
        with self.assertRaises(wt.ReplayForecastArchiveError) as ctx:
            wt.load_replay_forecasts()
        self.assertIn("not found", str(ctx.exception))
        self.assertEqual(wt._REPLAY_FORECASTS, {})

    def test_empty_archive_leaves_dict_empty(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_FORECASTS"] = _empty_archive()
        self.assertEqual(wt.load_replay_forecasts(), {})
        self.assertEqual(wt._REPLAY_FORECASTS, {})

    def test_invalid_json_raises(self):
        os.environ["SIMMER_REPLAY"] = "1"
        fd, path = tempfile.mkstemp(suffix=".json", prefix="wx-replay-bad-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("not-json")
        with self.assertRaises(wt.ReplayForecastArchiveError):
            wt.load_replay_forecasts(path)

    def test_sample_file_is_not_auto_loaded(self):
        """Committed .sample.json is not the default path → empty plane."""
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ.pop("SIMMER_REPLAY_FORECASTS", None)
        self.assertTrue(os.path.isfile(_SAMPLE_ARCHIVE))
        self.assertFalse(os.path.isfile(wt._DEFAULT_REPLAY_FORECASTS_PATH))
        self.assertEqual(wt.load_replay_forecasts(), {})
        self.assertEqual(wt._REPLAY_FORECASTS, {})

    def test_user_archive_auto_loads_when_present(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ.pop("SIMMER_REPLAY_FORECASTS", None)
        with open(self._missing_default, "w", encoding="utf-8") as fh:
            json.dump({"KLGA": {"2026-04-30": {"high": 72, "low": 50}}}, fh)
        loaded = wt.load_replay_forecasts()
        self.assertEqual(loaded["KLGA"]["2026-04-30"]["high"], 72)

    def test_provenance_line_handles_station_with_no_dates(self):
        """{"KLGA": {}} loads but has no dates; must read as FIX, not crash."""
        os.environ["SIMMER_REPLAY"] = "1"
        wt._REPLAY_FORECASTS["KLGA"] = {}
        self.assertEqual(
            wt._replay_forecast_provenance_line(),
            "Replay: no archive: NOAA dark, 0 entries is FIX",
        )

    def test_empty_plane_does_not_depend_on_working_tree_user_file(self):
        """Pinned gate stays green if KEEP docs put an archive in the skill dir."""
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ.pop("SIMMER_REPLAY_FORECASTS", None)
        self.assertNotEqual(wt._DEFAULT_REPLAY_FORECASTS_PATH, _USER_ARCHIVE)
        self.assertFalse(os.path.isfile(wt._DEFAULT_REPLAY_FORECASTS_PATH))
        self.assertEqual(wt.load_replay_forecasts(), {})
        with open(self._missing_default, "w", encoding="utf-8") as fh:
            json.dump({"KLGA": {"2026-04-30": {"high": 72, "low": 50}}}, fh)
        wt.reset_replay_forecasts()
        loaded = wt.load_replay_forecasts()
        self.assertEqual(loaded["KLGA"]["2026-04-30"]["high"], 72)

    def test_station_forecast_stays_empty_without_user_archive(self):
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ.pop("SIMMER_REPLAY_FORECASTS", None)
        noaa = MagicMock(side_effect=AssertionError("live NOAA is look-ahead"))
        with patch.object(wt, "get_noaa_forecast_for_station", noaa), \
             patch.object(wt, "get_openmeteo_forecast_for_station", noaa):
            self.assertEqual(wt._station_forecast("KLGA", False), {})
        noaa.assert_not_called()

    def test_ksea_lead_at_0030z(self):
        """P1: tick 2026-04-30T00:30Z, KSEA −25200 → Apr 30/2, May 1/3, May 2/none."""
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = "2026-04-30T00:30:00+00:00"
        wt._REPLAY_STATION_UTC_OFFSET["KSEA"] = -25200
        day = {
            "high": 11,
            "low": 10,
            "leads": {
                "1": {"high": 11, "low": 10},
                "2": {"high": 22, "low": 20},
                "3": {"high": 33, "low": 30},
            },
        }
        wt._REPLAY_FORECASTS["KSEA"] = {
            "2026-04-30": day,
            "2026-05-01": day,
            "2026-05-02": day,
        }
        self.assertEqual(wt._replay_lead_for_event("2026-04-30", "KSEA"), 2)
        self.assertEqual(wt._replay_lead_for_event("2026-05-01", "KSEA"), 3)
        self.assertIsNone(wt._replay_lead_for_event("2026-05-02", "KSEA"))
        self.assertEqual(
            wt._station_forecast("KSEA", False, event_date="2026-04-30")
            ["2026-04-30"]["high"],
            22,
        )
        self.assertEqual(
            wt._station_forecast("KSEA", False, event_date="2026-05-01")
            ["2026-05-01"]["high"],
            33,
        )
        self.assertEqual(
            wt._station_forecast("KSEA", False, event_date="2026-05-02"),
            {},
        )

    def test_llbg_lead_at_0030z(self):
        """P1: same tick, LLBG +10800 → Apr 30 lead 1, May 1 lead 2."""
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = "2026-04-30T00:30:00+00:00"
        wt._REPLAY_STATION_UTC_OFFSET["LLBG"] = 10800
        day = {
            "high": 11,
            "low": 10,
            "leads": {
                "1": {"high": 11, "low": 10},
                "2": {"high": 22, "low": 20},
                "3": {"high": 33, "low": 30},
            },
        }
        wt._REPLAY_FORECASTS["LLBG"] = {"2026-04-30": day, "2026-05-01": day}
        self.assertEqual(wt._replay_lead_for_event("2026-04-30", "LLBG"), 1)
        self.assertEqual(wt._replay_lead_for_event("2026-05-01", "LLBG"), 2)
        self.assertEqual(
            wt._station_forecast("LLBG", True, event_date="2026-04-30")
            ["2026-04-30"]["high"],
            11,
        )
        self.assertEqual(
            wt._station_forecast("LLBG", True, event_date="2026-05-01")
            ["2026-05-01"]["high"],
            22,
        )

    def test_leads_archive_no_offsets_assumes_utc_minus_12(self):
        """P1: leads without _meta offsets → UTC−12, provenance tz=assumed."""
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = "2026-04-30T00:30:00+00:00"
        path = _write_archive({
            "KLGA": {
                "2026-04-30": {
                    "high": 11,
                    "low": 10,
                    "leads": {
                        "1": {"high": 11, "low": 10},
                        "2": {"high": 22, "low": 20},
                        "3": {"high": 33, "low": 30},
                    },
                },
                "2026-05-01": {
                    "high": 11,
                    "low": 10,
                    "leads": {
                        "1": {"high": 11, "low": 10},
                        "2": {"high": 22, "low": 20},
                        "3": {"high": 33, "low": 30},
                    },
                },
                "2026-05-02": {
                    "high": 11,
                    "low": 10,
                    "leads": {
                        "1": {"high": 11, "low": 10},
                        "2": {"high": 22, "low": 20},
                        "3": {"high": 33, "low": 30},
                    },
                },
            }
        })
        wt.load_replay_forecasts(path)
        self.assertEqual(wt._REPLAY_STATION_UTC_OFFSET, {})
        self.assertEqual(wt._replay_lead_for_event("2026-04-30", "KLGA"), 2)
        self.assertEqual(wt._replay_lead_for_event("2026-05-01", "KLGA"), 3)
        self.assertIsNone(wt._replay_lead_for_event("2026-05-02", "KLGA"))
        self.assertIn("tz=assumed", wt._replay_forecast_provenance_line())

    def test_leads_less_archive_uses_top_level(self):
        """P1 (c): hand-built / sample without leads keep today's shape."""
        os.environ["SIMMER_REPLAY"] = "1"
        os.environ["SIMMER_REPLAY_NOW"] = "2026-04-29T12:00:00+00:00"
        wt._REPLAY_FORECASTS["KLGA"] = {"2026-04-30": {"high": 72, "low": 50}}
        self.assertEqual(
            wt._station_forecast("KLGA", False, event_date="2026-04-30")
            ["2026-04-30"]["high"],
            72,
        )
        self.assertEqual(
            wt._station_forecast("KLGA", False)["2026-04-30"]["high"], 72
        )

    def test_loader_preserves_leads_and_provenance(self):
        os.environ["SIMMER_REPLAY"] = "1"
        path = _write_archive({
            "_meta": {
                "source": "open-meteo-previous-runs",
                "utc_offset_seconds": {"KLGA": -14400},
            },
            "KLGA": {
                "2026-04-30": {
                    "high": 11,
                    "low": 10,
                    "leads": {
                        "1": {"high": 11, "low": 10},
                        "2": {"high": 22, "low": 20},
                        "3": {"high": 33, "low": 30},
                    },
                }
            }
        })
        loaded = wt.load_replay_forecasts(path)
        self.assertEqual(loaded["KLGA"]["2026-04-30"]["leads"]["2"]["high"], 22)
        self.assertEqual(wt._REPLAY_STATION_UTC_OFFSET["KLGA"], -14400)
        self.assertNotIn("_meta", loaded)
        line = wt._replay_forecast_provenance_line()
        self.assertIn("leads=1-3", line)
        self.assertNotIn("tz=assumed", line)


from dataclasses import dataclass as _dataclass


@_dataclass
class _FakePosition:
    market_id: str
    venue: str = "polymarket"


class TestGetPositionsReplayVenue(unittest.TestCase):
    """SIM-5484: replay's /api/sdk/positions 422s on any venue filter
    (_reject_unsupported, SIM-5067). get_positions() must omit venue under
    replay so the skill actually learns what it holds — the pre-fix
    behavior called client.get_positions(venue=client.venue), which 422s,
    gets swallowed to [], and re-buys the same bucket every tick."""

    def tearDown(self):
        os.environ.pop("SIMMER_REPLAY", None)
        wt._client = None

    def test_replay_omits_venue_filter(self):
        os.environ["SIMMER_REPLAY"] = "1"
        client = MagicMock()
        client.venue = "polymarket"
        client.get_positions.return_value = []
        with patch.object(wt, "get_client", MagicMock(return_value=client)):
            wt.get_positions()
        client.get_positions.assert_called_once_with(venue=None)

    def test_live_still_filters_by_configured_venue(self):
        os.environ.pop("SIMMER_REPLAY", None)
        client = MagicMock()
        client.venue = "polymarket"
        client.get_positions.return_value = []
        with patch.object(wt, "get_client", MagicMock(return_value=client)):
            wt.get_positions()
        client.get_positions.assert_called_once_with(venue="polymarket")

    def test_held_market_not_rebought_next_tick(self):
        """A market already held must not appear as a fresh entry candidate
        on the next tick — the actual failure mode #1 caused (13-25x
        re-buys/market: every tick, get_positions()==[] under replay, so
        the position-check that would skip an already-held market never
        fires)."""
        os.environ["SIMMER_REPLAY"] = "1"
        client = MagicMock()
        client.venue = "polymarket"
        client.get_positions.return_value = [_FakePosition(market_id="wx-nyc-72")]
        with patch.object(wt, "get_client", MagicMock(return_value=client)):
            positions = wt.get_positions()
        client.get_positions.assert_called_once_with(venue=None)
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["market_id"], "wx-nyc-72")


if __name__ == "__main__":
    unittest.main()
