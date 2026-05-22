"""Tests for build_and_sign_order_v2_dw_ows (V2 sig-type-3 / POLY_1271 / OWS).

These tests cover the new OWS V2-DW signing path added 2026-05-22 to unblock
per-agent OWS users on Polymarket. See _dev/active/_v2-ows-dw-signing/spec.md
for full design notes.

Test methodology follows codex consult v2 P2 guidance:
  - Inner signature checked via address recoverability (ecrecover), NOT
    byte-equality with eth_account output. ECDSA may use different valid
    nonces between OWS Rust and eth_account Python; both produce valid
    signatures.
  - `_coerce_typed_data_uints` tested at its own level (it's an internal
    helper invoked inside ows_sign_typed_data); the OWS sign call itself
    is exercised by the end-to-end test_e2e_signing below.

All tests use throwaway OWS wallets created at setUp and deleted at tearDown.
"""

import json
import unittest
from typing import Optional

try:
    import ows
    OWS_AVAILABLE = True
except ImportError:
    OWS_AVAILABLE = False

if OWS_AVAILABLE:
    from simmer_sdk.signing import build_and_sign_order_v2_dw_ows


_TEST_WALLET_PREFIX = "_test_v2_dw_ows_"
_TEST_DW = "0x981929dae94c6a9859a22CB029F6c0F2b1b68624"
_TEST_TOKEN_ID = (
    "71321045679252212594626385532706912750332728571942134274129960822013436447464"
)


def _make_test_wallet(name_suffix: str) -> str:
    """Create a throwaway OWS wallet, return its name and EVM address."""
    name = _TEST_WALLET_PREFIX + name_suffix
    try:
        ows.delete_wallet(name)
    except Exception:
        pass
    w = ows.create_wallet(name)
    eoa = [
        a for a in w["accounts"] if a["chain_id"] == "eip155:1"
    ][0]["address"]
    return name, eoa


def _delete_test_wallet(name: str) -> None:
    try:
        ows.delete_wallet(name)
    except Exception:
        pass


@unittest.skipUnless(OWS_AVAILABLE, "open-wallet-standard not installed")
class TestV2DwOwsHappyPath(unittest.TestCase):
    """End-to-end signing produces well-formed V2-DW orders."""

    def setUp(self):
        self.wallet_name, self.eoa = _make_test_wallet("happy")

    def tearDown(self):
        _delete_test_wallet(self.wallet_name)

    def test_gtc_buy_produces_317_byte_wrap(self):
        signed = build_and_sign_order_v2_dw_ows(
            ows_wallet=self.wallet_name,
            eoa_address=self.eoa,
            deposit_wallet_address=_TEST_DW,
            token_id=_TEST_TOKEN_ID,
            side="BUY",
            price=0.50,
            size=10.0,
            neg_risk=False,
            tick_size=0.01,
            order_type="GTC",
            builder_code=None,
            metadata=None,
        )
        sig_hex = signed.signature.removeprefix("0x")
        # 317 bytes = innerSig(65) + appDomSep(32) + contentsHash(32) +
        #             typeStr(186) + lenBytes(2)
        self.assertEqual(len(sig_hex) // 2, 317)
        self.assertEqual(signed.signatureType, 3)
        self.assertEqual(signed.exchange_version, "v2")
        # maker == signer == DW (POLY_1271 critical invariant)
        self.assertEqual(signed.maker, _TEST_DW)
        self.assertEqual(signed.signer, _TEST_DW)
        # Inner v byte (offset 64 = position 128-130 in hex) ∈ {27, 28}
        v_byte = sig_hex[128:130]
        self.assertIn(v_byte, ("1b", "1c"))

    def test_fak_buy_with_amount_usdc(self):
        """FAK BUY routes through market-order rounding with amount_usdc."""
        signed = build_and_sign_order_v2_dw_ows(
            ows_wallet=self.wallet_name,
            eoa_address=self.eoa,
            deposit_wallet_address=_TEST_DW,
            token_id=_TEST_TOKEN_ID,
            side="BUY",
            price=0.50,
            size=10.0,
            neg_risk=False,
            tick_size=0.01,
            order_type="FAK",
            builder_code=None,
            metadata=None,
            amount_usdc=5.00,
        )
        # amount_usdc=5.00 → makerAmount = 5_000_000 (USDC 6dp)
        self.assertEqual(signed.makerAmount, "5000000")

    def test_tick_rounding_at_entry(self):
        """Codex P1 #1: function must apply round_price_to_tick at entry.
        Submit price=0.123456 with tick=0.01 → effective price=0.12 (or
        tick-aligned). Bypassing this would let CLOB reject for tick
        violations (SIM-1666 class)."""
        signed = build_and_sign_order_v2_dw_ows(
            ows_wallet=self.wallet_name,
            eoa_address=self.eoa,
            deposit_wallet_address=_TEST_DW,
            token_id=_TEST_TOKEN_ID,
            side="BUY",
            price=0.123456,
            size=10.0,
            neg_risk=False,
            tick_size=0.01,
            order_type="GTC",
            builder_code=None,
            metadata=None,
        )
        # GTC BUY: maker = size * price (USDC).
        # 10 * 0.12 = 1.20 → 1_200_000
        # If tick-rounding didn't happen, 10 * 0.123456 = 1.23456 →
        # 1_234_560, which would be a different makerAmount.
        self.assertEqual(signed.makerAmount, "1200000")


@unittest.skipUnless(OWS_AVAILABLE, "open-wallet-standard not installed")
class TestV2DwOwsAddressMismatch(unittest.TestCase):
    """OWS wallet address must match eoa_address argument."""

    def setUp(self):
        self.wallet_name, self.eoa = _make_test_wallet("addr_mismatch")

    def tearDown(self):
        _delete_test_wallet(self.wallet_name)

    def test_wrong_eoa_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            build_and_sign_order_v2_dw_ows(
                ows_wallet=self.wallet_name,
                eoa_address="0x0000000000000000000000000000000000000001",
                deposit_wallet_address=_TEST_DW,
                token_id=_TEST_TOKEN_ID,
                side="BUY",
                price=0.50,
                size=10.0,
                neg_risk=False,
                tick_size=0.01,
                order_type="GTC",
                builder_code=None,
                metadata=None,
            )
        self.assertIn("does not match", str(ctx.exception))


@unittest.skipUnless(OWS_AVAILABLE, "open-wallet-standard not installed")
class TestV2DwOwsSignatureValidity(unittest.TestCase):
    """Inner ECDSA signature is recoverable to the OWS wallet's address.

    Per codex consult v2 P2 #1: do not assert byte-identity vs eth_account
    output (ECDSA nonces may differ between implementations). Test that
    each signature recovers to the expected signer address via ecrecover —
    the actual property the Polymarket CLOB / Solady ECDSA validates.
    """

    def setUp(self):
        self.wallet_name, self.eoa = _make_test_wallet("recover")

    def tearDown(self):
        _delete_test_wallet(self.wallet_name)

    def test_inner_signature_recovers_to_eoa(self):
        from eth_account import Account
        from eth_account.messages import encode_typed_data
        from simmer_sdk.ows_utils import ows_sign_typed_data

        # Sign a minimal EIP-712 message via OWS, then recover the signer
        # via ecrecover. If recovery yields the OWS wallet's EVM address,
        # the signature is valid — the same property Solady ECDSA in the
        # deposit-wallet contract checks.
        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "Foo": [{"name": "bar", "type": "uint256"}],
            },
            "primaryType": "Foo",
            "domain": {
                "name": "Test",
                "version": "1",
                "chainId": "137",
                "verifyingContract": "0x0000000000000000000000000000000000000001",
            },
            "message": {"bar": 42},
        }
        sig_hex = ows_sign_typed_data(self.wallet_name, json.dumps(typed_data))
        signable = encode_typed_data(full_message=typed_data)
        recovered = Account.recover_message(signable, signature=sig_hex)
        self.assertEqual(recovered.lower(), self.eoa.lower())


class TestCoerceTypedDataUintsRecursion(unittest.TestCase):
    """The _coerce_typed_data_uints utility must recursively coerce uints
    inside nested struct fields (codex consult P1 #2). V1 envelopes
    (flat) and V2 envelopes (nested TypedDataSign + Order) must both
    work."""

    def test_v1_flat_message_unchanged(self):
        """V1: primaryType=Order at top of message, no nesting. The
        recursion should produce identical output to the prior top-
        level-only implementation."""
        from simmer_sdk.ows_utils import _coerce_typed_data_uints

        v1 = json.dumps({
            "types": {
                "Order": [
                    {"name": "salt", "type": "uint256"},
                    {"name": "tokenId", "type": "uint256"},
                    {"name": "maker", "type": "address"},
                ],
            },
            "primaryType": "Order",
            "domain": {"name": "test", "chainId": "1"},
            "message": {
                "salt": 12345,
                "tokenId": 71321045679252212594626385532706912750332728571942134274129960822013436447464,
                "maker": "0x0000000000000000000000000000000000000001",
            },
        })
        out = json.loads(_coerce_typed_data_uints(v1))
        self.assertEqual(out["message"]["salt"], "12345")
        self.assertTrue(out["message"]["tokenId"].startswith("0x"))
        # address field untouched
        self.assertEqual(
            out["message"]["maker"],
            "0x0000000000000000000000000000000000000001",
        )

    def test_v2_nested_typed_data_sign_descends(self):
        """V2: primaryType=TypedDataSign with `contents: Order` nested.
        The Order's uint fields must be coerced via recursion."""
        from simmer_sdk.ows_utils import _coerce_typed_data_uints

        v2 = json.dumps({
            "types": {
                "TypedDataSign": [
                    {"name": "contents", "type": "Order"},
                    {"name": "name", "type": "string"},
                    {"name": "salt", "type": "bytes32"},
                ],
                "Order": [
                    {"name": "salt", "type": "uint256"},
                    {"name": "tokenId", "type": "uint256"},
                    {"name": "amount", "type": "uint256"},
                    {"name": "maker", "type": "address"},
                ],
            },
            "primaryType": "TypedDataSign",
            "domain": {"name": "test", "chainId": "1"},
            "message": {
                "contents": {
                    "salt": 99999,
                    "tokenId": 71321045679252212594626385532706912750332728571942134274129960822013436447464,
                    "amount": 5000,
                    "maker": "0x0000000000000000000000000000000000000002",
                },
                "name": "DepositWallet",
                "salt": "0x" + "00" * 32,
            },
        })
        out = json.loads(_coerce_typed_data_uints(v2))
        self.assertEqual(out["message"]["contents"]["salt"], "99999")
        self.assertTrue(out["message"]["contents"]["tokenId"].startswith("0x"))
        self.assertEqual(out["message"]["contents"]["amount"], "5000")
        # Outer message fields not in types[primary] should be untouched.
        self.assertEqual(out["message"]["name"], "DepositWallet")
        self.assertEqual(out["message"]["salt"], "0x" + "00" * 32)

    def test_large_uint_uses_hex_encoding(self):
        """Values > 2^128 require hex encoding (OWS Rust parser
        constraint). Verify both at top level and nested."""
        from simmer_sdk.ows_utils import _coerce_typed_data_uints

        big = (1 << 200)  # well above 2^128

        data = json.dumps({
            "types": {
                "Wrap": [{"name": "inner", "type": "Inner"}],
                "Inner": [{"name": "val", "type": "uint256"}],
            },
            "primaryType": "Wrap",
            "domain": {},
            "message": {"inner": {"val": big}},
        })
        out = json.loads(_coerce_typed_data_uints(data))
        self.assertTrue(out["message"]["inner"]["val"].startswith("0x"))


if __name__ == "__main__":
    unittest.main()
