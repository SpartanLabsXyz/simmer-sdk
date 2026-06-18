"""
Offline tests for combo placement plumbing — NO network, NO socket.

Covers the product-of-legs estimate, the dry-run plan / identity resolution,
input validation, and the RFQ_ACCEPT wire shape (side+signatureType as ints,
expiration '0'). The live WS path is exercised only by the morning $1 fill.
"""
import pytest
from eth_account import Account

from simmer_sdk import combo as cb
from simmer_sdk import combo_signing as cs

TEST_PK = "0x" + "01" * 32
TEST_EOA = Account.from_key(TEST_PK).address
TEST_DW = "0x4fBd7fcC42C9b393A95E059042E7c0553B124326"
LEGS = ["111", "222"]


def test_estimate_product_of_legs():
    q = cb.estimate_combo_price([0.5, 0.4], stake=10)
    assert q is not None
    assert abs(q["combined_price"] - 0.2) < 1e-9
    assert abs(q["multiplier"] - 5.0) < 1e-9
    assert abs(q["potential_payout"] - 50.0) < 1e-9


def test_estimate_needs_two_legs():
    assert cb.estimate_combo_price([0.5]) is None
    assert cb.estimate_combo_price([0.5, 0]) is None  # invalid leg dropped -> <2


def test_dry_run_plan_eoa():
    plan = cb.place_combo(
        creds={}, private_key=TEST_PK, eoa_address=TEST_EOA,
        leg_position_ids=LEGS, size_usdc=1.0, signature_type=0, dry_run=True,
    )
    assert plan["dry_run"] is True
    assert plan["identity"]["signature_type"] == 0
    assert plan["identity"]["maker_address"] == TEST_EOA
    assert plan["requested_size"]["value_e6"] == "1000000"
    assert "no socket" in plan["note"].lower()


def test_dry_run_plan_dw():
    plan = cb.place_combo(
        creds={}, private_key=TEST_PK, eoa_address=TEST_EOA,
        leg_position_ids=LEGS, size_usdc=2.5, signature_type=3,
        deposit_wallet_address=TEST_DW, dry_run=True,
    )
    assert plan["identity"]["signature_type"] == 3
    assert plan["identity"]["maker_address"] == TEST_DW
    assert plan["requested_size"]["value_e6"] == "2500000"


def test_validation_min_legs():
    with pytest.raises(ValueError, match="at least 2 legs"):
        cb.place_combo(creds={}, private_key=TEST_PK, eoa_address=TEST_EOA,
                       leg_position_ids=["111"], size_usdc=1.0, dry_run=True)


def test_validation_min_stake():
    with pytest.raises(ValueError, match="at least .1"):
        cb.place_combo(creds={}, private_key=TEST_PK, eoa_address=TEST_EOA,
                       leg_position_ids=LEGS, size_usdc=0.5, dry_run=True)


def test_validation_dw_requires_address():
    with pytest.raises(ValueError, match="requires deposit_wallet_address"):
        cb.place_combo(creds={}, private_key=TEST_PK, eoa_address=TEST_EOA,
                       leg_position_ids=LEGS, size_usdc=1.0, signature_type=3, dry_run=True)


def test_dw_live_explicitly_disabled():
    """DW combos are SUPPORTED now (proven on-chain). The module default
    (allow_deposit_wallet=True) allows them; passing allow_deposit_wallet=False
    explicitly disables a DW combo with a clear message that points at
    activate_combo_dw(). (The SimmerClient gates on the on-chain approval
    pre-check; this is the low-level module's opt-out.)"""
    with pytest.raises(cb.ComboPlacementError, match="activate_combo_dw"):
        cb.place_combo(
            creds={"apiKey": "k", "secret": "s", "passphrase": "p"},
            private_key=TEST_PK, eoa_address=TEST_EOA,
            leg_position_ids=LEGS, size_usdc=1.0, signature_type=3,
            deposit_wallet_address=TEST_DW, dry_run=False,  # live
            allow_deposit_wallet=False,  # explicit opt-out
        )


def test_dw_dry_run_still_works():
    """DW dry-run is NOT gated — it shows the plan (no money, no socket)."""
    plan = cb.place_combo(
        creds={}, private_key=TEST_PK, eoa_address=TEST_EOA,
        leg_position_ids=LEGS, size_usdc=1.0, signature_type=3,
        deposit_wallet_address=TEST_DW, dry_run=True,
    )
    assert plan["dry_run"] is True
    assert plan["identity"]["signature_type"] == 3


def test_signed_order_wire_shape():
    order = cs.build_and_sign_combo_order_dw(
        private_key=TEST_PK, eoa_address=TEST_EOA, deposit_wallet_address=TEST_DW,
        token_id="111", side="BUY", maker_amount=1_026_000, taker_amount=16_390_000,
        salt=1, timestamp=1,
    )
    wire = cb._signed_order_to_wire(order)
    assert wire["side"] == 0  # BUY -> int 0 (not "BUY")
    assert isinstance(wire["signatureType"], int) and wire["signatureType"] == 3
    assert wire["expiration"] == "0"
    assert isinstance(wire["makerAmount"], str) and wire["makerAmount"] == "1026000"
    assert wire["maker"] == wire["signer"]  # DW == DW


def test_fetch_combo_legs_geoblock_403(monkeypatch):
    """SIM-3279: a 403 from the geo-restricted combo RFQ API raises a clear
    ComboGeoBlockError (with the server's reason), not an opaque HTTPError."""
    class FakeResp:
        status_code = 403
        text = '{"message": "Trading restricted in your region"}'
        def json(self):
            return {"message": "Trading restricted in your region"}

    monkeypatch.setattr(cb.requests, "get", lambda *a, **k: FakeResp())
    with pytest.raises(cb.ComboGeoBlockError) as ei:
        cb.fetch_combo_legs()
    msg = str(ei.value)
    assert "not available in your region" in msg
    assert "Trading restricted in your region" in msg  # server detail surfaced
    # subclass of ComboPlacementError, so existing handlers catch it unchanged
    assert isinstance(ei.value, cb.ComboPlacementError)
