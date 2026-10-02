from simmer_sdk.client import Position, SimmerClient


def _client(monkeypatch):
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OWS_WALLET", raising=False)
    monkeypatch.setattr(
        "simmer_sdk.version_check.check_server_version_compatibility",
        lambda *args, **kwargs: None,
    )
    return SimmerClient(api_key="sk_test", venue="polymarket")


def _position(market_id="m1", *, yes=0.0, no=0.0, venue="polymarket"):
    return Position(
        market_id=market_id,
        question="Question?",
        shares_yes=yes,
        shares_no=no,
        current_value=0.0,
        pnl=0.0,
        status="active",
        venue=venue,
    )


def test_risk_alert_skips_trade_when_position_recheck_finds_no_matching_position(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(client, "get_positions", lambda venue=None: [])

    trades = []
    monkeypatch.setattr(client, "trade", lambda **kwargs: trades.append(kwargs))

    client._process_risk_alerts(alerts=[{
        "market_id": "m1",
        "side": "yes",
        "shares": 10,
        "exit_reason": "stop_loss",
        "venue": "polymarket",
    }])

    assert trades == []


def test_risk_alert_rechecks_position_and_caps_sell_size_to_live_shares(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "get_positions",
        lambda venue=None: [_position(yes=3.25)],
    )

    trades = []
    monkeypatch.setattr(client, "trade", lambda **kwargs: trades.append(kwargs) or {"success": True})
    monkeypatch.setattr(client, "delete_monitor", lambda market_id, side: None)
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {})

    client._process_risk_alerts(alerts=[{
        "market_id": "m1",
        "side": "yes",
        "shares": 10,
        "exit_reason": "stop_loss",
        "venue": "polymarket",
    }])

    assert len(trades) == 1
    assert trades[0]["shares"] == 3.25
    assert trades[0]["venue"] == "polymarket"


def test_risk_alert_skips_non_polymarket_alerts(monkeypatch):
    client = _client(monkeypatch)

    trades = []
    monkeypatch.setattr(client, "trade", lambda **kwargs: trades.append(kwargs))

    client._process_risk_alerts(alerts=[{
        "market_id": "m1",
        "side": "yes",
        "shares": 10,
        "exit_reason": "stop_loss",
        "venue": "sim",
    }])

    assert trades == []


def test_risk_alert_defers_sell_when_order_book_has_no_bids(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "get_positions",
        lambda venue=None: [_position(yes=3.25)],
    )
    monkeypatch.setattr(client, "_polymarket_has_bids", lambda token_id, **kwargs: False)

    trades = []
    monkeypatch.setattr(client, "trade", lambda **kwargs: trades.append(kwargs) or {"success": True})
    monkeypatch.setattr(client, "delete_monitor", lambda market_id, side: None)
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {})

    client._process_risk_alerts(alerts=[{
        "market_id": "m1",
        "side": "yes",
        "shares": 10,
        "exit_reason": "stop_loss",
        "venue": "polymarket",
        "token_id": "tok1",
    }])

    assert trades == []


def test_risk_alert_sells_when_order_book_check_is_unfetchable(monkeypatch):
    client = _client(monkeypatch)
    monkeypatch.setattr(
        client,
        "get_positions",
        lambda venue=None: [_position(yes=3.25)],
    )
    monkeypatch.setattr(client, "_polymarket_has_bids", lambda token_id, **kwargs: None)

    trades = []
    monkeypatch.setattr(client, "trade", lambda **kwargs: trades.append(kwargs) or {"success": True})
    monkeypatch.setattr(client, "delete_monitor", lambda market_id, side: None)
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {})

    client._process_risk_alerts(alerts=[{
        "market_id": "m1",
        "side": "yes",
        "shares": 10,
        "exit_reason": "stop_loss",
        "venue": "polymarket",
        "token_id": "tok1",
    }])

    assert len(trades) == 1


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _patch_book_response(monkeypatch, payload):
    monkeypatch.setattr(
        "simmer_sdk.client.requests.get",
        lambda *args, **kwargs: _FakeResponse(payload),
    )


def test_polymarket_has_bids_true_for_nonempty_bid_list(monkeypatch):
    client = _client(monkeypatch)
    _patch_book_response(monkeypatch, {"bids": [{"price": "0.5", "size": "10"}]})

    assert client._polymarket_has_bids("tok1") is True


def test_polymarket_has_bids_false_for_empty_bid_list(monkeypatch):
    client = _client(monkeypatch)
    _patch_book_response(monkeypatch, {"bids": []})

    assert client._polymarket_has_bids("tok1") is False


def test_polymarket_has_bids_unknown_for_missing_bids_key(monkeypatch):
    client = _client(monkeypatch)
    _patch_book_response(monkeypatch, {"error": "temporarily unavailable"})

    assert client._polymarket_has_bids("tok1") is None


def test_polymarket_has_bids_unknown_for_null_bids(monkeypatch):
    client = _client(monkeypatch)
    _patch_book_response(monkeypatch, {"bids": None})

    assert client._polymarket_has_bids("tok1") is None


def test_polymarket_has_bids_unknown_for_empty_payload(monkeypatch):
    client = _client(monkeypatch)
    _patch_book_response(monkeypatch, {})

    assert client._polymarket_has_bids("tok1") is None
