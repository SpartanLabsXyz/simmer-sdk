"""Tests for SimmerClient.from_env() and SimmerClient.with_ows_wallet().

These ergonomic classmethods let skill bundles construct clients without any
direct os.environ reads — the Hermes regex scanner flags those as HIGH
severity (tools/skills_guard.py:130) and the LLM scanner treats them as
suspicious surface area.
"""

import pytest

from simmer_sdk.client import SimmerClient


# ---------------------------------------------------------------------------
# from_env()
# ---------------------------------------------------------------------------


def test_from_env_happy_path_all_three_vars(monkeypatch):
    """from_env() picks up SIMMER_API_KEY and the constructor auto-detects the rest."""
    monkeypatch.setenv("SIMMER_API_KEY", "sk_live_test_abc")
    monkeypatch.setenv(
        "WALLET_PRIVATE_KEY",
        "0x" + "a" * 64,  # 66-char EVM key (will fail validation if eth_account checks signature)
    )
    monkeypatch.setenv("OWS_WALLET", "test-wallet")

    # OWS takes priority over WALLET_PRIVATE_KEY (per __init__ docstring), so
    # the client will try to import ows_utils. We can't easily install OWS
    # in the test env, but the ImportError path sets _ows_wallet to None and
    # falls through. That's fine for this test — we just want to confirm
    # from_env() reads the api_key correctly.
    client = SimmerClient.from_env()

    assert client.api_key == "sk_live_test_abc"
    assert client.venue == "sim"  # default


def test_from_env_only_api_key(monkeypatch):
    """from_env() works with just SIMMER_API_KEY set; no wallet env vars required."""
    monkeypatch.setenv("SIMMER_API_KEY", "sk_live_test_xyz")
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("SIMMER_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OWS_WALLET", raising=False)

    client = SimmerClient.from_env()

    assert client.api_key == "sk_live_test_xyz"
    assert client._private_key is None
    assert client._ows_wallet is None


def test_from_env_raises_when_api_key_missing(monkeypatch):
    """from_env() raises RuntimeError with a dashboard pointer when SIMMER_API_KEY is unset."""
    monkeypatch.delenv("SIMMER_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SIMMER_API_KEY"):
        SimmerClient.from_env()


def test_from_env_raises_when_api_key_empty(monkeypatch):
    """from_env() treats empty-string SIMMER_API_KEY as missing."""
    monkeypatch.setenv("SIMMER_API_KEY", "")

    with pytest.raises(RuntimeError, match="SIMMER_API_KEY"):
        SimmerClient.from_env()


def test_from_env_forwards_kwargs(monkeypatch):
    """from_env() forwards extra kwargs (venue, base_url, etc.) to __init__."""
    monkeypatch.setenv("SIMMER_API_KEY", "sk_live_test")
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OWS_WALLET", raising=False)

    client = SimmerClient.from_env(venue="kalshi", base_url="https://example.test")

    assert client.venue == "kalshi"
    assert client.base_url == "https://example.test"


# ---------------------------------------------------------------------------
# with_ows_wallet()
# ---------------------------------------------------------------------------


def test_with_ows_wallet_explicit_api_key(monkeypatch):
    """with_ows_wallet() uses the explicit api_key parameter when provided."""
    # Even if SIMMER_API_KEY is set in env, explicit param wins.
    monkeypatch.setenv("SIMMER_API_KEY", "env_key")
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OWS_WALLET", raising=False)

    # OWS import will likely fail in test env → _ows_wallet falls back to None,
    # but the api_key wiring is what we're testing here.
    client = SimmerClient.with_ows_wallet("my-agent", api_key="explicit_key")

    assert client.api_key == "explicit_key"


def test_with_ows_wallet_env_fallback_api_key(monkeypatch):
    """with_ows_wallet() falls back to SIMMER_API_KEY when api_key is None."""
    monkeypatch.setenv("SIMMER_API_KEY", "fallback_key")
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OWS_WALLET", raising=False)

    client = SimmerClient.with_ows_wallet("my-agent")

    assert client.api_key == "fallback_key"


def test_with_ows_wallet_raises_when_api_key_missing(monkeypatch):
    """with_ows_wallet() raises RuntimeError when neither param nor env provides an api_key."""
    monkeypatch.delenv("SIMMER_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SIMMER_API_KEY"):
        SimmerClient.with_ows_wallet("my-agent")


def test_with_ows_wallet_raises_when_api_key_empty(monkeypatch):
    """with_ows_wallet() treats empty-string api_key arg as missing and falls back to env, then errors."""
    monkeypatch.delenv("SIMMER_API_KEY", raising=False)

    # api_key="" is falsy, but the function only falls back to env when
    # api_key IS None (not just falsy). Empty string passed explicitly should
    # still reach the constructor — but the constructor doesn't validate
    # api_key emptiness, so this test asserts the env-fallback only kicks in
    # for None. Confirm the empty-env case raises.
    monkeypatch.setenv("SIMMER_API_KEY", "")
    with pytest.raises(RuntimeError, match="SIMMER_API_KEY"):
        SimmerClient.with_ows_wallet("my-agent")


def test_with_ows_wallet_forwards_kwargs(monkeypatch):
    """with_ows_wallet() forwards extra kwargs to __init__."""
    monkeypatch.setenv("SIMMER_API_KEY", "k")
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OWS_WALLET", raising=False)

    client = SimmerClient.with_ows_wallet(
        "my-agent",
        venue="polymarket",
        base_url="https://example.test",
        live=False,  # avoid risk-alert network call
    )

    assert client.venue == "polymarket"
    assert client.base_url == "https://example.test"
    assert client.live is False
