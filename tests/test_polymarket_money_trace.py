"""Current-market compatibility and Protocol V2 order routing.

The current-market section was captured with PolyNode 0.10.8, before raising
the dependency floor. It pins the signed bytes, not just a second calculation using the same
upgraded dependency. Only the clock and entropy are faked; payload construction,
amount calculation and signing run through the SDK's public signing entry point.
The appended Protocol V2 rows verify ExchangeV3 domains and signer recovery.
Run this file directly to print a candidate trace; review changes before updating
tests/snapshots/polymarket_money_trace.txt.
"""

import json
from dataclasses import asdict, is_dataclass
import socket
from pathlib import Path

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from py_clob_client_v2.order_utils import ExchangeOrderBuilderV2

from test_v2_dw_signing_core_snapshot import (
    _FIXED_DW,
    _FIXED_EOA,
    _FIXED_PRIV,
    _FIXED_TOKEN_ID,
)


SNAPSHOT = Path(__file__).with_name("snapshots") / "polymarket_money_trace.txt"
FIXED_SECONDS = 1716652800
# Synthetic positions for the binary / neg-risk module fixtures. The last byte
# is outcome YES (0), per PolyNode market_protocol.py:148-152. These IDs are
# deliberately distinct from the current-market token; no network lookup.
V2_POSITIONS = {False: str(int("11" * 31 + "00", 16)),
                True: str(int("22" * 31 + "00", 16))}


def _json(value):
    return json.dumps(
        value,
        default=lambda raw: asdict(raw) if is_dataclass(raw) else "0x" + raw.hex(),
        separators=(",", ":"),
    )


def render_money_trace(monkeypatch, protocol_v2=False):
    from simmer_sdk.signing import build_and_sign_order

    def no_network(*args, **kwargs):
        raise AssertionError("Signing must not access the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setenv("SIMMER_POLYMARKET_EXCHANGE_VERSION", "v2")
    monkeypatch.delenv("POLY_BUILDER_CODE", raising=False)
    monkeypatch.setattr("time.time", lambda: FIXED_SECONDS)
    monkeypatch.setattr("time.time_ns", lambda: FIXED_SECONDS * 1_000_000_000)
    monkeypatch.setattr("random.random", lambda: 0.5)

    captured = []
    build_typed_data = ExchangeOrderBuilderV2.build_order_typed_data
    sign_typed_data = Account.sign_typed_data

    def capture_eoa(builder, order):
        typed_data = build_typed_data(builder, order)
        captured.append(typed_data)
        return typed_data

    def capture_dw(private_key, **kwargs):
        captured.append(kwargs["full_message"])
        return sign_typed_data(private_key, **kwargs)

    monkeypatch.setattr(ExchangeOrderBuilderV2, "build_order_typed_data", capture_eoa)
    monkeypatch.setattr(Account, "sign_typed_data", staticmethod(capture_dw))

    lines = [
        "Current markets: April exchange V2 (market protocol v1)",
        "Key: public Hardhat account #0; no network",
        f"Clock: {FIXED_SECONDS} seconds; entropy: random.random() = 0.5",
        "Salt: EOA = clock milliseconds / 2; POLY_1271 = clock milliseconds",
        "Nonce: absent in V2; expiration: 0 (wire only, outside signed struct)",
    ]
    if protocol_v2:
        lines = ["Protocol V2 markets: ExchangeV3 (domain version 3)"]
    for order_type in ("GTC", "FAK"):
        for signature_type in (0, 3):
            for neg_risk in (False, True):
                for side in ("BUY", "SELL"):
                    inputs = dict(
                        wallet_address=_FIXED_EOA,
                        token_id=_FIXED_TOKEN_ID,
                        side=side,
                        price=0.43,
                        size=12.34,
                        neg_risk=neg_risk,
                        signature_type=signature_type,
                        tick_size=0.01,
                        order_type=order_type,
                        deposit_wallet_address=_FIXED_DW if signature_type == 3 else None,
                    )
                    if protocol_v2:
                        from simmer_sdk.polymarket_contracts import MarketProtocol
                        # Distinct from the legacy token ID: routing must replace it.
                        inputs["market_protocol"] = MarketProtocol("v2", V2_POSITIONS[neg_risk])
                    captured.clear()
                    signed = build_and_sign_order(private_key=_FIXED_PRIV, **inputs)
                    assert len(captured) == 1
                    typed_data = captured[0]
                    if protocol_v2:
                        from simmer_sdk.combo_signing import combo_domain
                        assert typed_data["domain"] == combo_domain()
                        assert signed.tokenId == V2_POSITIONS[neg_risk]
                    message = typed_data["message"]
                    order = message["contents"] if signature_type == 3 else message
                    order_data = {
                        "domain": typed_data["domain"],
                        "types": {"Order": typed_data["types"]["Order"]},
                        "primaryType": "Order",
                        "message": order,
                    }
                    encoded_order = encode_typed_data(full_message=order_data)
                    encoded_signed = encode_typed_data(full_message=typed_data)
                    signature = bytes.fromhex(signed.signature[2:])
                    inner_signature = signature[:65]
                    assert Account.recover_message(
                        encoded_signed, signature=inner_signature
                    ) == _FIXED_EOA
                    if protocol_v2:
                        wrong_domain = {**typed_data, "domain": {**typed_data["domain"], "version": "2"}}
                        assert Account.recover_message(
                            encode_typed_data(full_message=wrong_domain), signature=inner_signature
                        ) != _FIXED_EOA
                    if signature_type == 3:
                        assert signature[65:97] == encoded_order.header
                        assert signature[97:129] == encoded_order.body

                    lines.extend([
                        "",
                        f"{order_type} / sig={signature_type} / neg_risk={neg_risk} / {side}"
                        + (f" / module={'neg_risk' if neg_risk else 'binary'}" if protocol_v2 else ""),
                        "inputs: " + _json(inputs),
                        "domain: " + _json(typed_data["domain"]),
                        "Order fields: " + _json(typed_data["types"]["Order"]),
                        "Order: " + _json(order),
                        "Order struct hash: 0x" + encoded_order.body.hex(),
                        "signed primary type: " + typed_data["primaryType"],
                        "signed message: " + _json(message),
                        "signed struct hash: 0x" + encoded_signed.body.hex(),
                        "wire: " + _json(signed.to_dict()),
                    ])
    return "\n".join(lines) + "\n"


def test_current_market_money_trace(monkeypatch):
    assert render_money_trace(monkeypatch) == SNAPSHOT.read_text().split("Protocol V2 markets:")[0]


def test_protocol_v2_money_trace(monkeypatch):
    trace = render_money_trace(monkeypatch, protocol_v2=True)
    assert trace == "Protocol V2 markets:" + SNAPSHOT.read_text().split("Protocol V2 markets:")[1]


if __name__ == "__main__":
    with pytest.MonkeyPatch.context() as patch:
        print(render_money_trace(patch), end="")
    with pytest.MonkeyPatch.context() as patch:
        print(render_money_trace(patch, protocol_v2=True), end="")
