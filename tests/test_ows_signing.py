"""Tests for OWS order signing path."""

import json
import pytest


def test_build_and_sign_order_ows():
    """OWS signing produces valid Polymarket order with correct fields."""
    pytest.importorskip("ows")
    from simmer_sdk.signing import build_and_sign_order_ows

    result = build_and_sign_order_ows(
        ows_wallet="test-polymarket",
        token_id="1234567890",
        side="BUY",
        price=0.5,
        size=10.0,
    )

    assert result.signature, "Signature should not be empty"
    assert result.maker.startswith("0x"), "Maker should be an EVM address"
    assert result.signer == result.maker, "Signer should match maker"
    assert result.side == "BUY"
    assert result.signatureType == 0  # EOA
    assert result.tokenId == "1234567890"
    assert int(result.makerAmount) > 0
    assert int(result.takerAmount) > 0


def test_build_and_sign_order_ows_neg_risk():
    """OWS signing works for neg_risk markets."""
    pytest.importorskip("ows")
    from simmer_sdk.signing import build_and_sign_order_ows

    result = build_and_sign_order_ows(
        ows_wallet="test-polymarket",
        token_id="1234567890",
        side="SELL",
        price=0.3,
        size=10.0,
        neg_risk=True,
    )

    assert result.signature
    assert result.side == "SELL"


def test_build_and_sign_order_ows_signature_recovers():
    """Signature from OWS recovers to the wallet's address."""
    pytest.importorskip("ows")
    pytest.importorskip("eth_account")
    from simmer_sdk.signing import build_and_sign_order_ows
    from simmer_sdk.ows_utils import get_ows_wallet_address
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    from py_order_utils.builders import OrderBuilder
    from py_order_utils.signer import Signer
    from py_order_utils.model import OrderData, EOA, BUY
    from py_clob_client.config import get_contract_config
    from poly_eip712_structs import make_domain

    wallet_address = get_ows_wallet_address("test-polymarket")
    result = build_and_sign_order_ows(
        ows_wallet="test-polymarket",
        token_id="1234567890",
        side="BUY",
        price=0.5,
        size=10.0,
    )

    # Reconstruct the EIP-712 typed data to verify recovery
    contract_config = get_contract_config(137, False)
    dummy_account = Account.create()
    order_builder = OrderBuilder(
        contract_config.exchange, 137,
        Signer(key=dummy_account.key.hex()),
    )
    data = OrderData(
        maker=dummy_account.address,
        taker="0x0000000000000000000000000000000000000000",
        tokenId="1234567890",
        makerAmount=result.makerAmount,
        takerAmount=result.takerAmount,
        side=BUY,
        feeRateBps="0",
        nonce="0",
        signer=dummy_account.address,
        expiration="0",
        signatureType=EOA,
    )
    order = order_builder.build_order(data)
    order.values["salt"] = int(result.salt)
    order.values["maker"] = result.maker
    order.values["signer"] = result.signer

    domain = make_domain(
        name="Polymarket CTF Exchange",
        version="1",
        chainId="137",
        verifyingContract=contract_config.exchange,
    )
    typed_data = json.loads(order.to_message_json(domain=domain))
    message = encode_typed_data(full_message=typed_data)
    recovered = Account.recover_message(
        message,
        signature=bytes.fromhex(result.signature.replace("0x", ""))
    )
    assert recovered.lower() == wallet_address.lower()


def test_build_and_sign_order_ows_validation():
    """OWS signing validates inputs the same as regular signing."""
    pytest.importorskip("ows")
    from simmer_sdk.signing import build_and_sign_order_ows

    with pytest.raises(ValueError, match="Invalid side"):
        build_and_sign_order_ows(
            ows_wallet="test-polymarket",
            token_id="123", side="INVALID", price=0.5, size=10.0,
        )

    with pytest.raises(ValueError, match="Invalid price"):
        build_and_sign_order_ows(
            ows_wallet="test-polymarket",
            token_id="123", side="BUY", price=0.0, size=10.0,
        )

    with pytest.raises(ValueError, match="Invalid size"):
        build_and_sign_order_ows(
            ows_wallet="test-polymarket",
            token_id="123", side="BUY", price=0.5, size=-1.0,
        )
