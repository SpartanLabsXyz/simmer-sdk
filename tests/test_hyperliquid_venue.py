"""Unit tests for HyperliquidVenue adapter (transport mocked — no network)."""

import json

import pytest

pytest.importorskip("hyperliquid", reason="requires the [hyperliquid] extra")

from eth_account import Account

from simmer_sdk.hyperliquid_signing import RawKeyHyperliquidSigner
from simmer_sdk.hyperliquid_venue import HyperliquidVenue, HyperliquidVenueError
from simmer_sdk.venue_adapter import VenueAdapter

TEST_KEY = "0x" + "22" * 32
TEST_ADDR = Account.from_key(TEST_KEY).address


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _venue(monkeypatch, capture, response):
    """Build a venue whose requests.post is mocked to record calls + return `response`."""
    def _fake_post(url, json=None, timeout=None):
        capture.append((url, json))
        return _FakeResp(200, response)

    monkeypatch.setattr("simmer_sdk.hyperliquid_venue.requests.post", _fake_post)
    return HyperliquidVenue(RawKeyHyperliquidSigner(TEST_KEY), is_mainnet=True)


def test_conforms_to_venue_adapter_protocol(monkeypatch):
    v = _venue(monkeypatch, [], {})
    assert isinstance(v, VenueAdapter)
    assert v.venue == "hyperliquid"
    assert v.address == TEST_ADDR


def test_place_order_posts_signed_action(monkeypatch):
    calls = []
    resp = {"status": "ok", "response": {"type": "order", "data": {"statuses": [{"resting": {"oid": 999}}]}}}
    v = _venue(monkeypatch, calls, resp)

    out = v.place_order(size=10.0, limit_px=0.05, is_buy=True, outcome_id=173, side="yes")

    (url, body) = calls[0]
    assert url == "https://api.hyperliquid.xyz/exchange"
    assert body["action"]["type"] == "order"
    assert body["action"]["orders"][0]["a"] == 100_001_730  # asset id
    assert set(body["signature"]) == {"r", "s", "v"}
    assert body["nonce"] > 0
    assert out["response"]["data"]["statuses"][0]["resting"]["oid"] == 999


def test_exchange_error_status_raises(monkeypatch):
    v = _venue(monkeypatch, [], {"status": "err", "response": "Insufficient margin"})
    with pytest.raises(HyperliquidVenueError, match="Insufficient margin"):
        v.place_order(size=10.0, limit_px=0.05, is_buy=True, outcome_id=173)


def test_non_200_raises(monkeypatch):
    def _fake_post(url, json=None, timeout=None):
        return _FakeResp(502, {"x": "bad gateway"})

    monkeypatch.setattr("simmer_sdk.hyperliquid_venue.requests.post", _fake_post)
    v = HyperliquidVenue(RawKeyHyperliquidSigner(TEST_KEY), is_mainnet=True)
    with pytest.raises(HyperliquidVenueError, match="HTTP 502"):
        v.get_order_book(173)


def test_cancel_posts_cancel_action(monkeypatch):
    calls = []
    v = _venue(monkeypatch, calls, {"status": "ok", "response": {"type": "cancel"}})
    v.cancel_order(order_id=999, outcome_id=173, side="yes")
    (_, body) = calls[0]
    assert body["action"] == {"type": "cancel", "cancels": [{"a": 100_001_730, "o": 999}]}


def test_get_order_book_coin_notation(monkeypatch):
    calls = []
    v = _venue(monkeypatch, calls, {"coin": "#1730", "levels": [[], []]})
    v.get_order_book(173, "yes")
    v.get_order_book(173, "no")
    assert calls[0][1] == {"type": "l2Book", "coin": "#1730"}
    assert calls[1][1] == {"type": "l2Book", "coin": "#1731"}


def test_get_positions_extracts_asset_positions(monkeypatch):
    state = {"assetPositions": [{"position": {"coin": "#1730", "szi": "5.0"}}], "marginSummary": {}}
    v = _venue(monkeypatch, [], state)
    assert v.get_positions() == state["assetPositions"]


def test_get_balances_parses_margin_summary(monkeypatch):
    state = {"marginSummary": {"accountValue": "100.5"}, "withdrawable": "42.0"}
    v = _venue(monkeypatch, [], state)
    bal = v.get_balances()
    assert bal["account_value"] == "100.5"
    assert bal["withdrawable"] == "42.0"
    assert bal["raw"] is state


def test_testnet_base_url():
    v = HyperliquidVenue(RawKeyHyperliquidSigner(TEST_KEY), is_mainnet=False)
    assert v.base_url == "https://api.hyperliquid-testnet.xyz"
