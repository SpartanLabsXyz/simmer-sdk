"""Offline v2 fetch contracts, with golden output captured from v1 normalization."""

import importlib
from io import BytesIO
import json
from pathlib import Path
import sys
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

FIXTURES = Path(__file__).parent / 'fixtures'
WALLET = '0x' + 'a' * 40


@pytest.fixture
def xray(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    with patch.object(sys, 'stdout'):
        module = importlib.import_module('wallet_xray')
    def no_network(*args, **kwargs):
        pytest.fail('Unexpected network request')
    monkeypatch.setattr(module, 'urlopen', no_network)
    return module


def test_v2_preserves_v1_analysis(xray, monkeypatch):
    sample = json.loads((FIXTURES / 'activity.json').read_text())
    expected = json.loads((FIXTURES / 'activity_expected.json').read_text())
    monkeypatch.setattr(xray, 'api_request', lambda url: sample)
    assert xray.get_wallet_trades(WALLET, 2) == expected['trades']
    analysis = xray.analyze_wallet(WALLET, 2)
    analysis.pop('analysis_timestamp')
    assert analysis == expected['analysis']


def test_cursor_pages_and_limit(xray, monkeypatch):
    rows = json.loads((FIXTURES / 'activity.json').read_text())['data']
    pages = iter([
        {'data': rows[:1], 'pagination': {'next_cursor': 'opaque+/='}},
        {'data': rows[1:] * 2, 'pagination': {'next_cursor': 'unused'}},
    ])
    urls = []
    def fetch(url):
        urls.append(urlsplit(url))
        return next(pages)
    monkeypatch.setattr(xray, 'api_request', fetch)
    trades = xray.get_wallet_trades(WALLET.upper(), 2)
    assert len(trades) == 2
    assert len(urls) == 2
    assert all(url.path == '/v2/activity' for url in urls)
    assert parse_qs(urls[0].query) == {'user': [WALLET], 'limit': ['2']}
    assert parse_qs(urls[1].query) == {'user': [WALLET], 'limit': ['1'], 'cursor': ['opaque+/=']}


def test_page_size_cap_and_null_cursor(xray, monkeypatch):
    urls = []
    def fetch(url):
        urls.append(url)
        return {'data': [], 'pagination': {'next_cursor': None}}
    monkeypatch.setattr(xray, 'api_request', fetch)
    assert xray.get_wallet_trades(WALLET, 2000) == []
    assert parse_qs(urlsplit(urls[0]).query)['limit'] == ['1000']
    assert len(urls) == 1


def test_repeated_cursor_is_error(xray, monkeypatch):
    row = json.loads((FIXTURES / 'activity.json').read_text())['data'][:1]
    monkeypatch.setattr(xray, 'api_request', lambda url: {
        'data': row, 'pagination': {'next_cursor': 'repeat'}})
    with pytest.raises(RuntimeError, match='[Rr]epeated cursor'):
        xray.get_wallet_trades(WALLET, 10)


@pytest.mark.parametrize('payload', [
    {}, [], {'data': None}, {'data': []},
    {'data': [], 'pagination': {}},
    {'data': [], 'pagination': {'next_cursor': 123}},
    {'data': [None], 'pagination': {'next_cursor': None}},
])
def test_malformed_activity_is_error(xray, monkeypatch, payload):
    monkeypatch.setattr(xray, 'api_request', lambda url: payload)
    with pytest.raises(RuntimeError, match='[Mm]alformed'):
        xray.analyze_wallet(WALLET)


@pytest.mark.parametrize('failure', [
    HTTPError('https://data-api.polymarket.com/v2/activity', 410, 'Gone', {}, None),
    URLError('offline'), TimeoutError('timed out'),
])
def test_cli_failed_read_exits_nonzero(xray, monkeypatch, capsys, failure):
    def fetch(*args, **kwargs):
        raise failure
    monkeypatch.setattr(xray, 'urlopen', fetch)
    monkeypatch.setattr(sys, 'argv', ['wallet_xray.py', WALLET, '--json'])
    with pytest.raises(SystemExit) as exc:
        xray.main()
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert '410' in output.err if isinstance(failure, HTTPError) else 'error' in output.err.lower()
    assert 'No trade history' not in output.err
    assert output.out == ''


def test_cli_invalid_json_exits_nonzero(xray, monkeypatch, capsys):
    monkeypatch.setattr(xray, 'urlopen', lambda *a, **kw: BytesIO(b'not json'))
    monkeypatch.setattr(sys, 'argv', ['wallet_xray.py', WALLET, '--json'])
    with pytest.raises(SystemExit) as exc:
        xray.main()
    assert exc.value.code == 1
    assert 'malformed' in capsys.readouterr().err.lower()


def test_second_page_failure_does_not_return_partial_analysis(xray, monkeypatch):
    sample = json.loads((FIXTURES / 'activity.json').read_text())
    pages = iter([sample, RuntimeError('API Error 410: Gone')])
    def fetch(url):
        page = next(pages)
        if isinstance(page, RuntimeError):
            raise page
        return page
    monkeypatch.setattr(xray, 'api_request', fetch)
    with pytest.raises(RuntimeError, match='410'):
        xray.analyze_wallet(WALLET, 3)


@pytest.mark.parametrize('compare', [False, True])
def test_cli_malformed_envelope_is_error(xray, monkeypatch, capsys, compare):
    monkeypatch.setattr(xray, 'api_request', lambda url: {})
    argv = ['wallet_xray.py', WALLET, '--json']
    if compare:
        argv.extend([WALLET, '--compare'])
    monkeypatch.setattr(sys, 'argv', argv)
    with pytest.raises(SystemExit) as exc:
        xray.main()
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert 'Malformed' in output.err
    assert 'No trade history' not in output.err
    assert output.out == ''


@pytest.mark.parametrize('field,value', [
    (field, value)
    for field in ('timestamp', 'size', 'usdc_size', 'price')
    for value in (None, True, '123', [], {}, float('nan'), float('inf'), -float('inf'))
] + [
    (field, value)
    for field in ('condition_id', 'type', 'side', 'transaction_hash', 'outcome')
    for value in (None, 123, [], {})
] + [('condition_id', '')])
def test_malformed_activity_values_raise(xray, monkeypatch, field, value):
    sample = json.loads((FIXTURES / 'activity.json').read_text())
    sample['data'][0][field] = value
    monkeypatch.setattr(xray, 'api_request', lambda url: sample)
    with pytest.raises(RuntimeError, match='[Mm]alformed'):
        xray.analyze_wallet(WALLET, 2)


@pytest.mark.parametrize('side', ['', 'HOLD', 'buygarbage'])
def test_invalid_trade_side_raises(xray, monkeypatch, side):
    sample = json.loads((FIXTURES / 'activity.json').read_text())
    sample['data'][0].update(type='TRADE', side=side)
    monkeypatch.setattr(xray, 'api_request', lambda url: sample)
    with pytest.raises(RuntimeError, match='^Malformed Polymarket activity API row$'):
        xray.get_wallet_trades(WALLET, 1)


def test_empty_page_with_cursor_raises_without_fetching_again(xray, monkeypatch):
    calls = []
    def fetch(url):
        calls.append(url)
        assert len(calls) == 1, 'Empty page must fail before another request'
        return {'data': [], 'pagination': {'next_cursor': 'next'}}
    monkeypatch.setattr(xray, 'api_request', fetch)
    with pytest.raises(RuntimeError, match='[Nn]o progress'):
        xray.get_wallet_trades(WALLET, 1)


@pytest.mark.parametrize('activity_type', ['REDEEM', 'MERGE', 'SPLIT'])
def test_non_trade_empty_side_is_valid(xray, monkeypatch, activity_type):
    sample = json.loads((FIXTURES / 'activity.json').read_text())
    row = sample['data'][0]
    row.update(type=activity_type, side='', timestamp=0)
    monkeypatch.setattr(xray, 'api_request', lambda url: sample)
    trade = xray.get_wallet_trades(WALLET, 1)[0]
    assert trade['activity_type'] == activity_type
    assert trade['created_at'] == '1970-01-01T00:00:00Z'
    assert trade['action'] == ('sell_yes' if activity_type == 'REDEEM' else '_yes')


def test_distinct_events_in_same_transaction_are_preserved(xray, monkeypatch):
    sample = json.loads((FIXTURES / 'activity.json').read_text())
    sample['data'][1]['transaction_hash'] = sample['data'][0]['transaction_hash']
    monkeypatch.setattr(xray, 'api_request', lambda url: sample)
    trades = xray.get_wallet_trades(WALLET, 2)
    assert len(trades) == 2
    assert trades[0]['transaction_hash'] == trades[1]['transaction_hash']
    assert trades[0]['shares'] != trades[1]['shares']


@pytest.mark.parametrize('activity_type', ['REWARD', 'YIELD', 'MAKER_REBATE', 'TAKER_REBATE'])
def test_non_market_rows_with_empty_condition_id_are_valid(xray, monkeypatch, activity_type):
    # Live /v2/activity returns these account-level rows with condition_id "".
    sample = json.loads((FIXTURES / 'activity.json').read_text())
    sample['data'][0].update(type=activity_type, side='', condition_id='')
    monkeypatch.setattr(xray, 'api_request', lambda url: sample)
    trade = xray.get_wallet_trades(WALLET, 1)[0]
    assert trade['activity_type'] == activity_type
    assert trade['market_id'] == ''


@pytest.mark.parametrize('activity_type', ['TRADE', 'SPLIT', 'MERGE', 'REDEEM', 'CONVERSION'])
def test_market_rows_require_condition_id(xray, monkeypatch, activity_type):
    sample = json.loads((FIXTURES / 'activity.json').read_text())
    sample['data'][0].update(type=activity_type, side='BUY', condition_id='')
    monkeypatch.setattr(xray, 'api_request', lambda url: sample)
    with pytest.raises(RuntimeError, match='^Malformed Polymarket activity API row$'):
        xray.get_wallet_trades(WALLET, 1)
