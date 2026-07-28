"""Contract tests for PmxtSidecarClient (SIM-4222, A1). No network.

The FIXTURE_* payloads are RECORDED RESPONSES from a real pmxt-core 2.54.0
sidecar during the 2026-07-28 builder-attribution gate run (which ended in two
live attributed mainnet fills). They are the contract: if a pmxt version bump
changes any of these shapes, these tests are the tripwire — update the pin,
re-record, and dry-run diff per the version-bump policy in
``simmer/_dev/active/_hyperliquid-rd/pmxt-hl-venue-adapter-spec.md``.
"""

import json

import pytest

from simmer_sdk.pmxt_sidecar import (
    PINNED_PMXT_VERSION,
    PmxtSidecarClient,
    PmxtSidecarError,
)

BUILDER = "0xB42D8926BF2Db204Ad27A11817eB2EF2A9f5EF13"

# Recorded from pmxt-core 2.54.0, 2026-07-28 (params echoed by the dispatcher;
# raw is the unsigned action that went on to sign + fill on mainnet).
FIXTURE_BUILD_ORDER = {
    "success": True,
    "data": {
        "exchange": "Hyperliquid",
        "params": {
            "marketId": "ETH",
            "outcomeId": "1",
            "side": "sell",
            "type": "market",
            "amount": 0.01,
            "price": 1859.3,
            "builder": BUILDER,
            "builderFee": 10,
        },
        "raw": {
            "type": "order",
            "orders": [
                {
                    "a": 1,
                    "b": False,
                    "p": "1859.3",
                    "s": "0.01",
                    "r": False,
                    "t": {"limit": {"tif": "Ioc"}},
                }
            ],
            "grouping": "na",
            "builder": {"b": BUILDER.lower(), "f": 10},
        },
    },
}

FIXTURE_HEALTH = {"status": "ok", "timestamp": 1785219085579}


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _client(monkeypatch, response, capture=None, status=200):
    def _fake_post(url, json=None, timeout=None):
        if capture is not None:
            capture.append((url, json))
        return _FakeResp(status, response)

    def _fake_get(url, timeout=None):
        return _FakeResp(status, response)

    monkeypatch.setattr("simmer_sdk.pmxt_sidecar.requests.post", _fake_post)
    monkeypatch.setattr("simmer_sdk.pmxt_sidecar.requests.get", _fake_get)
    return PmxtSidecarClient("http://127.0.0.1:8080")


# ---- envelope contract ----------------------------------------------------


def test_build_order_unwraps_recorded_envelope(monkeypatch):
    capture = []
    c = _client(monkeypatch, FIXTURE_BUILD_ORDER, capture)
    action = c.build_order(FIXTURE_BUILD_ORDER["data"]["params"])
    assert action == FIXTURE_BUILD_ORDER["data"]["raw"]
    # bare-object body on the dispatcher route — not wrapped in {"args": [...]}
    url, body = capture[0]
    assert url.endswith("/api/hyperliquid/buildOrder")
    assert body == FIXTURE_BUILD_ORDER["data"]["params"]


def test_build_order_preserves_msgpack_key_order(monkeypatch):
    """Key order (type, orders, grouping, builder) is hash-relevant; the client
    must return the action untouched, in dispatcher order."""
    c = _client(monkeypatch, FIXTURE_BUILD_ORDER)
    action = c.build_order({})
    assert list(action.keys()) == ["type", "orders", "grouping", "builder"]
    assert list(action["orders"][0].keys()) == ["a", "b", "p", "s", "r", "t"]


def test_build_order_rejects_missing_success(monkeypatch):
    c = _client(monkeypatch, {"data": FIXTURE_BUILD_ORDER["data"]})
    with pytest.raises(PmxtSidecarError, match="envelope"):
        c.build_order({})


def test_build_order_rejects_missing_raw(monkeypatch):
    c = _client(monkeypatch, {"success": True, "data": {"exchange": "Hyperliquid"}})
    with pytest.raises(PmxtSidecarError, match="raw action"):
        c.build_order({})


def test_build_order_rejects_http_error(monkeypatch):
    c = _client(monkeypatch, {"error": "boom"}, status=500)
    with pytest.raises(PmxtSidecarError, match="HTTP 500"):
        c.build_order({})


def test_health_ok(monkeypatch):
    c = _client(monkeypatch, FIXTURE_HEALTH)
    assert c.health() is True


def test_health_bad_status(monkeypatch):
    c = _client(monkeypatch, {"status": "degraded"})
    assert c.health() is False


# ---- assert_built_action (the pre-sign gate) ------------------------------

RAW = FIXTURE_BUILD_ORDER["data"]["raw"]


def _assert_ok(action=RAW, **overrides):
    kw = dict(asset_id=1, is_buy=False, builder=BUILDER, builder_fee_tenths_bp=10)
    kw.update(overrides)
    PmxtSidecarClient.assert_built_action(action, **kw)


def test_assert_accepts_the_recorded_action():
    _assert_ok()


def test_assert_rejects_wrong_asset():
    with pytest.raises(PmxtSidecarError, match="asset mismatch"):
        _assert_ok(asset_id=2)


def test_assert_rejects_wrong_side():
    with pytest.raises(PmxtSidecarError, match="side mismatch"):
        _assert_ok(is_buy=True)


def test_assert_rejects_wrong_builder_address():
    with pytest.raises(PmxtSidecarError, match="builder mismatch"):
        _assert_ok(builder="0x" + "11" * 20)


def test_assert_rejects_wrong_fee():
    """f is tenths of a basis point; a 10x unit slip is the canonical mistake."""
    with pytest.raises(PmxtSidecarError, match="builder mismatch"):
        _assert_ok(builder_fee_tenths_bp=100)


def test_assert_rejects_unexpected_builder_when_none_requested():
    with pytest.raises(PmxtSidecarError, match="unexpected builder"):
        _assert_ok(builder=None, builder_fee_tenths_bp=None)


def test_assert_accepts_builderless_action_when_none_requested():
    builderless = {k: v for k, v in RAW.items() if k != "builder"}
    PmxtSidecarClient.assert_built_action(
        builderless, asset_id=1, is_buy=False, builder=None, builder_fee_tenths_bp=None
    )


def test_assert_rejects_multi_order_actions():
    doubled = dict(RAW, orders=[RAW["orders"][0], RAW["orders"][0]])
    with pytest.raises(PmxtSidecarError, match="exactly 1 order"):
        _assert_ok(action=doubled)


def test_pinned_version_is_recorded():
    """The pin the fixtures were recorded against — bump deliberately."""
    assert PINNED_PMXT_VERSION == "2.54.0"
