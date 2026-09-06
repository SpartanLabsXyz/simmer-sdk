"""Client-level wiring tests for the hyperliquid venue."""

import os

import pytest

from simmer_sdk.client import SimmerClient

TEST_KEY = "0x" + "33" * 32
MAIN_ADDR = "0x" + "cd" * 20


def test_venue_registered():
    assert "hyperliquid" in SimmerClient.VENUES


def test_hyperliquid_property_builds_adapter(monkeypatch):
    pytest.importorskip("hyperliquid", reason="requires the [hyperliquid] extra")
    monkeypatch.setenv("WALLET_PRIVATE_KEY", TEST_KEY)
    monkeypatch.delenv("OWS_WALLET", raising=False)
    c = SimmerClient(api_key="test", private_key=TEST_KEY)
    venue = c.hyperliquid
    assert venue.venue == "hyperliquid"
    assert venue.is_mainnet is True
    # cached
    assert c.hyperliquid is venue


def test_hyperliquid_from_env_uses_wallet_private_key(monkeypatch):
    monkeypatch.setenv("SIMMER_API_KEY", "test")
    monkeypatch.setenv("WALLET_PRIVATE_KEY", TEST_KEY)
    monkeypatch.delenv("OWS_WALLET", raising=False)
    monkeypatch.delenv("SIMMER_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(
        "simmer_sdk.version_check.check_server_version_compatibility",
        lambda *args, **kwargs: None,
    )

    c = SimmerClient.from_env(venue="hyperliquid")

    assert c._private_key == TEST_KEY


def test_hyperliquid_testnet_env(monkeypatch):
    pytest.importorskip("hyperliquid", reason="requires the [hyperliquid] extra")
    monkeypatch.setenv("SIMMER_HYPERLIQUID_TESTNET", "1")
    c = SimmerClient(api_key="test", private_key=TEST_KEY)
    assert c.hyperliquid.is_mainnet is False


def test_hyperliquid_main_address_env(monkeypatch):
    pytest.importorskip("hyperliquid", reason="requires the [hyperliquid] extra")
    monkeypatch.setenv("SIMMER_HYPERLIQUID_MAIN_ADDRESS", MAIN_ADDR)
    c = SimmerClient(api_key="test", private_key=TEST_KEY)
    assert c.hyperliquid.address == MAIN_ADDR
    assert c.hyperliquid.signer_address != MAIN_ADDR


def test_hyperliquid_property_without_signer_raises(monkeypatch):
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OWS_WALLET", raising=False)
    monkeypatch.delenv("SIMMER_PRIVATE_KEY", raising=False)
    c = SimmerClient(api_key="test")
    with pytest.raises(ValueError, match="requires a signer"):
        _ = c.hyperliquid


def test_unified_trade_hyperliquid_guarded(monkeypatch):
    """trade(venue='hyperliquid') must NOT fall through to /api/sdk/trade —
    it raises until the server fill-recording endpoint lands."""
    monkeypatch.setenv("WALLET_PRIVATE_KEY", TEST_KEY)
    c = SimmerClient(api_key="test", private_key=TEST_KEY)
    with pytest.raises(NotImplementedError, match="client.hyperliquid.place_order"):
        c.trade(market_id="m1", side="yes", amount=5.0, venue="hyperliquid")


def _offline(monkeypatch, posts, signs):
    """Sign without the [hyperliquid] extra and record /exchange and /info
    POSTs instead of sending them, so the gate tests run in CI (which
    installs no extras). ``signs`` records every signer call."""
    import simmer_sdk.hyperliquid_signing as hs
    import simmer_sdk.hyperliquid_venue as hv

    monkeypatch.setattr(
        hs.RawKeyHyperliquidSigner, "sign_l1_action",
        lambda self, *a, **k: signs.append("l1") or {"r": "0x0"},
    )
    monkeypatch.setattr(
        hs.RawKeyHyperliquidSigner, "sign_user_action",
        lambda self, *a, **k: signs.append("user") or {"r": "0x0"},
    )
    monkeypatch.setattr(hv, "build_order_action", lambda *a, **k: {"type": "order"})
    monkeypatch.setattr(
        hv.HyperliquidVenue, "_post",
        lambda self, path, body: posts.append(path) or {"status": "ok", "response": {}, "assetPositions": [], "marginSummary": {}},
    )


def _gated_client(monkeypatch, posts, signs, **kwargs):
    monkeypatch.setenv("WALLET_PRIVATE_KEY", TEST_KEY)
    monkeypatch.delenv("OWS_WALLET", raising=False)
    _offline(monkeypatch, posts, signs)
    if kwargs.pop("readonly", False):
        return SimmerClient.readonly(api_key="test", private_key=TEST_KEY, **kwargs)
    return SimmerClient(api_key="test", private_key=TEST_KEY, **kwargs)


def _assert_refuses_every_submission(c, posts, signs, match):
    """Every signed path refuses before the signer runs and before any POST;
    submit_action refuses on its own for callers that build actions elsewhere."""
    with pytest.raises(RuntimeError, match=match):
        c.hyperliquid.place_order(size=1.0, limit_px=0.5, is_buy=True, outcome_id=1)
    with pytest.raises(RuntimeError, match=match):
        c.hyperliquid.cancel_order(order_id=1, outcome_id=1)
    with pytest.raises(RuntimeError, match=match):
        c.hyperliquid.approve_agent("0x" + "ab" * 20)
    with pytest.raises(RuntimeError, match=match):
        c.hyperliquid.submit_action({"type": "order"}, {"r": "0x0"}, 1)
    assert posts == []
    assert signs == []


def test_paper_client_hyperliquid_refuses_submission(monkeypatch):
    """live=False routes trade() to paper; the HL adapter signs and submits
    locally, so it must carry the same gate or a paper client moves real funds."""
    posts, signs = [], []
    c = _gated_client(monkeypatch, posts, signs, live=False)
    _assert_refuses_every_submission(c, posts, signs, match="live=False")


def test_readonly_client_hyperliquid_refuses_submission(monkeypatch):
    posts, signs = [], []
    c = _gated_client(monkeypatch, posts, signs, readonly=True)
    _assert_refuses_every_submission(c, posts, signs, match="readonly")


def test_paper_client_hyperliquid_reads_still_work(monkeypatch):
    """The gate is on submission only: a paper client can still read HL state."""
    posts, signs = [], []
    c = _gated_client(monkeypatch, posts, signs, live=False)
    c.hyperliquid.get_positions()
    c.hyperliquid.get_balances()
    assert posts == ["/info", "/info"]
    assert signs == []


def test_live_client_hyperliquid_submits(monkeypatch):
    """The gate must not catch the live client: submission signs and reaches /exchange."""
    posts, signs = [], []
    c = _gated_client(monkeypatch, posts, signs)
    c.hyperliquid.place_order(size=1.0, limit_px=0.5, is_buy=True, outcome_id=1)
    assert posts == ["/exchange"]
    assert signs == ["l1"]
