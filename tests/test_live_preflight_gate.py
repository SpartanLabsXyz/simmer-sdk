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
    client._readonly = False
    client._private_key = None
    client._ows_wallet = None
    client._wallet_address = None
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
        venue="polymarket", planned_amount=10.0, exposure_cap_usd=100.0,
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
