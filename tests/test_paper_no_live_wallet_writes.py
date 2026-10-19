"""Paper clients must never reach real wallet writes."""
import inspect
from unittest.mock import Mock

import pytest

from simmer_sdk import SimmerClient
from simmer_sdk.client import TradeResult


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(SimmerClient, '_request', Mock(return_value={}))
    monkeypatch.setattr(SimmerClient, '_validate_and_set_wallet', lambda self, key: setattr(self, '_private_key', key))
    monkeypatch.setattr('requests.get', Mock(side_effect=AssertionError('unexpected HTTP')))
    http = Mock(side_effect=AssertionError('unexpected HTTP'))
    monkeypatch.setattr('requests.sessions.Session.request', http)
    monkeypatch.delenv('OWS_WALLET', raising=False)
    monkeypatch.delenv('WALLET_PRIVATE_KEY', raising=False)
    monkeypatch.delenv('SIMMER_PRIVATE_KEY', raising=False)
    return http


@pytest.mark.parametrize('method,args', [
    ('cancel_order', ('order',)), ('cancel_market_orders', ('market',)),
    ('cancel_all_orders', ()), ('_cancel_orders_for_token', ('token',)),
    ('redeem', ('market', 'yes')), ('auto_redeem', ()), ('set_approvals', ()),
    ('activate_polymarket_dw', ()), ('activate_combo_dw', ()),
    ('prepare_real_trade', ('market', 'yes', 1)), ('wrap_on_dw', ()),
    ('_process_risk_alerts', ()),
])
def test_paper_wallet_write_refused(method, args, offline):
    client = SimmerClient(api_key='sk_test_...', venue='polymarket', live=False, private_key='0x' + '11' * 32)
    client._request.reset_mock()
    client._get_clob_client = Mock(side_effect=AssertionError('signing reached'))
    with pytest.raises(RuntimeError, match='paper mode.*live=False'):
        getattr(client, method)(*args)
    client._request.assert_not_called()
    client._get_clob_client.assert_not_called()
    offline.assert_not_called()


def test_paper_briefing_skips_risk():
    client = SimmerClient(api_key='sk_test_...', venue='polymarket', live=False, private_key='0x' + '11' * 32)
    briefing = {'triggered_risk_alerts': [{'market_id': 'market'}]}
    client._request = Mock(return_value=briefing)
    client._process_risk_alerts = Mock()
    client.trade = Mock()
    client.delete_monitor = Mock()
    client._cancel_orders_for_token = Mock()
    assert client.get_briefing() == briefing
    client._process_risk_alerts.assert_not_called()
    client.trade.assert_not_called()
    client.delete_monitor.assert_not_called()
    client._cancel_orders_for_token.assert_not_called()
    assert [call.args[0] for call in client._request.call_args_list] == ['GET']


@pytest.mark.parametrize('venue', ['polymarket', 'kalshi', 'hyperliquid'])
def test_real_venue_requires_explicit_live(venue):
    with pytest.raises(ValueError, match='live=True.*live=False'):
        SimmerClient(api_key='sk_test_...', venue=venue)
    for live in (True, False):
        assert SimmerClient(api_key='sk_test_...', venue=venue, live=live).live is live


def test_implicit_sim_cannot_write_real_venue():
    client = SimmerClient(api_key='sk_test_...')
    assert client.live is True
    client._request.reset_mock()
    with pytest.raises(ValueError, match='live=True.*live=False'):
        client.trade('market', 'yes', 1, venue='polymarket')
    client.venue = 'polymarket'
    with pytest.raises(ValueError, match='live=True.*live=False'):
        client.cancel_all_orders()
    client._request.assert_not_called()


@pytest.mark.parametrize('success,filled', [(False, 0), (True, 0), (True, 2.5)])
def test_risk_exit_requires_fill(success, filled, capsys):
    client = SimmerClient(api_key='sk_test_...', venue='polymarket', live=True)
    client._risk_exit_fresh_shares = Mock(return_value=10)
    client.trade = Mock(return_value=TradeResult(success=success, market_id='market', side='yes', shares_sold=filled, error='unfilled'))
    client.delete_monitor = Mock()
    client._request = Mock()
    client._process_risk_alerts([{'market_id': 'market', 'side': 'yes', 'shares': 10, 'exit_reason': 'stop'}])
    output = capsys.readouterr().out
    if success and filled:
        client.delete_monitor.assert_called_once_with('market', 'yes')
        client._request.assert_called_once_with('DELETE', '/api/sdk/risk-alerts/market/yes')
        assert 'sold 2.50 shares' in output
    else:
        client.delete_monitor.assert_not_called()
        client._request.assert_not_called()
        assert 'Risk exit failed' in output
        assert 'sold' not in output
        assert 'Risk exit executed' not in output


def test_no_per_trade_live():
    assert 'live' not in inspect.signature(SimmerClient.trade).parameters


@pytest.mark.parametrize("factory", ["from_env", "readonly", "with_ows_wallet"])
@pytest.mark.parametrize("live", [True, False])
def test_factory_passes_live_through(factory, live, monkeypatch):
    monkeypatch.setenv("SIMMER_API_KEY", "sk_test_...")
    monkeypatch.setattr("simmer_sdk.ows_utils.get_ows_wallet_address", lambda name: "0x" + "11" * 20)
    args = ("test-wallet",) if factory == "with_ows_wallet" else ()
    client = getattr(SimmerClient, factory)(*args, venue="polymarket", live=live)
    assert client.live is live
    assert client._live_explicit is True


@pytest.mark.parametrize("venue", ["sim", "simmer", "sandbox"])
def test_sim_alias_default_is_live(venue):
    assert SimmerClient(api_key="sk_test_...", venue=venue).live is True


def test_readonly_real_venue_needs_no_live_and_still_refuses_writes(monkeypatch):
    monkeypatch.setenv("SIMMER_API_KEY", "sk_test_...")
    client = SimmerClient.readonly(venue="polymarket")
    assert client.live is True
    client._request.reset_mock()
    with pytest.raises(RuntimeError, match="readonly"):
        client.cancel_all_orders()
    client._request.assert_not_called()
