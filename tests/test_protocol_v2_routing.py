"""Offline Protocol V2 routing and approval boundary tests."""

from dataclasses import FrozenInstanceError
from unittest.mock import Mock
import socket

import pytest
from polynode.market_protocol import protocol_v2_position_ids

from simmer_sdk.client import SimmerClient
from test_v2_dw_signing_core_snapshot import _FIXED_EOA, _FIXED_PRIV, _FIXED_DW, _FIXED_TOKEN_ID

YES_POSITION, NO_POSITION = protocol_v2_position_ids("0x" + "11" * 31)
_, OTHER_NO_POSITION = protocol_v2_position_ids("0x" + "22" * 31)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Protocol routing tests must stay offline")
    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket.socket, "connect_ex", reject)


@pytest.mark.parametrize("version,position_id", [
    ("v2", None), ("v1", "123"), ("v3", None), (None, None),
    ("v2", ""), ("v2", "-1"), ("v2", "01"), ("v2", "0x12"),
    ("v2", 123), ("v2", str(2**256)), ("v2", "١٢"),
    ("v2", str(int(YES_POSITION) + 2)),
    ("v2", str(int(YES_POSITION) + 255)),
])
def test_protocol_guards(version, position_id):
    from simmer_sdk.polymarket_contracts import MarketProtocol
    with pytest.raises(ValueError):
        MarketProtocol(version, position_id)


def test_protocol_is_immutable_and_defaults_to_current():
    from simmer_sdk.polymarket_contracts import MarketProtocol
    assert MarketProtocol().version == "v1"
    protocol = MarketProtocol("v2", YES_POSITION)
    with pytest.raises(FrozenInstanceError):
        protocol.position_id = None


def make_client():
    client = object.__new__(SimmerClient)
    client._private_key = _FIXED_PRIV
    client._wallet_address = _FIXED_EOA
    client._ows_wallet = None
    client._market_data_cache = {}
    client._uses_deposit_wallet = False
    client._deposit_wallet_address = None
    return client


def market_data():
    return {
        "id": "market", "question": "Test market?",
        "polymarket_token_id": _FIXED_TOKEN_ID,
        "polymarket_no_token_id": "456",
        "polymarket_protocol_version": "v2",
        "polymarket_position_id": YES_POSITION,
        "polymarket_no_position_id": NO_POSITION,
    }


def mock_approval_requests(client, status):
    def request(method, path, **kwargs):
        assert method == "GET"
        if path == "/api/sdk/agents/me":
            return {"wallet_uses_deposit_wallet": client._uses_deposit_wallet,
                    "deposit_wallet_address": client._deposit_wallet_address}
        assert path.startswith("/api/polymarket/allowances/")
        return status
    client._request = Mock(side_effect=request)


@pytest.mark.parametrize("side,position_id", [("yes", YES_POSITION), ("no", NO_POSITION)])
@pytest.mark.parametrize("dw", [False, True])
def test_client_routes_server_metadata(monkeypatch, side, position_id, dw):
    monkeypatch.setenv("SIMMER_POLYMARKET_EXCHANGE_VERSION", "v2")
    client = make_client()
    client._uses_deposit_wallet = dw
    client._deposit_wallet_address = _FIXED_DW if dw else None
    data = market_data()
    # V2 markets need only position IDs, not legacy CTF token IDs.
    data.pop("polymarket_token_id")
    data.pop("polymarket_no_token_id")
    client._request = Mock(return_value={"market": data})
    signed = client._build_signed_order("market", side, amount=10, price=0.43)
    assert signed["tokenId"] == position_id
    assert signed["signatureType"] == (3 if dw else 0)
    client._request.assert_called_once_with("GET", "/api/sdk/markets/market")
    market = client._parse_market(data)
    assert market.polymarket_yes_protocol.position_id == YES_POSITION
    assert market.polymarket_no_protocol.position_id == NO_POSITION


@pytest.mark.parametrize("dw", [False, True])
@pytest.mark.parametrize("side", ["yes", "no"])
@pytest.mark.parametrize("yes,no,reason", [
    (NO_POSITION, YES_POSITION, "outcome"),
    (YES_POSITION, YES_POSITION, "outcome"),
    (NO_POSITION, NO_POSITION, "outcome"),
    (YES_POSITION, OTHER_NO_POSITION, "condition"),
    (str(int(YES_POSITION) + 2), NO_POSITION, "outcome"),
    (YES_POSITION, str(int(NO_POSITION) + 254), "outcome"),
    (NO_POSITION, None, "outcome"),
    (None, YES_POSITION, "outcome"),
])
def test_invalid_positions_rejected_before_signing(monkeypatch, dw, side, yes, no, reason):
    client = make_client()
    client._uses_deposit_wallet = dw
    client._deposit_wallet_address = _FIXED_DW if dw else None
    data = {**market_data(), "polymarket_position_id": yes, "polymarket_no_position_id": no}
    client._request = Mock(return_value={"market": data})
    sign = Mock(side_effect=AssertionError("must reject before signing"))
    monkeypatch.setattr("simmer_sdk.signing.build_and_sign_order", sign)
    # If the requested outcome is absent, the existing required-ID guard applies.
    error = "position_id" if (yes if side == "yes" else no) is None else reason
    with pytest.raises(ValueError, match=error):
        client._build_signed_order("market", side, amount=10, price=0.43)
    sign.assert_not_called()


def test_incomplete_server_metadata_fails_before_signing(monkeypatch):
    client = make_client()
    data = market_data()
    data.pop("polymarket_position_id")
    client._request = Mock(return_value={"market": data})
    sign = Mock(side_effect=AssertionError("must reject before signing"))
    monkeypatch.setattr("simmer_sdk.signing.build_and_sign_order", sign)
    with pytest.raises(ValueError, match="position_id"):
        client._build_signed_order("market", "yes", amount=10, price=0.43)
    sign.assert_not_called()


def test_protocol_v2_rejects_legacy_exchange(monkeypatch):
    from simmer_sdk.polymarket_contracts import MarketProtocol
    from simmer_sdk.signing import build_and_sign_order
    monkeypatch.setenv("SIMMER_POLYMARKET_EXCHANGE_VERSION", "v1")
    with pytest.raises(ValueError, match="Protocol V2"):
        build_and_sign_order(_FIXED_PRIV, _FIXED_EOA, _FIXED_TOKEN_ID,
                             "BUY", 0.43, 12.34, market_protocol=MarketProtocol("v2", YES_POSITION))


def test_protocol_v2_approval_transactions_match_polynode(monkeypatch):
    from polynode.trading.onboarding import build_protocol_v2_order_approval_txns
    from simmer_sdk.approvals import get_approval_transactions, get_required_approvals
    monkeypatch.setenv("SIMMER_POLYMARKET_EXCHANGE_VERSION", "v2")
    old = get_approval_transactions()
    txs = get_approval_transactions(protocol_v2=True)
    assert txs[:len(old)] == old
    expected = build_protocol_v2_order_approval_txns()
    # Addresses are case-insensitive; compare full calldata and native value exactly.
    assert [{"to": t["to"].lower(), "data": t["data"], "value": int(t["value"], 16)}
            for t in txs[len(old):]] == [{**t, "to": t["to"].lower()} for t in expected]
    required = get_required_approvals(protocol_v2=True)
    assert len(required) == len(txs)
    assert required[-1]["token"] == "PositionManager"
    assert required[-1]["token_address"] == expected[-1]["to"]


@pytest.mark.parametrize("pusd,position,ready", [(False, False, False), (True, False, False),
                                                 (False, True, False), (True, True, True)])
def test_protocol_v2_approval_checks_do_not_trust_legacy_all_set(monkeypatch, pusd, position, ready):
    from simmer_sdk.polymarket_contracts import COMBO_EXCHANGE
    monkeypatch.setenv("SIMMER_POLYMARKET_EXCHANGE_VERSION", "v2")
    client = make_client()
    key = COMBO_EXCHANGE.lower()
    from simmer_sdk.approvals import get_approval_transactions
    legacy = {
        f"{'pusd' if tx['token'] == 'pUSD' else 'ctf'}_{tx['spender_address'].lower()}": True
        for tx in get_approval_transactions()
    }
    status = {"all_set": True, "allowances": {
        **legacy,
        f"pusd_{key}": pusd, f"position_manager_{key}": position,
    }}
    mock_approval_requests(client, status)
    result = client.ensure_approvals(protocol_v2=True)
    assert result["ready"] is ready
    assert len(result["missing_transactions"]) == int(not pusd) + int(not position)
    assert ("PositionManager" in result["guide"]) is (not position)
    assert "protocol_v2=1" in client._request.call_args.args[1]
    assert status["all_set"] is True  # Do not mutate the server response.


def test_current_server_metadata_defaults_to_current_protocol():
    client = make_client()
    market = client._parse_market({"id": "current", "question": "Current market?"})
    assert market.polymarket_yes_protocol.version == "v1"
    assert market.polymarket_no_protocol.position_id is None


def test_ows_dw_uses_same_protocol_as_raw_key(monkeypatch):
    from eth_account import Account
    from simmer_sdk.polymarket_contracts import MarketProtocol
    from simmer_sdk.signing import build_and_sign_order, build_and_sign_order_v2_dw_ows
    import json

    monkeypatch.setenv("SIMMER_POLYMARKET_EXCHANGE_VERSION", "v2")
    monkeypatch.setattr("time.time", lambda: 1716652800)
    monkeypatch.setattr("simmer_sdk.ows_utils.get_ows_wallet_address", lambda _: _FIXED_EOA)
    monkeypatch.setattr("simmer_sdk.ows_utils.ows_sign_typed_data", lambda wallet, data:
                        "0x" + Account.sign_typed_data(_FIXED_PRIV, full_message=json.loads(data)).signature.hex())
    inputs = dict(token_id=_FIXED_TOKEN_ID, side="BUY", price=0.43, size=12.34,
                  neg_risk=True, tick_size=0.01, order_type="GTC", builder_code=None,
                  metadata=None, deposit_wallet_address=_FIXED_DW,
                  market_protocol=MarketProtocol("v2", YES_POSITION))
    raw = build_and_sign_order(_FIXED_PRIV, _FIXED_EOA, signature_type=3, **inputs)
    ows = build_and_sign_order_v2_dw_ows("test-wallet", _FIXED_EOA, **inputs)
    assert ows.to_dict() == raw.to_dict()
    assert ows.tokenId == YES_POSITION


def test_ows_eoa_rejects_protocol_v2_before_signing(monkeypatch):
    client = make_client()
    client._ows_wallet = "test-wallet"
    client._request = Mock(return_value={"market": market_data()})
    sign = Mock(side_effect=AssertionError("must reject legacy-only signer"))
    monkeypatch.setattr("simmer_sdk.signing.build_and_sign_order_ows", sign)
    with pytest.raises(ValueError, match="OWS EOA"):
        client._build_signed_order("market", "yes", amount=10, price=0.43)
    sign.assert_not_called()


def test_protocol_v2_approvals_check_deposit_wallet_holder(monkeypatch):
    monkeypatch.setenv("SIMMER_POLYMARKET_EXCHANGE_VERSION", "v2")
    client = make_client()
    client._uses_deposit_wallet = True
    client._deposit_wallet_address = _FIXED_DW
    mock_approval_requests(client, {"all_set": True, "allowances": {}})
    result = client.ensure_approvals(protocol_v2=True)
    assert result["ready"] is False
    assert _FIXED_DW in client._request.call_args.args[1]
    assert "dashboard" in result["guide"]


def test_protocol_v2_set_approvals_preserves_dw_activation_path():
    client = make_client()
    client._uses_deposit_wallet = True
    client._deposit_wallet_address = _FIXED_DW
    client.live = True
    client._live_explicit = True
    mock_approval_requests(client, {})
    result = client.set_approvals(protocol_v2=True)
    assert result["deposit_wallet_user"] is True
    assert result["set"] == result["failed"] == 0
    client._request.assert_called_once_with("GET", "/api/sdk/agents/me")


def test_protocol_v2_managed_readiness_is_not_assumed():
    client = make_client()
    client._wallet_address = None
    client._probe_managed_wallet = Mock(return_value={"wallet_ownership": "native"})
    result = client.ensure_approvals(protocol_v2=True)
    assert result["ready"] is False
    assert result["managed"] is True
    assert "dashboard" in result["guide"]


def test_protocol_v2_direct_approval_check_uses_dw():
    client = make_client()
    client._uses_deposit_wallet = True
    client._deposit_wallet_address = _FIXED_DW
    mock_approval_requests(client, {"all_set": True, "allowances": {}})
    assert client.check_approvals(protocol_v2=True)["all_set"] is False
    assert _FIXED_DW in client._request.call_args.args[1]


def test_protocol_v2_approval_check_requires_dw_address():
    client = make_client()
    client._uses_deposit_wallet = True
    mock_approval_requests(client, {})
    with pytest.raises(ValueError, match="deposit_wallet_address"):
        client.ensure_approvals(protocol_v2=True)
    client._request.assert_called_once_with("GET", "/api/sdk/agents/me")


@pytest.fixture
def fresh_approval_client(monkeypatch):
    monkeypatch.setenv("SIMMER_POLYMARKET_EXCHANGE_VERSION", "v2")
    return SimmerClient(api_key="sk_test_...", private_key=_FIXED_PRIV,
                        live=True, process_risk_alerts=False, _ignore_env_wallets=True)


def approval_status(ready):
    from simmer_sdk.approvals import get_required_approvals
    prefixes = {"pUSD": "pusd", "USDC.e": "usdc", "CTF": "ctf",
                "PositionManager": "position_manager"}
    return {"all_set": ready, "allowances": {
        f"{prefixes[approval['token']]}_{approval['spender_address'].lower()}": ready
        for approval in get_required_approvals(protocol_v2=True)
    }}


def mock_fresh_approval_requests(client, per_agent, *, eoa_ready=True, dw_active=True):
    # Conflicting parent state proves that per-agent identity takes precedence.
    me = {"wallet_uses_deposit_wallet": dw_active,
          "deposit_wallet_address": _FIXED_DW}
    if per_agent:
        me.update(wallet_uses_deposit_wallet=not dw_active,
                  deposit_wallet_address=_FIXED_EOA,
                  per_agent_wallet_address=_FIXED_EOA,
                  per_agent_dw_active=dw_active,
                  per_agent_deposit_wallet_address=_FIXED_DW)

    def request(method, path, **kwargs):
        if method == "GET" and path == "/api/sdk/agents/me":
            return me
        if method == "GET" and path.startswith(f"/api/polymarket/allowances/{_FIXED_EOA}?"):
            return approval_status(eoa_ready)
        if method == "GET" and path.startswith(f"/api/polymarket/allowances/{_FIXED_DW}?"):
            return approval_status(False)
        if method == "POST" and path == "/api/rpc/polygon":
            # Offline preflight values let the broken setter reach its signer.
            return {"result": "0xde0b6b3a7640000"}
        raise AssertionError(f"Unexpected request: {method} {path}")

    client._request = Mock(side_effect=request)
    client._sign_eip1559_tx_for_broadcast = Mock(
        side_effect=lambda *args: pytest.fail("must not sign EOA approvals for a DW account"))


@pytest.mark.parametrize("per_agent", [False, True])
@pytest.mark.parametrize("method", ["ensure_approvals", "check_approvals"])
def test_fresh_v2_approvals_use_active_dw(fresh_approval_client, per_agent, method):
    client = fresh_approval_client
    mock_fresh_approval_requests(client, per_agent)
    result = getattr(client, method)(protocol_v2=True)
    assert result["ready" if method == "ensure_approvals" else "all_set"] is False
    assert _FIXED_DW in client._request.call_args.args[1]
    assert all(call.args[0] == "GET" for call in client._request.call_args_list)
    client._sign_eip1559_tx_for_broadcast.assert_not_called()


@pytest.mark.parametrize("per_agent", [False, True])
@pytest.mark.parametrize("eoa_ready", [False, True])
def test_fresh_v2_set_approvals_never_signs_for_dw(fresh_approval_client, per_agent, eoa_ready):
    client = fresh_approval_client
    mock_fresh_approval_requests(client, per_agent, eoa_ready=eoa_ready)
    result = client.set_approvals(protocol_v2=True)
    assert result["deposit_wallet_user"] is True
    assert result["set"] == result["failed"] == 0
    client._sign_eip1559_tx_for_broadcast.assert_not_called()
    client._request.assert_called_once_with("GET", "/api/sdk/agents/me")


@pytest.mark.parametrize("per_agent", [False, True])
@pytest.mark.parametrize("method", ["ensure_approvals", "set_approvals"])
def test_fresh_v2_inactive_dw_checks_eoa(fresh_approval_client, per_agent, method):
    client = fresh_approval_client
    mock_fresh_approval_requests(client, per_agent, dw_active=False)
    result = getattr(client, method)(protocol_v2=True)
    if method == "ensure_approvals":
        assert result["ready"] is True
    else:
        from simmer_sdk.approvals import get_required_approvals
        assert result == {"set": 0, "failed": 0, "details": [],
                          "skipped": len(get_required_approvals(protocol_v2=True))}
    assert _FIXED_EOA in client._request.call_args.args[1]
    assert client._request.call_count == 2
    client._sign_eip1559_tx_for_broadcast.assert_not_called()


@pytest.mark.parametrize("method", ["ensure_approvals", "check_approvals", "set_approvals"])
@pytest.mark.parametrize("identity", [{}, {"per_agent_wallet_address": _FIXED_EOA}])
def test_v2_approvals_require_known_funding_wallet(fresh_approval_client, method, identity):
    client = fresh_approval_client
    client._request = Mock(return_value=identity)
    client._sign_eip1559_tx_for_broadcast = Mock(
        side_effect=lambda *args: pytest.fail("must resolve identity before signing"))
    with pytest.raises(ValueError, match="funding wallet"):
        getattr(client, method)(protocol_v2=True)
    client._sign_eip1559_tx_for_broadcast.assert_not_called()
    client._request.assert_called_once_with("GET", "/api/sdk/agents/me")


def test_v2_discovery_failure_cannot_cache_eoa_fallback(fresh_approval_client):
    client = fresh_approval_client
    client._request = Mock(side_effect=RuntimeError("identity unavailable"))
    with pytest.raises(RuntimeError, match="identity unavailable"):
        client.ensure_approvals(protocol_v2=True)
    mock_fresh_approval_requests(client, per_agent=True)
    assert client.ensure_approvals(protocol_v2=True)["ready"] is False
    assert _FIXED_DW in client._request.call_args.args[1]


def test_v2_explicit_approval_address_needs_no_discovery(fresh_approval_client):
    client = fresh_approval_client
    client._request = Mock(return_value=approval_status(True))
    assert client.check_approvals(address=_FIXED_EOA, protocol_v2=True)["all_set"] is True
    client._request.assert_called_once_with(
        "GET", f"/api/polymarket/allowances/{_FIXED_EOA}?protocol_v2=1", timeout=None)
