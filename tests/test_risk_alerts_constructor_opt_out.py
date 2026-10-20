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


@pytest.mark.parametrize("flags, expected_calls", [([], 0), (["--positions"], 0), (["--live"], 1)])
def test_copytrading_main_real_client(monkeypatch, flags, expected_calls):
    import importlib.util
    from pathlib import Path
    import sys

    monkeypatch.setenv("SIMMER_API_KEY", "sk_test_x")
    monkeypatch.setenv("WALLET_PRIVATE_KEY", KEY)
    monkeypatch.setenv("TRADING_VENUE", "polymarket")
    monkeypatch.setattr(sys.stdout, "reconfigure", lambda **kwargs: None, raising=False)
    response = Mock(status_code=200)
    response.json.return_value = {}
    monkeypatch.setattr("requests.sessions.Session.request", Mock(return_value=response))
    check = Mock()
    monkeypatch.setattr(SimmerClient, "_process_risk_alerts", check)
    path = Path(__file__).parents[1] / "skills/polymarket-copytrading/copytrading_trader.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("copytrading_real_risk_test", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    module._config["venue"] = "polymarket"
    assert module._client is None
    monkeypatch.setattr(module, "run_copytrading", Mock())
    monkeypatch.setattr(sys, "argv", ["trader"] + flags)
    module.main()
    assert isinstance(module._client, SimmerClient)
    assert check.call_count == expected_calls


@pytest.mark.parametrize("venue", ["sim", "polymarket"])
def test_copytrading_positions_uses_client_venue(monkeypatch, capsys, venue):
    import importlib.util
    from pathlib import Path
    import sys

    monkeypatch.setenv("SIMMER_API_KEY", "sk_test_x")
    monkeypatch.setenv("WALLET_PRIVATE_KEY", KEY)
    monkeypatch.setenv("TRADING_VENUE", "polymarket")
    monkeypatch.setattr(sys.stdout, "reconfigure", lambda **kwargs: None, raising=False)
    question = "Distinctive client venue position"
    response = Mock(status_code=200)
    response.json.return_value = {"positions": [{
        "venue": venue, "question": question, "shares_yes": 10,
        "shares_no": 0, "current_value": 6, "pnl": 1, "cost_basis": 5,
    }]}
    http = Mock(return_value=response)
    monkeypatch.setattr("requests.sessions.Session.request", http)
    check = Mock()
    monkeypatch.setattr(SimmerClient, "_process_risk_alerts", check)
    path = Path(__file__).parents[1] / "skills/polymarket-copytrading/copytrading_trader.py"
    monkeypatch.syspath_prepend(str(path.parent))
    spec = importlib.util.spec_from_file_location("copytrading_positions_venue_test", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", ["trader", "--positions", "--venue", venue])

    module.main()

    assert isinstance(module._client, SimmerClient)
    check.assert_not_called()
    position_urls = [call.kwargs["url"] for call in http.call_args_list
                     if "/api/sdk/positions" in call.kwargs.get("url", "")]
    assert len(position_urls) == 1
    assert f"venue={venue}" in position_urls[0]
    output = capsys.readouterr().out
    assert question in output
    assert "No polymarket positions found" not in output
    assert "No sim positions found" not in output
    assert f"Your {venue.title()} Positions" in output
