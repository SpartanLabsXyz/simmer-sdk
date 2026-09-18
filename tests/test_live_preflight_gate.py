"""Hard preflight control point on live real-money placements (#371).

Live + real venue + dry_run=False auto-runs client.preflight() and refuses
when ok_to_trade is False. Paper / sim / dry_run are unchanged. The
skip_preflight / SIMMER_SKIP_PREFLIGHT valve bypasses with a deprecation
warning for one release.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from simmer_sdk.client import SimmerClient, PreflightResult


def _ok_preflight(**overrides) -> PreflightResult:
    data = dict(
        client_preflight_id="pf-ok",
        agent_id="agent-1",
        tier="pro",
        resolved_venue="polymarket",
        execution_wallet="0xabc",
        deposit_wallet=None,
        signer_status="managed",
        spendable_balance=50.0,
        gas_balance=None,
        open_exposure_total=0.0,
        exposure_cap_usd=100.0,
        planned_amount=10.0,
        would_exceed_cap=False,
        pending_alerts=[],
        ok_to_trade=True,
        blockers=[],
        warnings=[],
    )
    data.update(overrides)
    return PreflightResult(**data)


def _blocked_preflight(blockers=None, **overrides) -> PreflightResult:
    blockers = list(blockers or ["WALLET_UNVERIFIED"])
    return _ok_preflight(ok_to_trade=False, blockers=blockers, **overrides)


def _live_client(venue: str = "polymarket") -> SimmerClient:
    client = SimmerClient.__new__(SimmerClient)
    client.live = True
    client.venue = venue
    client.base_url = "https://api.simmer.markets"
    client._readonly = False
    client._private_key = None
    client._ows_wallet = None
    client._wallet_address = None
    client._deposit_wallet_address = None
    client._uses_deposit_wallet = False
    client._solana_key_available = False
    client._held_markets_cache = None
    client._approvals_warned = False
    client.ORDER_TYPES = SimmerClient.ORDER_TYPES
    client.VENUES = SimmerClient.VENUES
    client._request = MagicMock(return_value={"success": True, "market_id": "m1", "side": "yes"})
    client._get_held_markets = MagicMock(return_value={})
    return client


def _paper_client(venue: str = "polymarket") -> SimmerClient:
    from simmer_sdk.paper import PaperPortfolio

    client = SimmerClient.__new__(SimmerClient)
    client.live = False
    client.venue = venue
    client._readonly = False
    client._paper_portfolio = PaperPortfolio(starting_balance=10_000.0)
    client.get_market_context = MagicMock(
        return_value={
            "market": {
                "question": "Will it rain?",
                "external_price_yes": 0.50,
                "current_probability": 0.50,
                "status": "active",
            }
        }
    )
    client.preflight = MagicMock(side_effect=AssertionError("preflight must not run on paper"))
    return client


# --- trade(): blocked / allowed ---------------------------------------------


def test_live_trade_refuses_when_preflight_blocked():
    client = _live_client()
    client.preflight = MagicMock(return_value=_blocked_preflight(["WALLET_UNVERIFIED", "EXPOSURE_CAP_EXCEEDED"]))

    result = client.trade("m1", "yes", amount=10.0, venue="polymarket")

    assert result.success is False
    assert result.skip_reason == "preflight_blocked"
    assert result.error_code == "preflight_blocked"
    assert "WALLET_UNVERIFIED" in result.error
    assert "EXPOSURE_CAP_EXCEEDED" in result.error
    client.preflight.assert_called_once_with(
        venue="polymarket", planned_amount=10.0, exposure_cap_usd=0.0,
    )
    client._request.assert_not_called()


def test_live_trade_allows_when_preflight_ok():
    client = _live_client()
    client.preflight = MagicMock(return_value=_ok_preflight())

    result = client.trade("m1", "yes", amount=10.0, venue="polymarket")

    assert result.success is True
    client.preflight.assert_called_once()
    client._request.assert_called_once()
    assert client._request.call_args.args[1] == "/api/sdk/trade"


def test_live_sell_passes_planned_amount_zero():
    """Sells do not add spend — cap math should see planned_amount=0."""
    client = _live_client()
    client.preflight = MagicMock(return_value=_ok_preflight())

    client.trade("m1", "yes", shares=5.0, action="sell", venue="polymarket")

    assert client.preflight.call_args.kwargs["planned_amount"] == 0.0


def test_live_sell_skips_exposure_cap_even_when_env_set(monkeypatch):
    """Sells must not be blocked by an already-over-cap book (CTO P1)."""
    monkeypatch.setenv("EXPOSURE_CAP_USD", "10")
    client = _live_client()
    client.preflight = MagicMock(return_value=_ok_preflight())

    client.trade("m1", "yes", shares=5.0, action="sell", venue="polymarket")

    assert client.preflight.call_args.kwargs["planned_amount"] == 0.0
    assert client.preflight.call_args.kwargs["exposure_cap_usd"] == 0.0


def test_live_buy_without_env_does_not_apply_default_cap(monkeypatch):
    """Auto-gate cap is opt-in — unset EXPOSURE_CAP_USD must not default to $100."""
    monkeypatch.delenv("EXPOSURE_CAP_USD", raising=False)
    client = _live_client()
    client.preflight = MagicMock(return_value=_ok_preflight())

    client.trade("m1", "yes", amount=10.0, venue="polymarket")

    assert client.preflight.call_args.kwargs["exposure_cap_usd"] == 0.0


def test_exposure_cap_env_zero_is_not_re_capped(monkeypatch):
    """Skills that disable via EXPOSURE_CAP_USD=0 must not be re-capped."""
    monkeypatch.setenv("EXPOSURE_CAP_USD", "0")
    client = _live_client()
    client.preflight = MagicMock(return_value=_ok_preflight())

    client.trade("m1", "yes", amount=10.0, venue="polymarket")

    assert client.preflight.call_args.kwargs["exposure_cap_usd"] == 0.0


# --- trade(): paper / sim / dry_run unchanged -------------------------------


def test_dry_run_live_real_venue_does_not_call_preflight():
    client = _live_client()
    client.preflight = MagicMock(side_effect=AssertionError("preflight must not run on dry_run"))

    result = client.trade("m1", "yes", amount=10.0, venue="polymarket", dry_run=True)

    assert result.success is True
    assert client._request.call_args.kwargs["json"]["dry_run"] is True


def test_sim_venue_live_does_not_call_preflight():
    client = _live_client(venue="sim")
    client.preflight = MagicMock(side_effect=AssertionError("preflight must not run on sim"))

    result = client.trade("m1", "yes", amount=10.0, venue="sim")

    assert result.success is True
    client._request.assert_called_once()


def test_paper_trade_does_not_call_preflight():
    client = _paper_client()

    result = client.trade("m1", "yes", amount=1.0, venue="polymarket")

    assert result.success is True
    assert result.simulated


# --- skip valve -------------------------------------------------------------


def test_skip_preflight_kwarg_bypasses_with_deprecation_warning():
    client = _live_client()
    client.preflight = MagicMock(return_value=_blocked_preflight())

    with pytest.warns(DeprecationWarning, match="migrate valve"):
        result = client.trade(
            "m1", "yes", amount=10.0, venue="polymarket", skip_preflight=True,
        )

    assert result.success is True
    client.preflight.assert_not_called()
    client._request.assert_called_once()


def test_skip_preflight_env_bypasses_with_deprecation_warning(monkeypatch):
    monkeypatch.setenv("SIMMER_SKIP_PREFLIGHT", "1")
    client = _live_client()
    client.preflight = MagicMock(return_value=_blocked_preflight())

    with pytest.warns(DeprecationWarning, match="migrate valve"):
        result = client.trade("m1", "yes", amount=10.0, venue="polymarket")

    assert result.success is True
    client.preflight.assert_not_called()


def test_exposure_cap_env_is_forwarded_to_preflight(monkeypatch):
    monkeypatch.setenv("EXPOSURE_CAP_USD", "250")
    client = _live_client()
    client.preflight = MagicMock(return_value=_ok_preflight())

    client.trade("m1", "yes", amount=10.0, venue="polymarket")

    assert client.preflight.call_args.kwargs["exposure_cap_usd"] == 250.0


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "+Infinity", "-Infinity", "1e309"])
def test_exposure_cap_env_buy_returns_failure_result(raw, monkeypatch):
    """Bad EXPOSURE_CAP_USD must not raise ValueError out of trade() on a buy."""
    monkeypatch.setenv("EXPOSURE_CAP_USD", raw)
    client = _live_client()
    client.preflight = MagicMock(return_value=_ok_preflight())

    result = client.trade("m1", "yes", amount=10.0, venue="polymarket")

    assert result.success is False
    assert result.skip_reason == "preflight_blocked"
    assert result.error_code == "preflight_blocked"
    assert result.error == "EXPOSURE_CAP_USD must be a finite number"
    client.preflight.assert_not_called()
    client._request.assert_not_called()


def test_sell_skips_bad_exposure_cap_parse(monkeypatch):
    """Sells skip cap parsing — a bad EXPOSURE_CAP_USD must not raise or block."""
    monkeypatch.setenv("EXPOSURE_CAP_USD", "NaN")
    client = _live_client()
    client.preflight = MagicMock(return_value=_ok_preflight())

    result = client.trade("m1", "yes", shares=5.0, action="sell", venue="polymarket")

    assert result.success is True
    client.preflight.assert_called_once()
    assert client.preflight.call_args.kwargs["exposure_cap_usd"] == 0.0
    client._request.assert_called_once()


def test_place_combo_sell_skips_exposure_cap(monkeypatch):
    monkeypatch.setenv("EXPOSURE_CAP_USD", "10")
    client = _combo_client()
    client.preflight = MagicMock(return_value=_ok_preflight())
    client._combo_dw_approved = MagicMock(return_value=True)

    with patch("simmer_sdk.combo.place_combo", return_value={"status": "ok"}) as mock_place:
        with patch("py_clob_client.client.ClobClient"):
            result = client.place_combo(
                leg_position_ids=["111", "222"],
                size_usdc=5.0,
                direction="SELL",
                dry_run=False,
            )

    assert result == {"status": "ok"}
    client.preflight.assert_called_once()
    assert client.preflight.call_args.kwargs["planned_amount"] == 0.0
    assert client.preflight.call_args.kwargs["exposure_cap_usd"] == 0.0
    mock_place.assert_called_once()


# --- preflight() helpers: gas + finiteness + null venue ---------------------


def test_pol_substring_in_alert_message_is_not_insufficient_gas():
    assert SimmerClient._alert_signals_insufficient_gas(
        {"message": "Review the political risk policy before trading"}
    ) is False
    assert SimmerClient._alert_signals_insufficient_gas(
        {"message": "low pol on the wallet"}
    ) is False
    assert SimmerClient._alert_signals_insufficient_gas(
        {"message": "insufficient gas on polygon"}
    ) is False


def test_structured_insufficient_gas_code_matches():
    assert SimmerClient._alert_signals_insufficient_gas(
        {"code": "INSUFFICIENT_GAS", "message": "political headline"}
    ) is True
    assert SimmerClient._alert_signals_insufficient_gas("INSUFFICIENT_GAS") is True
    assert SimmerClient._alert_signals_insufficient_gas(
        {"type": "insufficient_gas"}
    ) is True


def test_preflight_rejects_non_finite_exposure_cap_arg():
    client = _live_client()
    client._request = MagicMock(side_effect=AssertionError("must reject before reads"))
    with pytest.raises(ValueError, match="finite number"):
        client.preflight(venue="polymarket", exposure_cap_usd=float("nan"))
    with pytest.raises(ValueError, match="finite number"):
        client.preflight(venue="polymarket", exposure_cap_usd=float("inf"))


def test_replay_env_returns_ok_for_real_venue_without_network(monkeypatch):
    client = _live_client()
    client.base_url = "http://127.0.0.1:43117"
    client._wallet_address = "0xabc"
    client._request = MagicMock(
        side_effect=AssertionError("replay preflight should not call network")
    )

    monkeypatch.setenv("SIMMER_REPLAY", "1")

    result = client.preflight(
        venue="polymarket", planned_amount=5.0, exposure_cap_usd=100.0,
    )

    assert result.ok_to_trade is True
    assert result.blockers == []
    assert result.resolved_venue == "polymarket"
    assert result.signer_status == "replay"
    assert "replay_preflight_ok" in result.warnings
    client._request.assert_not_called()


def test_replay_env_does_not_bypass_preflight_for_non_loopback(monkeypatch):
    client = _live_client()
    client.base_url = "https://api.simmer.markets"
    client._wallet_address = "0xabc"

    def _request(method, endpoint, **kwargs):
        if "/agents/me" in endpoint:
            return {
                "agent_id": "a1",
                "rate_limits": {"tier": "pro"},
                "real_trading_enabled": False,
                "wallet_address": "0xabc",
            }
        if "/briefing" in endpoint:
            return {"risk_alerts": [], "venues": {"polymarket": {"balance": 50}}}
        if "/positions" in endpoint:
            return {"positions": []}
        raise AssertionError(f"unexpected {endpoint}")

    client._request = MagicMock(side_effect=_request)
    monkeypatch.setenv("SIMMER_REPLAY", "1")

    result = client.preflight(
        venue="polymarket", planned_amount=5.0, exposure_cap_usd=100.0,
    )

    assert result.ok_to_trade is False
    assert "WALLET_UNVERIFIED" in result.blockers
    assert result.signer_status == "managed"
    assert "replay_preflight_ok" not in result.warnings
    assert client._request.call_count == 3


def test_preflight_null_venue_position_counts_as_sim():
    """venue=None is virtual $SIM, not real USDC exposure (keep MCP in sync)."""
    client = _live_client()
    client._ows_wallet = None
    client._deposit_wallet_address = None
    client._uses_deposit_wallet = False
    client._solana_key_available = False
    client._wallet_address = "0xabc"

    def _request(method, endpoint, **kwargs):
        if "/agents/me" in endpoint:
            return {
                "agent_id": "a1",
                "rate_limits": {"tier": "pro"},
                "real_trading_enabled": True,
                "wallet_address": "0xabc",
            }
        if "/briefing" in endpoint:
            return {"risk_alerts": [], "venues": {"polymarket": {"balance": 50}}}
        if "/positions" in endpoint:
            return {
                "positions": [
                    {"venue": None, "current_value": 80.0},
                    {"venue": "polymarket", "current_value": 15.0},
                ]
            }
        raise AssertionError(f"unexpected {endpoint}")

    client._request = _request
    result = client.preflight(
        venue="polymarket", planned_amount=0.0, exposure_cap_usd=100.0,
    )
    assert result.open_exposure_total == 15.0
    assert result.ok_to_trade is True


def test_preflight_non_numeric_current_value_is_exposure_unknown():
    """Parse failures must become EXPOSURE_UNKNOWN, not an uncaught raise."""
    client = _live_client()
    client._ows_wallet = None
    client._deposit_wallet_address = None
    client._uses_deposit_wallet = False
    client._solana_key_available = False
    client._wallet_address = "0xabc"

    def _request(method, endpoint, **kwargs):
        if "/agents/me" in endpoint:
            return {
                "agent_id": "a1",
                "rate_limits": {"tier": "pro"},
                "real_trading_enabled": True,
                "wallet_address": "0xabc",
            }
        if "/briefing" in endpoint:
            return {"risk_alerts": [], "venues": {"polymarket": {"balance": 50}}}
        if "/positions" in endpoint:
            return {
                "positions": [
                    {"venue": "polymarket", "current_value": "unknown"},
                ]
            }
        if "/allowances/" in endpoint:
            return {"all_set": True}
        raise AssertionError(f"unexpected {endpoint}")

    client._request = _request
    result = client.preflight(
        venue="polymarket", planned_amount=5.0, exposure_cap_usd=100.0,
    )
    assert "EXPOSURE_UNKNOWN" in result.blockers
    assert result.ok_to_trade is False
    assert any("positions_fetch_failed" in w for w in result.warnings)


def test_live_trade_unknown_current_value_does_not_crash(monkeypatch):
    """trade() must return a failure result, not raise, when current_value is junk."""
    monkeypatch.setenv("EXPOSURE_CAP_USD", "100")
    client = _live_client()
    client._ows_wallet = None
    client._deposit_wallet_address = None
    client._uses_deposit_wallet = False
    client._solana_key_available = False
    client._wallet_address = "0xabc"

    def _request(method, endpoint, **kwargs):
        if "/agents/me" in endpoint:
            return {
                "agent_id": "a1",
                "rate_limits": {"tier": "pro"},
                "real_trading_enabled": True,
                "wallet_address": "0xabc",
            }
        if "/briefing" in endpoint:
            return {"risk_alerts": [], "venues": {"polymarket": {"balance": 50}}}
        if "/positions" in endpoint:
            return {
                "positions": [
                    {"venue": "polymarket", "current_value": "unknown"},
                ]
            }
        if "/allowances/" in endpoint:
            return {"all_set": True}
        if endpoint == "/api/sdk/trade":
            raise AssertionError("trade must not POST when exposure is unknown")
        raise AssertionError(f"unexpected {endpoint}")

    client._request = _request
    result = client.trade("m1", "yes", amount=10.0, venue="polymarket")
    assert result.success is False
    assert result.skip_reason == "preflight_blocked"
    assert "EXPOSURE_UNKNOWN" in result.error


def test_live_sell_unknown_current_value_does_not_crash(monkeypatch):
    """Sells skip the cap, so a junk current_value is a warning — not a crash."""
    monkeypatch.setenv("EXPOSURE_CAP_USD", "100")
    client = _live_client()
    client._ows_wallet = None
    client._deposit_wallet_address = None
    client._uses_deposit_wallet = False
    client._solana_key_available = False
    client._wallet_address = "0xabc"
    posted = []

    def _request(method, endpoint, **kwargs):
        if "/agents/me" in endpoint:
            return {
                "agent_id": "a1",
                "rate_limits": {"tier": "pro"},
                "real_trading_enabled": True,
                "wallet_address": "0xabc",
            }
        if "/briefing" in endpoint:
            return {"risk_alerts": [], "venues": {"polymarket": {"balance": 50}}}
        if "/positions" in endpoint:
            return {
                "positions": [
                    {"venue": "polymarket", "current_value": "unknown"},
                ]
            }
        if "/allowances/" in endpoint:
            return {"all_set": True}
        if endpoint == "/api/sdk/trade":
            posted.append(kwargs.get("json") or {})
            return {"success": True, "market_id": "m1", "side": "yes"}
        raise AssertionError(f"unexpected {endpoint}")

    client._request = _request
    result = client.trade("m1", "yes", shares=5.0, action="sell", venue="polymarket")
    assert result.success is True
    assert len(posted) == 1


def test_preflight_reads_are_serial_with_timeout_5():
    """Identity / briefing / positions (and approvals) are serial 5s reads. No threads."""
    import inspect

    from simmer_sdk import client as client_mod

    module_src = inspect.getsource(client_mod)
    assert "ThreadPoolExecutor" not in module_src
    assert "_preflight_parallel_reads" not in module_src
    assert "concurrent.futures" not in module_src

    client = _live_client()
    client._ows_wallet = "agent-wallet"
    client._private_key = None
    client._deposit_wallet_address = None
    client._uses_deposit_wallet = False
    client._solana_key_available = False
    client._wallet_address = "0xabc"
    timeouts = []

    def _request(method, endpoint, **kwargs):
        timeouts.append((endpoint, kwargs.get("timeout")))
        if "/agents/me" in endpoint:
            return {
                "agent_id": "a1",
                "rate_limits": {"tier": "pro"},
                "real_trading_enabled": True,
                "wallet_address": "0xabc",
                "per_agent_wallet_address": "0xabc",
            }
        if "/briefing" in endpoint:
            return {"risk_alerts": [], "venues": {"polymarket": {"balance": 50}}}
        if "/positions" in endpoint:
            return {"positions": []}
        if "/allowances/" in endpoint:
            return {"all_set": True}
        raise AssertionError(f"unexpected {endpoint}")

    client._request = _request
    result = client.preflight(venue="polymarket", exposure_cap_usd=0)
    assert result.ok_to_trade is True
    assert len(timeouts) >= 3
    by_ep = {ep: timeout for ep, timeout in timeouts}
    assert by_ep["/api/sdk/agents/me"] == SimmerClient._PREFLIGHT_READ_TIMEOUT_S
    assert by_ep["/api/sdk/briefing"] == SimmerClient._PREFLIGHT_READ_TIMEOUT_S
    assert by_ep["/api/sdk/positions"] == SimmerClient._PREFLIGHT_READ_TIMEOUT_S
    allowances = [t for ep, t in timeouts if "/allowances/" in ep]
    assert allowances == [SimmerClient._PREFLIGHT_READ_TIMEOUT_S]
    assert SimmerClient._PREFLIGHT_READ_TIMEOUT_S == 5


def test_preflight_free_text_pol_does_not_block_gas():
    client = _live_client()
    client._ows_wallet = None
    client._deposit_wallet_address = None
    client._uses_deposit_wallet = False
    client._solana_key_available = False
    client._wallet_address = "0xabc"

    def _request(method, endpoint, **kwargs):
        if "/agents/me" in endpoint:
            return {
                "agent_id": "a1",
                "rate_limits": {"tier": "pro"},
                "real_trading_enabled": True,
                "wallet_address": "0xabc",
            }
        if "/briefing" in endpoint:
            return {
                "risk_alerts": [{"message": "political / policy watch: vegas event"}],
                "venues": {"polymarket": {"balance": 50}},
            }
        if "/positions" in endpoint:
            return {"positions": []}
        if "/allowances/" in endpoint:
            return {"all_set": True}
        raise AssertionError(f"unexpected {endpoint}")

    client._request = _request
    result = client.preflight(venue="polymarket", exposure_cap_usd=0)
    assert "INSUFFICIENT_GAS" not in result.blockers
    assert result.ok_to_trade is True


def test_preflight_bare_string_insufficient_gas_blocks():
    """Bare-string alerts must still match after message/code normalization."""
    client = _live_client()
    client._ows_wallet = None
    client._deposit_wallet_address = None
    client._uses_deposit_wallet = False
    client._solana_key_available = False
    client._wallet_address = "0xabc"

    def _request(method, endpoint, **kwargs):
        if "/agents/me" in endpoint:
            return {
                "agent_id": "a1",
                "rate_limits": {"tier": "pro"},
                "real_trading_enabled": True,
                "wallet_address": "0xabc",
            }
        if "/briefing" in endpoint:
            return {
                "risk_alerts": ["INSUFFICIENT_GAS"],
                "venues": {"polymarket": {"balance": 50}},
            }
        if "/positions" in endpoint:
            return {"positions": []}
        if "/allowances/" in endpoint:
            return {"all_set": True}
        raise AssertionError(f"unexpected {endpoint}")

    client._request = _request
    result = client.preflight(venue="polymarket", exposure_cap_usd=0)
    assert "INSUFFICIENT_GAS" in result.blockers
    assert result.ok_to_trade is False
    assert result.pending_alerts[0]["code"] == "INSUFFICIENT_GAS"
    assert result.pending_alerts[0]["message"] == "INSUFFICIENT_GAS"


def test_preflight_structured_gas_code_blocks():
    client = _live_client()
    client._ows_wallet = None
    client._deposit_wallet_address = None
    client._uses_deposit_wallet = False
    client._solana_key_available = False
    client._wallet_address = "0xabc"

    def _request(method, endpoint, **kwargs):
        if "/agents/me" in endpoint:
            return {
                "agent_id": "a1",
                "rate_limits": {"tier": "pro"},
                "real_trading_enabled": True,
                "wallet_address": "0xabc",
            }
        if "/briefing" in endpoint:
            return {
                "risk_alerts": [{"code": "INSUFFICIENT_GAS", "message": "fund wallet POL"}],
                "venues": {"polymarket": {"balance": 50}},
            }
        if "/positions" in endpoint:
            return {"positions": []}
        if "/allowances/" in endpoint:
            return {"all_set": True}
        raise AssertionError(f"unexpected {endpoint}")

    client._request = _request
    result = client.preflight(venue="polymarket", exposure_cap_usd=0)
    assert "INSUFFICIENT_GAS" in result.blockers
    assert result.ok_to_trade is False


# --- place_combo ------------------------------------------------------------


def _combo_client() -> SimmerClient:
    client = SimmerClient.__new__(SimmerClient)
    client.live = True
    client.venue = "polymarket"
    client._readonly = False
    client._private_key = "0x" + "ab" * 32
    client._ows_wallet = None
    client._wallet_address = "0xEOA"
    client._uses_deposit_wallet = False
    client._deposit_wallet_address = None
    client._load_per_agent_dw_state = MagicMock()
    return client


def test_place_combo_live_refuses_when_preflight_blocked():
    client = _combo_client()
    client.preflight = MagicMock(return_value=_blocked_preflight(["EXPOSURE_CAP_EXCEEDED"]))

    with pytest.raises(RuntimeError, match="ok_to_trade=False"):
        client.place_combo(
            leg_position_ids=["111", "222"], size_usdc=5.0, dry_run=False,
        )

    client.preflight.assert_called_once()
    assert client.preflight.call_args.kwargs["planned_amount"] == 5.0
    assert client.preflight.call_args.kwargs["venue"] == "polymarket"


def test_place_combo_dry_run_does_not_call_preflight():
    client = _combo_client()
    client.preflight = MagicMock(side_effect=AssertionError("preflight must not run on combo dry_run"))

    with patch("simmer_sdk.combo.place_combo", return_value={"status": "dry_run"}) as mock_place:
        plan = client.place_combo(
            leg_position_ids=["111", "222"], size_usdc=5.0, dry_run=True,
        )

    assert plan == {"status": "dry_run"}
    mock_place.assert_called_once()


def test_place_combo_skip_preflight_bypasses_with_deprecation_warning():
    client = _combo_client()
    client.preflight = MagicMock(return_value=_blocked_preflight())
    client._combo_dw_approved = MagicMock(return_value=True)

    with patch("simmer_sdk.combo.place_combo", return_value={"status": "ok"}) as mock_place:
        with patch("py_clob_client.client.ClobClient"):
            with pytest.warns(DeprecationWarning, match="migrate valve"):
                result = client.place_combo(
                    leg_position_ids=["111", "222"],
                    size_usdc=5.0,
                    dry_run=False,
                    skip_preflight=True,
                )

    assert result == {"status": "ok"}
    client.preflight.assert_not_called()
    mock_place.assert_called_once()
