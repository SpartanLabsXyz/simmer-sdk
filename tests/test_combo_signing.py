"""
Offline unit tests for combo (RFQ) order signing — pure crypto, NO network.

Verifies the ERC-7739-v3 deposit-wallet wrap structure, the v3 domain
separator, the EOA signature recovery, and the identity invariants
(signer == maker == wallet). These prove the signer is STRUCTURALLY correct;
they cannot prove the live combo gateway accepts the wrap — that is the
morning live $1 fill (see combo-builder-morning-runbook.md).

Test key is a deterministic throwaway (0x01*32), never a funded wallet.
"""
import pytest
from eth_account import Account
from eth_utils import keccak, to_checksum_address
from eth_abi import encode as abi_encode

from simmer_sdk.signing import _ORDER_TYPE_STRING, _EIP712_DOMAIN_TYPE
from simmer_sdk import combo_signing as cs

# Deterministic throwaway key (NOT a real/funded wallet).
TEST_PK = "0x" + "01" * 32
TEST_EOA = Account.from_key(TEST_PK).address
TEST_DW = "0x4fBd7fcC42C9b393A95E059042E7c0553B124326"  # shape only; any address works

# Fixed inputs for determinism.
TOKEN = "1107413250132172873224635916030721458874274285221305485470547607907458875392"
MAKER_AMT = 1_026_000  # $1.026 e6 (from a quote)
TAKER_AMT = 16_390_000  # 16.39 shares e6
FIXED_SALT = 123456789
FIXED_TS = 1781547512


def _dw_order():
    return cs.build_and_sign_combo_order_dw(
        private_key=TEST_PK, eoa_address=TEST_EOA, deposit_wallet_address=TEST_DW,
        token_id=TOKEN, side="BUY", maker_amount=MAKER_AMT, taker_amount=TAKER_AMT,
        salt=FIXED_SALT, timestamp=FIXED_TS,
    )


def _eoa_order():
    return cs.build_and_sign_combo_order_eoa(
        private_key=TEST_PK, eoa_address=TEST_EOA,
        token_id=TOKEN, side="BUY", maker_amount=MAKER_AMT, taker_amount=TAKER_AMT,
        salt=FIXED_SALT, timestamp=FIXED_TS,
    )


# ── DW (POLY_1271) wrap ──

def test_dw_signature_is_317_bytes():
    order = _dw_order()
    raw = bytes.fromhex(order.signature[2:])
    assert len(raw) == 317, f"expected 317-byte wrap, got {len(raw)}"
    assert order.signatureType == 3


def test_dw_wrap_structure():
    """innerSig(65) || appDomSep(32) || contentsHash(32) || typeStr(186) || len(2)."""
    raw = bytes.fromhex(_dw_order().signature[2:])
    type_len = len(_ORDER_TYPE_STRING)  # 186
    assert raw[0:65] != b"\x00" * 65            # inner ECDSA sig present
    type_str = raw[129:129 + type_len]
    assert type_str == _ORDER_TYPE_STRING       # embedded Order type string
    len_bytes = raw[129 + type_len:]
    assert len(len_bytes) == 2
    assert (len_bytes[0] << 8) | len_bytes[1] == type_len


def test_dw_inner_v_not_normalized():
    """v must be 27/28 (Solady ecrecover returns 0x0 for v in {0,1})."""
    raw = bytes.fromhex(_dw_order().signature[2:])
    v = raw[64]
    assert v in (27, 28), f"inner sig v={v} must be 27/28, not normalized to 0/1"


def test_dw_app_domain_separator_is_v3():
    """The appDomainSeparator embedded in the wrap matches the combo v3 domain."""
    raw = bytes.fromhex(_dw_order().signature[2:])
    embedded = raw[65:97]
    d = cs.combo_domain()
    expected = keccak(abi_encode(
        ["bytes32", "bytes32", "bytes32", "uint256", "address"],
        [keccak(_EIP712_DOMAIN_TYPE), keccak(d["name"].encode()),
         keccak(d["version"].encode()), int(d["chainId"]),
         to_checksum_address(d["verifyingContract"])],
    ))
    assert embedded == expected


def test_dw_domain_is_not_v2_clob():
    """Sanity: the v3 override took — separator differs from the V2 CLOB exchange."""
    raw = bytes.fromhex(_dw_order().signature[2:])
    embedded = raw[65:97]
    v2 = keccak(abi_encode(
        ["bytes32", "bytes32", "bytes32", "uint256", "address"],
        [keccak(_EIP712_DOMAIN_TYPE), keccak(b"Polymarket CTF Exchange"),
         keccak(b"2"), 137,
         to_checksum_address("0xE111180000d2663C0091e4f400237545B87B996B")],
    ))
    assert embedded != v2


def test_dw_contents_hash_matches_order():
    raw = bytes.fromhex(_dw_order().signature[2:])
    embedded = raw[97:129]
    msg = cs._build_order_message(
        maker=to_checksum_address(TEST_DW), signer=to_checksum_address(TEST_DW),
        token_id=TOKEN, maker_amount=MAKER_AMT, taker_amount=TAKER_AMT, side="BUY",
        signature_type=3, metadata=None, builder=None, salt=FIXED_SALT, timestamp=FIXED_TS,
    )
    expected = keccak(abi_encode(
        ["bytes32", "uint256", "address", "address", "uint256", "uint256",
         "uint256", "uint8", "uint8", "uint256", "bytes32", "bytes32"],
        [keccak(_ORDER_TYPE_STRING), int(msg["salt"]),
         to_checksum_address(msg["maker"]), to_checksum_address(msg["signer"]),
         int(msg["tokenId"]), int(msg["makerAmount"]), int(msg["takerAmount"]),
         int(msg["side"]), int(msg["signatureType"]), int(msg["timestamp"]),
         bytes.fromhex(msg["metadata"][2:]), bytes.fromhex(msg["builder"][2:])],
    ))
    assert embedded == expected


def test_dw_maker_signer_equal_dw():
    order = _dw_order()
    assert order.maker == to_checksum_address(TEST_DW)
    assert order.signer == to_checksum_address(TEST_DW)
    assert order.maker == order.signer


def test_dw_deterministic_with_fixed_salt_ts():
    assert _dw_order().signature == _dw_order().signature


# ── EOA wrap ──

def test_eoa_signature_recovers_to_eoa():
    from eth_account.messages import encode_typed_data
    order = _eoa_order()
    msg = cs._build_order_message(
        maker=TEST_EOA, signer=TEST_EOA, token_id=TOKEN,
        maker_amount=MAKER_AMT, taker_amount=TAKER_AMT, side="BUY",
        signature_type=0, metadata=None, builder=None, salt=FIXED_SALT, timestamp=FIXED_TS,
    )
    typed = {
        "domain": cs.combo_domain(),
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"},
            ],
            **cs.COMBO_ORDER_TYPES,
        },
        "primaryType": "Order",
        "message": msg,
    }
    recovered = Account.recover_message(encode_typed_data(full_message=typed), signature=order.signature)
    assert recovered.lower() == TEST_EOA.lower()


def test_eoa_signature_is_65_bytes():
    raw = bytes.fromhex(_eoa_order().signature[2:])
    assert len(raw) == 65
    assert _eoa_order().signatureType == 0


def test_eoa_maker_signer_equal_eoa():
    order = _eoa_order()
    assert order.maker == to_checksum_address(TEST_EOA)
    assert order.signer == to_checksum_address(TEST_EOA)


# ── shared invariants ──

def test_side_mapping():
    buy = cs._build_order_message(maker=TEST_EOA, signer=TEST_EOA, token_id=TOKEN,
        maker_amount=MAKER_AMT, taker_amount=TAKER_AMT, side="BUY",
        signature_type=0, metadata=None, builder=None)
    sell = cs._build_order_message(maker=TEST_EOA, signer=TEST_EOA, token_id=TOKEN,
        maker_amount=MAKER_AMT, taker_amount=TAKER_AMT, side="sell",
        signature_type=0, metadata=None, builder=None)
    assert buy["side"] == 0
    assert sell["side"] == 1


def test_amounts_from_quote_preserved():
    order = _dw_order()
    assert order.makerAmount == str(MAKER_AMT)
    assert order.takerAmount == str(TAKER_AMT)
    assert order.expiration == "0"
    assert order.exchange_version == "v3"


def test_zero_amounts_rejected():
    with pytest.raises(ValueError):
        cs._build_order_message(maker=TEST_EOA, signer=TEST_EOA, token_id=TOKEN,
            maker_amount=0, taker_amount=TAKER_AMT, side="BUY",
            signature_type=0, metadata=None, builder=None)


def test_key_address_mismatch_refused():
    other = Account.create().address
    with pytest.raises(ValueError, match="Refusing to sign"):
        cs.build_and_sign_combo_order_eoa(
            private_key=TEST_PK, eoa_address=other, token_id=TOKEN,
            side="BUY", maker_amount=MAKER_AMT, taker_amount=TAKER_AMT,
        )
