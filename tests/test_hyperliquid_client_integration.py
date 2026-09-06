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


def _offline(monkeypatch, posts):
    """Sign without the [hyperliquid] extra and record /exchange POSTs instead
    of sending them, so the gate tests run in CI (which installs no extras)."""
    import simmer_sdk.hyperliquid_signing as hs
    import simmer_sdk.hyperliquid_venue as hv

    monkeypatch.setattr(
        hs.RawKeyHyperliquidSigner, "sign_l1_action", lambda self, *a, **k: {"r": "0x0"}
    )
    monkeypatch.setattr(hv, "build_order_action", lambda *a, **k: {"type": "order"})
    monkeypatch.setattr(
        hv.HyperliquidVenue, "_post",
        lambda self, path, body: posts.append(path) or {"status": "ok", "response": {}},
    )


def test_paper_client_hyperliquid_refuses_submission(monkeypatch):
    """live=False routes trade() to paper; the HL adapter signs and submits
    locally, so it must carry the same gate or a paper client moves real funds."""
    monkeypatch.setenv("WALLET_PRIVATE_KEY", TEST_KEY)
    monkeypatch.delenv("OWS_WALLET", raising=False)
    posts = []
    _offline(monkeypatch, posts)
    c = SimmerClient(api_key="test", private_key=TEST_KEY, live=False)
    with pytest.raises(RuntimeError, match="live=False"):
        c.hyperliquid.place_order(size=1.0, limit_px=0.5, is_buy=True, outcome_id=1)
    with pytest.raises(RuntimeError, match="live=False"):
        c.hyperliquid.cancel_order(order_id=1, outcome_id=1)
    assert posts == []


def test_readonly_client_hyperliquid_refuses_submission(monkeypatch):
    monkeypatch.setenv("WALLET_PRIVATE_KEY", TEST_KEY)
    monkeypatch.delenv("OWS_WALLET", raising=False)
    posts = []
    _offline(monkeypatch, posts)
    c = SimmerClient.readonly(api_key="test", private_key=TEST_KEY)
    with pytest.raises(RuntimeError, match="readonly"):
        c.hyperliquid.place_order(size=1.0, limit_px=0.5, is_buy=True, outcome_id=1)
    assert posts == []


def test_live_client_hyperliquid_submits(monkeypatch):
    """The gate must not catch the live client: submission reaches _post."""
    monkeypatch.setenv("WALLET_PRIVATE_KEY", TEST_KEY)
    monkeypatch.delenv("OWS_WALLET", raising=False)
    posts = []
    _offline(monkeypatch, posts)
    c = SimmerClient(api_key="test", private_key=TEST_KEY)
    c.hyperliquid.place_order(size=1.0, limit_px=0.5, is_buy=True, outcome_id=1)
    assert posts == ["/exchange"]
