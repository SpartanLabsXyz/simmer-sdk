"""Unit tests for PmxtHyperliquidVenue (SIM-4222, A2). No network.

Both hops are mocked: the pmxt sidecar (construction) and HL ``/exchange``
(submission). A stub signer stands in for ``HyperliquidSigner`` so these run
without the ``[hyperliquid]`` extra — CI installs the SDK bare, so unlike
``test_hyperliquid_venue.py`` (which skips) this file actually executes there.

FIXTURE_RAW is the action recorded from a real pmxt-core 2.54.0 sidecar during
the 2026-07-28 builder-attribution gate run — the same action that signed and
filled on mainnet with the fee accruing to our builder.
"""

import json

import pytest

from simmer_sdk.hyperliquid_venue import HyperliquidVenueError
from simmer_sdk.pmxt_hyperliquid_venue import PmxtHyperliquidVenue
from simmer_sdk.pmxt_sidecar import PmxtSidecarClient, PmxtSidecarError
from simmer_sdk.venue_adapter import VenueAdapter

BUILDER = "0xB42D8926BF2Db204Ad27A11817eB2EF2A9f5EF13"
SIGNER_ADDR = "0x" + "11" * 20
MAIN_ADDR = "0x" + "ab" * 20
SIG = {"r": "0x1", "s": "0x2", "v": 27}

# Recorded: market sell, 0.01 ETH @ 1859.3, builder fee 10 tenths-bp (1bp).
FIXTURE_RAW = {
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
}


class _StubSigner:
    """Minimal HyperliquidSigner: an address and a signature."""

    address = SIGNER_ADDR

    def __init__(self):
        self.calls = []

    def sign_l1_action(self, action, nonce, is_mainnet, vault_address=None, expires_after=None):
        self.calls.append((action, nonce, is_mainnet, vault_address))
        return SIG

    def sign_user_action(self, action, payload_types, primary_type, is_mainnet):
        return SIG


class _StubSidecar:
    """Stands in for PmxtSidecarClient; records params, returns a fixed action.

    Deliberately exposes a NO-OP ``assert_built_action``. The sidecar is an
    injectable transport seam, so if the venue ever dispatches the pre-sign gate
    through the instance instead of calling the class, every gate test in this
    file goes green while verifying nothing. This stub is what keeps that
    regression impossible to miss.
    """

    base_url = "http://127.0.0.1:8080"

    def __init__(self, action=None, healthy=True):
        self._action = action if action is not None else FIXTURE_RAW
        self._healthy = healthy
        self.params = []

    def health(self):
        return self._healthy

    def build_order(self, params):
        self.params.append(params)
        return self._action

    @staticmethod
    def assert_built_action(*args, **kwargs):
        return None


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def _venue(monkeypatch, *, action=None, exchange_response=None, capture=None, **kw):
    """Venue with both hops mocked; `capture` records HL /exchange posts."""
    resp = exchange_response if exchange_response is not None else {"status": "ok", "response": {}}

    def _fake_post(url, json=None, timeout=None):
        if capture is not None:
            capture.append((url, json))
        return _FakeResp(200, resp)

    monkeypatch.setattr("simmer_sdk.hyperliquid_venue.requests.post", _fake_post)
    kw.setdefault("builder_address", BUILDER)
    return PmxtHyperliquidVenue(_StubSigner(), sidecar=_StubSidecar(action), **kw)


# ---- protocol + identity --------------------------------------------------


def test_conforms_to_venue_adapter_protocol(monkeypatch):
    v = _venue(monkeypatch)
    assert isinstance(v, VenueAdapter)
    assert v.venue == "hyperliquid"  # same venue; construction locus is internal
    assert v.construction == "pmxt"


def test_main_address_is_account_of_record(monkeypatch):
    v = _venue(monkeypatch, main_address=MAIN_ADDR)
    assert v.address == MAIN_ADDR
    assert v.signer_address == SIGNER_ADDR


def test_address_defaults_to_signer_for_raw_key_setups(monkeypatch):
    v = _venue(monkeypatch)
    assert v.address == SIGNER_ADDR


def test_testnet_base_url(monkeypatch):
    v = _venue(monkeypatch, is_mainnet=False)
    assert v.base_url == "https://api.hyperliquid-testnet.xyz"
    assert v.is_mainnet is False


# ---- builder configuration ------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "not-an-address",
        "0x123",
        "B42D8926BF2Db204Ad27A11817eB2EF2A9f5EF13",  # missing 0x
        "0x" + "11" * 32,  # a 32-byte private key must never pass as an address
        None,
        12345,
    ],
)
def test_rejects_invalid_builder_address(bad):
    with pytest.raises(ValueError, match="builder_address"):
        PmxtHyperliquidVenue(_StubSigner(), builder_address=bad, sidecar=_StubSidecar())


def test_rejects_negative_builder_fee():
    with pytest.raises(ValueError, match="non-negative"):
        PmxtHyperliquidVenue(
            _StubSigner(), builder_address=BUILDER, builder_fee_tenths_bp=-1,
            sidecar=_StubSidecar(),
        )


def test_rejects_non_int_builder_fee():
    with pytest.raises(ValueError, match="must be an int"):
        PmxtHyperliquidVenue(
            _StubSigner(), builder_address=BUILDER, builder_fee_tenths_bp=1.0,
            sidecar=_StubSidecar(),
        )


def test_default_builder_fee_is_one_bp(monkeypatch):
    """10 tenths-bp = 1bp — the rate the gate run filled at."""
    assert _venue(monkeypatch).builder_fee_tenths_bp == 10


# ---- param mapping (our args → pmxt CreateOrderParams) --------------------


def test_place_order_maps_params_to_pmxt_contract(monkeypatch):
    v = _venue(monkeypatch)
    v.place_order(
        size=0.01, limit_px=1859.3, is_buy=False, asset_id=1,
        order_type="market", market_id="ETH",
    )
    assert v._sidecar.params[0] == {
        "outcomeId": "1",  # numeric asset id AS A STRING — pmxt parseInt's it
        "side": "sell",
        "type": "market",
        "amount": 0.01,
        "price": 1859.3,
        "builder": BUILDER,  # string address, not the {b,f} object
        "builderFee": 10,  # separate int, tenths of a basis point
        "marketId": "ETH",
    }


def test_price_is_always_sent(monkeypatch):
    """An omitted price silently defaults to '0.5' in pmxt — catastrophic on a
    perp. limit_px is required, so `price` is present on every build."""
    v = _venue(monkeypatch)
    v.place_order(size=0.01, limit_px=1859.3, is_buy=False, asset_id=1, order_type="market")
    assert v._sidecar.params[0]["price"] == 1859.3


def test_market_id_omitted_when_not_given(monkeypatch):
    v = _venue(monkeypatch)
    v.place_order(size=0.01, limit_px=1859.3, is_buy=False, asset_id=1, order_type="market")
    assert "marketId" not in v._sidecar.params[0]


# ---- signing + submission -------------------------------------------------


def test_signs_the_pmxt_built_action_and_submits_to_hl(monkeypatch):
    calls = []
    resp = {"status": "ok", "response": {"type": "order", "data": {"statuses": [{"filled": {"oid": 504113172815}}]}}}
    v = _venue(monkeypatch, capture=calls, exchange_response=resp)

    out = v.place_order(
        size=0.01, limit_px=1859.3, is_buy=False, asset_id=1, order_type="market"
    )

    # signed exactly the action pmxt built — no post-build mutation
    signed_action, nonce, is_mainnet, vault = v._signer.calls[0]
    assert signed_action is FIXTURE_RAW
    assert is_mainnet is True
    assert nonce > 0

    url, body = calls[0]
    assert url == "https://api.hyperliquid.xyz/exchange"
    assert body["action"] == FIXTURE_RAW
    assert body["action"]["builder"] == {"b": BUILDER.lower(), "f": 10}
    assert body["signature"] == SIG
    assert body["nonce"] == nonce
    assert out["response"]["data"]["statuses"][0]["filled"]["oid"] == 504113172815


def test_msgpack_key_order_survives_to_submission(monkeypatch):
    """Key order is hash-relevant; nothing between build and submit may reorder."""
    calls = []
    v = _venue(monkeypatch, capture=calls)
    v.place_order(size=0.01, limit_px=1859.3, is_buy=False, asset_id=1, order_type="market")
    action = calls[0][1]["action"]
    assert list(action.keys()) == ["type", "orders", "grouping", "builder"]
    assert list(action["orders"][0].keys()) == ["a", "b", "p", "s", "r", "t"]


def test_vault_address_threads_through_signing_and_submit(monkeypatch):
    calls = []
    vault = "0x" + "cd" * 20
    v = _venue(monkeypatch, capture=calls, vault_address=vault)
    v.place_order(size=0.01, limit_px=1859.3, is_buy=False, asset_id=1, order_type="market")
    assert v._signer.calls[0][3] == vault
    assert calls[0][1]["vaultAddress"] == vault


def test_exchange_error_raises(monkeypatch):
    v = _venue(monkeypatch, exchange_response={"status": "err", "response": "Insufficient margin"})
    with pytest.raises(HyperliquidVenueError, match="Insufficient margin"):
        v.place_order(size=0.01, limit_px=1859.3, is_buy=False, asset_id=1, order_type="market")


# ---- the pre-sign gate ----------------------------------------------------
#
# Every case here must fail BEFORE signing: an unverified action must never
# reach the key.


def _mutated(**wire_changes):
    wire = dict(FIXTURE_RAW["orders"][0], **wire_changes)
    return dict(FIXTURE_RAW, orders=[wire])


def _expect_rejected_before_signing(monkeypatch, action, match):
    calls = []
    v = _venue(monkeypatch, action=action, capture=calls)
    with pytest.raises(PmxtSidecarError, match=match):
        v.place_order(size=0.01, limit_px=1859.3, is_buy=False, asset_id=1, order_type="market")
    assert v._signer.calls == [], "signed an action that failed verification"
    assert calls == [], "submitted an action that failed verification"


def test_rejects_wrong_asset(monkeypatch):
    _expect_rejected_before_signing(monkeypatch, _mutated(a=2), "asset mismatch")


def test_rejects_wrong_side(monkeypatch):
    _expect_rejected_before_signing(monkeypatch, _mutated(b=True), "side mismatch")


def test_rejects_wrong_price(monkeypatch):
    _expect_rejected_before_signing(monkeypatch, _mutated(p="1860.0"), "price mismatch")


def test_rejects_defaulted_price(monkeypatch):
    """pmxt's '0.5' default for an omitted price is the canonical footgun."""
    _expect_rejected_before_signing(monkeypatch, _mutated(p="0.5"), "price mismatch")


def test_rejects_wrong_size(monkeypatch):
    _expect_rejected_before_signing(monkeypatch, _mutated(s="1.0"), "size mismatch")


def test_rejects_wrong_tif(monkeypatch):
    """A market order that came back Gtc would rest instead of crossing."""
    _expect_rejected_before_signing(
        monkeypatch, _mutated(t={"limit": {"tif": "Gtc"}}), "tif mismatch"
    )


def test_rejects_reduce_only_flipped_on(monkeypatch):
    _expect_rejected_before_signing(monkeypatch, _mutated(r=True), "reduce_only")


def test_rejects_wrong_builder_address(monkeypatch):
    imposter = dict(FIXTURE_RAW, builder={"b": "0x" + "99" * 20, "f": 10})
    _expect_rejected_before_signing(monkeypatch, imposter, "builder mismatch")


def test_rejects_wrong_builder_fee(monkeypatch):
    """f is tenths of a bp; a 10x unit slip is the canonical mistake."""
    fat = dict(FIXTURE_RAW, builder={"b": BUILDER.lower(), "f": 100})
    _expect_rejected_before_signing(monkeypatch, fat, "builder mismatch")


def test_rejects_missing_builder(monkeypatch):
    """Silently unattributed orders are the whole reason this adapter exists."""
    stripped = {k: v for k, v in FIXTURE_RAW.items() if k != "builder"}
    _expect_rejected_before_signing(monkeypatch, stripped, "builder mismatch")


def test_rejects_unexpected_wire_field(monkeypatch):
    """An upstream addition (a generated cloid, a trigger leg) must not be
    signed blind just because the fields we check happen to be right."""
    _expect_rejected_before_signing(
        monkeypatch, _mutated(c="0x" + "aa" * 16), "unexpected order-wire fields"
    )


def test_rejects_unexpected_action_field(monkeypatch):
    extra = dict(FIXTURE_RAW, expiresAfter=123)
    _expect_rejected_before_signing(monkeypatch, extra, "unexpected action fields")


def test_rejects_non_limit_order_type(monkeypatch):
    """A trigger/TP-SL order shape reaching the signer would be a different
    order from the one requested."""
    _expect_rejected_before_signing(
        monkeypatch,
        _mutated(t={"trigger": {"isMarket": True, "triggerPx": "1800", "tpsl": "sl"}}),
        "only plain limit orders",
    )


def test_rejects_multi_order_action(monkeypatch):
    doubled = dict(FIXTURE_RAW, orders=[FIXTURE_RAW["orders"][0]] * 2)
    _expect_rejected_before_signing(monkeypatch, doubled, "exactly 1 order")


def test_gate_is_not_bypassable_via_the_injected_sidecar(monkeypatch):
    """The sidecar is a transport seam; it must not be able to supply the
    verification policy. `_StubSidecar.assert_built_action` is a no-op, so if
    the venue dispatched the gate through the instance this would pass a
    two-BUY-orders-on-the-wrong-asset action straight to the signer."""
    hostile = dict(
        FIXTURE_RAW,
        orders=[
            dict(FIXTURE_RAW["orders"][0], a=2, b=True),
            dict(FIXTURE_RAW["orders"][0], a=2, b=True),
        ],
    )
    _expect_rejected_before_signing(monkeypatch, hostile, "exactly 1 order")


def test_rejects_type_drifted_asset(monkeypatch):
    """`1.0 == 1` in Python but encodes differently in msgpack — catch it at
    the gate, not as an opaque signature failure at submit."""
    _expect_rejected_before_signing(monkeypatch, _mutated(a=1.0), "asset mismatch")


def test_rejects_type_drifted_side(monkeypatch):
    """`0 == False`, so an int side must not sneak past the bool check."""
    _expect_rejected_before_signing(monkeypatch, _mutated(b=0), "side mismatch")


def test_rejects_type_drifted_builder_fee(monkeypatch):
    """`10.0 == 10` — an "exact" builder match must mean exact."""
    floaty = dict(FIXTURE_RAW, builder={"b": BUILDER.lower(), "f": 10.0})
    _expect_rejected_before_signing(monkeypatch, floaty, "builder mismatch")


def test_rejects_sub_ulp_price_drift(monkeypatch):
    """float('0.10000000000000001') == 0.1, so a float comparison would sign
    a price we never asked for. The gate compares decimals."""
    calls = []
    v = _venue(monkeypatch, action=_mutated(p="0.10000000000000001"), capture=calls)
    with pytest.raises(PmxtSidecarError, match="price mismatch"):
        v.place_order(size=0.01, limit_px=0.1, is_buy=False, asset_id=1, order_type="market")
    assert v._signer.calls == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_rejects_non_finite_size_and_price(monkeypatch, bad):
    """NaN fails every comparison, so a bare `<= 0` guard would wave it
    through and serialize into the order as JSON NaN."""
    v = _venue(monkeypatch)
    with pytest.raises(HyperliquidVenueError, match="finite"):
        v.place_order(size=bad, limit_px=1859.3, is_buy=False, asset_id=1, order_type="market")
    with pytest.raises(HyperliquidVenueError, match="finite"):
        v.place_order(size=0.01, limit_px=bad, is_buy=False, asset_id=1, order_type="market")
    assert v._sidecar.params == []


def test_price_comparison_is_exact_not_stringwise(monkeypatch):
    """'1859.30' for 1859.3 is a legitimate restyle — compare numerically."""
    calls = []
    v = _venue(monkeypatch, action=_mutated(p="1859.30"), capture=calls)
    v.place_order(size=0.01, limit_px=1859.3, is_buy=False, asset_id=1, order_type="market")
    assert len(calls) == 1


def test_accepts_pmxts_legitimate_8dp_rounding(monkeypatch):
    """An arithmetic-derived price is the common case, not the exotic one:
    0.1 + 0.2 is 0.30000000000000004, and pmxt's toFixed(8) legitimately emits
    '0.3'. The gate must not reject its own pinned version's normalization."""
    calls = []
    v = _venue(monkeypatch, action=_mutated(p="0.3", s="0.3"), capture=calls)
    v.place_order(
        size=0.1 + 0.2, limit_px=0.1 + 0.2, is_buy=False, asset_id=1, order_type="market"
    )
    assert len(calls) == 1, "rejected a price pmxt is entitled to produce"


def test_rejects_drift_beyond_8dp_rounding(monkeypatch):
    """Tolerating 8dp rounding must not tolerate anything past it."""
    _expect_rejected_before_signing(monkeypatch, _mutated(p="1859.4"), "price mismatch")


def test_rejects_requests_pmxt_could_not_build(monkeypatch):
    """pmxt's floatToWire throws if 8dp rounding moves a value by >= 1e-12.
    Mirroring that means a drifted sidecar cannot return "0.00000001" for a
    requested 1.4e-8 — a 29% smaller value — and have it accepted."""
    v = _venue(monkeypatch)
    with pytest.raises(HyperliquidVenueError, match="not representable"):
        v.place_order(size=1.4e-8, limit_px=1859.3, is_buy=False, asset_id=1, order_type="market")
    with pytest.raises(HyperliquidVenueError, match="not representable"):
        v.place_order(size=0.01, limit_px=1.4e-8, is_buy=False, asset_id=1, order_type="market")
    assert v._sidecar.params == []


def test_rejects_positive_but_sub_wire_precision_values(monkeypatch):
    """pmxt emits "0" for 5e-13 (inside its own 1e-12 tolerance). A zero price
    must never be signed just because the request was nominally positive."""
    v = _venue(monkeypatch)
    with pytest.raises(HyperliquidVenueError, match="not representable"):
        v.place_order(size=0.01, limit_px=5e-13, is_buy=False, asset_id=1, order_type="market")


def test_rejects_zero_wire_value_from_the_sidecar(monkeypatch):
    _expect_rejected_before_signing(monkeypatch, _mutated(p="0"), "price mismatch")


def test_large_integer_price_is_not_falsely_rejected(monkeypatch):
    """Decimal's default 28-digit context would raise while quantizing a large
    integer price, turning a valid order into a refusal."""
    calls = []
    v = _venue(monkeypatch, action=_mutated(p="100000000000000000000"), capture=calls)
    v.place_order(size=0.01, limit_px=1e20, is_buy=False, asset_id=1, order_type="market")
    assert len(calls) == 1


def test_rejects_unexpected_fields_inside_the_limit_type(monkeypatch):
    _expect_rejected_before_signing(
        monkeypatch,
        _mutated(t={"limit": {"tif": "Ioc", "unexpected": True}}),
        "unexpected fields inside the limit",
    )


def test_rejects_non_bool_is_buy(monkeypatch):
    """A truthy non-bool would pick a side by accident and then satisfy the
    type-exact wire check anyway, since True == 1.0."""
    v = _venue(monkeypatch)
    with pytest.raises(HyperliquidVenueError, match="is_buy must be a bool"):
        v.place_order(size=0.01, limit_px=1859.3, is_buy=1.0, asset_id=1, order_type="market")
    assert v._sidecar.params == []


@pytest.mark.parametrize(
    "kw", [{"order_id": "999"}, {"order_id": True}, {"asset_id": "1"}, {"asset_id": True}]
)
def test_cancel_validates_wire_types(monkeypatch, kw):
    """Cancel args go straight into the signed wire with nothing downstream to
    catch them."""
    v = _venue(monkeypatch)
    args = dict(order_id=999, asset_id=1)
    args.update(kw)
    with pytest.raises(HyperliquidVenueError, match="must be an int"):
        v.cancel_order(**args)
    assert v._signer.calls == []


# ---- unsupported capabilities fail loudly ---------------------------------


def test_reduce_only_is_rejected_not_dropped(monkeypatch):
    """pmxt hardcodes r=false. Silently dropping this on a closing order would
    be a money-path bug, so it raises and points at the native adapter."""
    v = _venue(monkeypatch)
    with pytest.raises(HyperliquidVenueError, match="reduce_only"):
        v.place_order(
            size=0.01, limit_px=1859.3, is_buy=False, asset_id=1,
            order_type="market", reduce_only=True,
        )
    assert v._signer.calls == []


def test_cloid_is_rejected_not_dropped(monkeypatch):
    v = _venue(monkeypatch)
    with pytest.raises(HyperliquidVenueError, match="cloid"):
        v.place_order(
            size=0.01, limit_px=1859.3, is_buy=False, asset_id=1,
            order_type="market", cloid="0x" + "aa" * 16,
        )


def test_alo_post_only_is_rejected(monkeypatch):
    """Alo is unreachable through pmxt construction."""
    v = _venue(monkeypatch)
    with pytest.raises(HyperliquidVenueError, match="not reachable"):
        v.place_order(size=0.01, limit_px=1859.3, is_buy=True, asset_id=1, tif="Alo")


@pytest.mark.parametrize(
    "kw,match",
    [
        ({"asset_id": "1"}, "asset_id must be an int"),
        ({"asset_id": True}, "asset_id must be an int"),
        ({"size": 0}, "size must be a positive"),
        ({"size": -1.0}, "size must be a positive"),
        ({"limit_px": 0}, "limit_px must be a positive"),
        ({"limit_px": -5.0}, "limit_px must be a positive"),
    ],
)
def test_bad_arguments_fail_with_a_naming_error(monkeypatch, kw, match):
    """The failure should name the bad argument, not surface later as an
    inscrutable gate mismatch."""
    v = _venue(monkeypatch)
    args = dict(size=0.01, limit_px=1859.3, is_buy=False, asset_id=1, order_type="market")
    args.update(kw)
    with pytest.raises(HyperliquidVenueError, match=match):
        v.place_order(**args)
    assert v._sidecar.params == [], "bad arguments reached the sidecar"
    assert v._signer.calls == []


def test_unknown_order_type_is_rejected(monkeypatch):
    v = _venue(monkeypatch)
    with pytest.raises(HyperliquidVenueError, match="order_type"):
        v.place_order(size=0.01, limit_px=1859.3, is_buy=True, asset_id=1, order_type="stop")


def test_conflicting_order_type_and_tif_is_rejected(monkeypatch):
    v = _venue(monkeypatch)
    with pytest.raises(HyperliquidVenueError, match="conflicting"):
        v.place_order(
            size=0.01, limit_px=1859.3, is_buy=True, asset_id=1,
            order_type="market", tif="Gtc",
        )


def test_tif_alias_accepted_when_consistent(monkeypatch):
    calls = []
    v = _venue(monkeypatch, capture=calls)
    v.place_order(
        size=0.01, limit_px=1859.3, is_buy=False, asset_id=1,
        order_type="market", tif="Ioc",
    )
    assert len(calls) == 1


# ---- reads delegate to the account-of-record ------------------------------


def test_reads_use_main_address(monkeypatch):
    calls = []
    state = {"assetPositions": [{"position": {"coin": "ETH"}}], "marginSummary": {"accountValue": "100.5"}, "withdrawable": "42.0"}

    def _fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        return _FakeResp(200, state)

    monkeypatch.setattr("simmer_sdk.hyperliquid_venue.requests.post", _fake_post)
    v = PmxtHyperliquidVenue(
        _StubSigner(), builder_address=BUILDER, main_address=MAIN_ADDR, sidecar=_StubSidecar()
    )

    assert v.get_positions() == state["assetPositions"]
    assert v.get_balances()["account_value"] == "100.5"
    v.get_open_orders()

    for _, body in calls:
        assert body["user"] == MAIN_ADDR, "read keyed to the signer, not the account"


def test_reads_accept_explicit_address_override(monkeypatch):
    calls = []

    def _fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        return _FakeResp(200, {"assetPositions": []})

    monkeypatch.setattr("simmer_sdk.hyperliquid_venue.requests.post", _fake_post)
    v = PmxtHyperliquidVenue(
        _StubSigner(), builder_address=BUILDER, main_address=MAIN_ADDR, sidecar=_StubSidecar()
    )
    other = "0x" + "ef" * 20
    v.get_positions(other)
    assert calls[0][1]["user"] == other


# ---- cancel ---------------------------------------------------------------


def test_cancel_builds_natively_and_submits(monkeypatch):
    calls = []
    v = _venue(monkeypatch, capture=calls)
    v.cancel_order(order_id=999, asset_id=1)
    assert calls[0][1]["action"] == {"type": "cancel", "cancels": [{"a": 1, "o": 999}]}
    assert v._sidecar.params == [], "cancel must not touch the sidecar"


# ---- preflight ------------------------------------------------------------


def test_preflight_passes_against_recorded_contract(monkeypatch):
    """A Gtc probe must come back Gtc; the fixture is Ioc, so preflight needs
    its own action shaped like a limit order."""
    probe = {
        "type": "order",
        "orders": [{"a": 1, "b": True, "p": "1000.0", "s": "0.01", "r": False, "t": {"limit": {"tif": "Gtc"}}}],
        "grouping": "na",
        "builder": {"b": BUILDER.lower(), "f": 10},
    }
    v = _venue(monkeypatch, action=probe)
    v.preflight()
    assert v._sidecar.params[0]["type"] == "limit"


def test_preflight_fails_on_unhealthy_sidecar():
    v = PmxtHyperliquidVenue(
        _StubSigner(), builder_address=BUILDER, sidecar=_StubSidecar(healthy=False)
    )
    with pytest.raises(PmxtSidecarError, match="unhealthy"):
        v.preflight()


def test_preflight_fails_on_drifted_shape(monkeypatch):
    """Startup handshake is the tripwire for an undocumented dispatcher that
    changed under us — catch it before the first trade, not during."""
    drifted = {
        "type": "order",
        "orders": [{"a": 1, "b": True, "p": "1000.0", "s": "0.01", "r": False, "t": {"limit": {"tif": "Gtc"}}}],
        "grouping": "na",
        # builder dropped upstream → orders would fill unattributed
    }
    v = _venue(monkeypatch, action=drifted)
    with pytest.raises(PmxtSidecarError, match="builder mismatch"):
        v.preflight()
