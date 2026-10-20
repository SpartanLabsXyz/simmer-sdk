"""Constructor risk exits remain opt-out and never run during opted-out probes."""
from unittest.mock import Mock

import pytest
from simmer_sdk.client import SimmerClient

KEY = "0x" + "1" * 64


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in ("WALLET_PRIVATE_KEY", "SIMMER_PRIVATE_KEY", "OWS_WALLET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("requests.sessions.Session.request", Mock(side_effect=AssertionError("Unexpected HTTP")))
    monkeypatch.setattr("simmer_sdk.version_check.check_server_version_compatibility", lambda *a, **k: None)


def test_default_keeps_risk_exits(monkeypatch):
    check = Mock()
    monkeypatch.setattr(SimmerClient, "_process_risk_alerts", check)
    SimmerClient(api_key="sk_test_...", venue="polymarket", live=True, private_key=KEY)
    check.assert_called_once_with()


def test_explicit_opt_out(monkeypatch):
    check = Mock()
    monkeypatch.setattr(SimmerClient, "_process_risk_alerts", check)
    SimmerClient(api_key="sk_test_...", venue="polymarket", live=True, private_key=KEY, process_risk_alerts=False)
    check.assert_not_called()


def test_env_wallet_opt_out_never_fetches_or_trades(monkeypatch):
    monkeypatch.setenv("WALLET_PRIVATE_KEY", KEY)
    response = Mock()
    response.status_code = 200
    response.json.return_value = {"risk_alerts": [{"market_id": "m1", "side": "yes", "shares": 10, "exit_reason": "stop_loss", "venue": "polymarket"}]}
    http = Mock(return_value=response)
    trade = Mock()
    monkeypatch.setattr("requests.sessions.Session.request", http)
    monkeypatch.setattr(SimmerClient, "trade", trade)
    client = SimmerClient(api_key="sk_test_...", venue="polymarket", live=True, process_risk_alerts=False)
    assert client._private_key == KEY
    assert not any("risk-alerts" in str(call) for call in http.call_args_list)
    trade.assert_not_called()


def test_readonly_still_skips(monkeypatch):
    check = Mock()
    monkeypatch.setattr(SimmerClient, "_process_risk_alerts", check)
    SimmerClient.readonly(api_key="sk_test_...", venue="polymarket", private_key=KEY)
    check.assert_not_called()
