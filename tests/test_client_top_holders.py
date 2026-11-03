"""Public Data API reads preserve holder output and distinguish failed reads."""

from io import BytesIO
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from simmer_sdk.client import SimmerClient

FIXTURES = Path(__file__).parent / 'fixtures' / 'polymarket_data_v2'


def test_v2_holders_preserve_v1_output(monkeypatch):
    payload = (FIXTURES / 'holders.json').read_bytes()
    requests = []

    def fetch(req, **kwargs):
        requests.append(req.full_url)
        return BytesIO(payload)

    monkeypatch.setattr('urllib.request.urlopen', fetch)
    result = SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition', limit=4)
    assert result == json.loads((FIXTURES / 'holders_expected.json').read_text())
    assert all(type(row['amount']) is float for row in result)
    url = urlsplit(requests[0])
    assert url.path == '/v2/holders'
    assert parse_qs(url.query) == {'condition': ['0xcondition'], 'limit': ['4']}


@pytest.mark.parametrize('failure', [
    HTTPError('https://data-api.polymarket.com/v2/holders', 410, 'Gone', {}, None),
    URLError('offline'), TimeoutError('timed out'),
])
def test_failed_read_raises(monkeypatch, failure):
    def fetch(*args, **kwargs):
        raise failure

    monkeypatch.setattr('urllib.request.urlopen', fetch)
    with pytest.raises(RuntimeError, match='410|offline|timed out'):
        SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition')


@pytest.mark.parametrize('payload', [
    b'{}', b'[]', b'{"data": null}', b'{"data": {}}', b'not json',
    b'{"data": [{}]}', b'{"data": [{"holders": null}]}',
    b'{"data": []}', b'{"data": [], "pagination": {}}',
    b'{"data": [], "pagination": null}',
])
def test_malformed_holders_raise(monkeypatch, payload):
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: BytesIO(payload))
    with pytest.raises(RuntimeError, match='[Mm]alformed'):
        SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition')


def test_empty_holders(monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: BytesIO(
        b'{"data": [], "pagination": {"next_cursor": null}}'))
    assert SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition') == []


@pytest.mark.parametrize('cursor', [123, '', False, [], {}])
def test_invalid_holder_cursor_raises(monkeypatch, cursor):
    sample = json.loads((FIXTURES / 'holders.json').read_text())
    sample['pagination']['next_cursor'] = cursor
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: BytesIO(json.dumps(sample).encode()))
    with pytest.raises(RuntimeError, match='Malformed Polymarket holders API response'):
        SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition')


def test_live_shape_with_next_cursor_returns_first_page_top_holders(monkeypatch):
    # The captured live response advertises a next page (/v2/holders always does).
    sample = json.loads((FIXTURES / 'holders.json').read_text())
    assert isinstance(sample['pagination']['next_cursor'], str) and sample['pagination']['next_cursor']
    requests = []

    def fetch(req, **kwargs):
        requests.append(req.full_url)
        return BytesIO(json.dumps(sample).encode())

    monkeypatch.setattr('urllib.request.urlopen', fetch)
    holders = SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition', limit=3)
    assert len(requests) == 1
    assert len(holders) == 3
    assert [h['amount'] for h in holders] == sorted((h['amount'] for h in holders), reverse=True)


def test_empty_page_with_next_cursor_raises(monkeypatch):
    sample = json.loads((FIXTURES / 'holders.json').read_text())
    sample['pagination']['next_cursor'] = 'next'
    sample['data'] = []
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: BytesIO(json.dumps(sample).encode()))
    with pytest.raises(RuntimeError, match='Malformed Polymarket holders API response'):
        SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition')


def test_unsorted_holder_group_raises(monkeypatch):
    sample = json.loads((FIXTURES / 'holders.json').read_text())
    group = sample['data'][0]['holders']
    group[0]['amount'], group[-1]['amount'] = group[-1]['amount'], group[0]['amount']
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: BytesIO(json.dumps(sample).encode()))
    with pytest.raises(RuntimeError, match='Malformed Polymarket holders API response'):
        SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition')


def test_holder_name_fallbacks_and_unknown_outcome(monkeypatch):
    rows = [
        {'proxy_wallet': '0x123456789abcdef', 'pseudonym': 'alias', 'amount': 4, 'outcome_index': 0},
        {'proxy_wallet': '0x123456789abcdef', 'amount': 3, 'outcome_index': 1},
        {'proxy_wallet': '0xabcdef123456789', 'amount': 2, 'outcome_index': 999},
    ]
    payload = json.dumps({'data': [{'token_id': 'token', 'holders': rows}],
                          'pagination': {'next_cursor': None}}).encode()
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: BytesIO(payload))
    result = SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition')
    assert [h['display_name'] for h in result] == ['alias', '0x12345678...', '0xabcdef12...']
    assert [h['outcome'] for h in result] == ['Yes', 'No', 'Unknown']
    assert result[-1]['profile_url'] == 'https://polymarket.com/profile/0xabcdef123456789'
    assert all(isinstance(h[key], str) for h in result
               for key in ('address', 'display_name', 'outcome', 'profile_url'))
    assert all(set(h) == {'address', 'display_name', 'amount', 'outcome', 'profile_url'} for h in result)


@pytest.mark.parametrize('field,value', [
    ('proxy_wallet', value) for value in (None, '', 123, [], {})
] + [
    ('amount', value) for value in (None, True, '12.5', 'bad', [], {}, float('nan'), float('inf'), -float('inf'))
] + [
    ('outcome_index', value) for value in (None, True, '0', 0.0, [], {})
] + [('name', 123), ('pseudonym', 123)])
def test_malformed_holder_values_raise(monkeypatch, field, value):
    sample = json.loads((FIXTURES / 'holders.json').read_text())
    row = sample['data'][0]['holders'][0]
    row['name'] = ''
    row[field] = value
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: BytesIO(json.dumps(sample).encode()))
    with pytest.raises(RuntimeError, match='[Mm]alformed'):
        SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition')


def test_continuation_page_with_only_empty_groups_raises(monkeypatch):
    sample = json.loads((FIXTURES / 'holders.json').read_text())
    assert sample['pagination']['next_cursor']
    for group in sample['data']:
        group['holders'] = []
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: BytesIO(json.dumps(sample).encode()))
    with pytest.raises(RuntimeError, match='Malformed Polymarket holders API response'):
        SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition')


def test_terminal_page_with_empty_groups_is_empty(monkeypatch):
    sample = json.loads((FIXTURES / 'holders.json').read_text())
    sample['pagination']['next_cursor'] = None
    for group in sample['data']:
        group['holders'] = []
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: BytesIO(json.dumps(sample).encode()))
    assert SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition') == []
