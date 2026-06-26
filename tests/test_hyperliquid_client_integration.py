"""Client-level wiring tests for the hyperliquid venue."""

import os

import pytest

from simmer_sdk.client import SimmerClient

TEST_KEY = "0x" + "33" * 32


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
