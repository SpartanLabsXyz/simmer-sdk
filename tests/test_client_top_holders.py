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
])
def test_malformed_holders_raise(monkeypatch, payload):
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: BytesIO(payload))
    with pytest.raises(RuntimeError, match='[Mm]alformed'):
        SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition')


def test_empty_holders(monkeypatch):
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: BytesIO(
        b'{"data": [], "pagination": {"next_cursor": null}}'))
    assert SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition') == []


def test_holder_name_fallbacks_and_unknown_outcome(monkeypatch):
    rows = [
        {'proxy_wallet': '0x123456789abcdef', 'pseudonym': 'alias', 'amount': 4, 'outcome_index': 0},
        {'proxy_wallet': '0x123456789abcdef', 'amount': 3, 'outcome_index': 1},
        {'proxy_wallet': '', 'amount': 2, 'outcome_index': 999},
    ]
    payload = json.dumps({'data': [{'token_id': 'token', 'holders': rows}]}).encode()
    monkeypatch.setattr('urllib.request.urlopen', lambda *a, **kw: BytesIO(payload))
    result = SimmerClient.__new__(SimmerClient).get_top_holders('0xcondition')
    assert [h['display_name'] for h in result] == ['alias', '0x12345678...', 'unknown']
    assert [h['outcome'] for h in result] == ['Yes', 'No', 'Unknown']
    assert result[-1]['profile_url'] is None
    assert all(set(h) == {'address', 'display_name', 'amount', 'outcome', 'profile_url'} for h in result)
