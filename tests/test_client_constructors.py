"""Tests for SimmerClient.from_env() and SimmerClient.with_ows_wallet().

These ergonomic classmethods let skill bundles construct clients without any
direct os.environ reads — the Hermes regex scanner flags those as HIGH
severity (tools/skills_guard.py:130) and the LLM scanner treats them as
suspicious surface area.
"""

import pytest
import warnings
import logging

from simmer_sdk.client import SimmerClient


TEST_OWS_ADDRESS = "0xABCD1234abcd1234abcd1234abcd1234abcd1234"


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


def test_from_env_sim_read_only_ignores_wallet_env_without_warnings(monkeypatch, caplog):
    """Default SIM/read-only construction should not warn about unused wallets."""
    monkeypatch.setenv("SIMMER_API_KEY", "sk_live_test_quiet")
    monkeypatch.setenv("OWS_WALLET", "missing-ows-wallet")
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "0x" + "a" * 64)
    monkeypatch.setenv("SIMMER_PRIVATE_KEY", "0x" + "b" * 64)
    monkeypatch.setenv("SOLANA_PRIVATE_KEY", "not-a-solana-key")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        client = SimmerClient.from_env()

    assert client.venue == "sim"
    assert client._ows_wallet is None
    assert client._private_key is None
    assert caught == []
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_live_venue_construction_still_warns_real_funds(monkeypatch, caplog):
    """Noise reduction for SIM clients must not hide live real-funds warnings."""
    monkeypatch.delenv("OWS_WALLET", raising=False)
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("SIMMER_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(
        "simmer_sdk.version_check.check_server_version_compatibility",
        lambda *args, **kwargs: None,
    )

    with caplog.at_level(logging.WARNING, logger="simmer_sdk.client"):
        client = SimmerClient(api_key="sk_live_test_live", venue="polymarket")

    assert client.venue == "polymarket"
    assert client.live is True
    assert any("LIVE trading with real funds" in r.getMessage() for r in caplog.records)


def test_paper_real_venue_construction_does_not_warn_live(monkeypatch, caplog):
    """venue='polymarket' + live=False is paper, not a live-funds warning.

    The warn used to key off the venue string alone, so a paper client
    printed ``LIVE trading with real funds`` (issue #345).
    """
    monkeypatch.delenv("OWS_WALLET", raising=False)
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("SIMMER_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(
        "simmer_sdk.version_check.check_server_version_compatibility",
        lambda *args, **kwargs: None,
    )

    with caplog.at_level(logging.WARNING, logger="simmer_sdk.client"):
        client = SimmerClient(
            api_key="sk_live_test_paper",
            venue="polymarket",
            live=False,
        )

    assert client.venue == "polymarket"
    assert client.live is False
    assert not any("LIVE trading with real funds" in r.getMessage() for r in caplog.records)


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
# readonly()
# ---------------------------------------------------------------------------


def test_readonly_skips_constructor_risk_exits_for_live_polymarket_wallet(monkeypatch):
    """readonly() construction must not fetch/process pending risk exits."""
    monkeypatch.delenv("OWS_WALLET", raising=False)
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "0x" + "a" * 64)
    monkeypatch.setattr(
        "simmer_sdk.client.SimmerClient._validate_and_set_wallet",
        lambda self, key: setattr(self, "_wallet_address", "0x" + "1" * 40),
    )
    monkeypatch.setattr(
        "simmer_sdk.version_check.check_server_version_compatibility",
        lambda *args, **kwargs: None,
    )

    called = []

    def fail_if_called(self, alerts=None):
        called.append(alerts)
        raise AssertionError("readonly construction processed risk alerts")

    monkeypatch.setattr(
        "simmer_sdk.client.SimmerClient._process_risk_alerts",
        fail_if_called,
    )

    client = SimmerClient.readonly(api_key="sk_live_test", venue="polymarket")

    assert client.venue == "polymarket"
    assert client._readonly is True
    assert called == []


def test_default_constructor_still_processes_risk_exits_for_live_polymarket_wallet(monkeypatch):
    """The external-wallet safety net stays default-on for regular clients."""
    monkeypatch.delenv("OWS_WALLET", raising=False)
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "0x" + "a" * 64)
    monkeypatch.setattr(
        "simmer_sdk.client.SimmerClient._validate_and_set_wallet",
        lambda self, key: setattr(self, "_wallet_address", "0x" + "1" * 40),
    )
    monkeypatch.setattr(
        "simmer_sdk.version_check.check_server_version_compatibility",
        lambda *args, **kwargs: None,
    )

    called = []
    monkeypatch.setattr(
        "simmer_sdk.client.SimmerClient._process_risk_alerts",
        lambda self, alerts=None: called.append(alerts),
    )

    client = SimmerClient(api_key="sk_live_test", venue="polymarket")

    assert client._readonly is False
    assert called == [None]


def test_readonly_rejects_trade_calls(monkeypatch):
    """A readonly client should fail closed if accidentally used to trade."""
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OWS_WALLET", raising=False)

    client = SimmerClient.readonly(api_key="sk_live_test")

    with pytest.raises(RuntimeError, match="readonly"):
        client.trade("market-id", "yes", amount=1.0)


READONLY_BLOCKED = [
    "update_settings", "cancel_order", "cancel_market_orders", "cancel_all_orders",
    "delete_alert", "register_webhook", "delete_webhook", "test_webhook",
    "link_wallet", "import_polymarket_wallet", "set_approvals",
    "activate_polymarket_dw", "activate_combo_dw", "wrap_on_dw",
    "register_agent_wallet", "update_agent_wallet_creds",
]


@pytest.mark.parametrize("method", READONLY_BLOCKED)
def test_readonly_rejects_account_mutations_before_any_request(monkeypatch, method):
    """Cancels, approvals, wallet, settings and webhook calls fail closed with no request sent."""
    import inspect

    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OWS_WALLET", raising=False)
    client = SimmerClient.readonly(api_key="sk_live_test")

    def _no_request(*a, **k):
        raise AssertionError(f"{method} sent a request from a readonly client")

    monkeypatch.setattr(client, "_request", _no_request)
    fn = getattr(client, method)
    args = [
        "x" for p in inspect.signature(fn).parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    with pytest.raises(RuntimeError, match="readonly"):
        fn(*args)


def test_every_public_write_method_is_readonly_guarded():
    """A new public method that sends POST/PATCH/PUT/DELETE must be guarded or explicitly allowed."""
    import ast
    import inspect
    import re
    import textwrap

    import simmer_sdk.client as client_module

    # Writes that touch no account state (catalog imports) or only return order params.
    allowed = {"import_market", "import_kalshi_market", "import_kalshi_event", "prepare_real_trade"}
    src = inspect.getsource(client_module)
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SimmerClient")
    unguarded = []
    for fn in cls.body:
        if not isinstance(fn, ast.FunctionDef) or fn.name.startswith("_") or fn.name in allowed:
            continue
        body = ast.get_source_segment(src, fn)
        if re.search(r'_request\(\s*"(POST|PATCH|PUT|DELETE)"', body) and "_assert_not_readonly" not in body:
            unguarded.append(fn.name)
    assert unguarded == []


# ---------------------------------------------------------------------------
# with_ows_wallet()
# ---------------------------------------------------------------------------


def test_with_ows_wallet_explicit_api_key(monkeypatch):
    """with_ows_wallet() uses the explicit api_key parameter when provided."""
    # Even if SIMMER_API_KEY is set in env, explicit param wins.
    monkeypatch.setenv("SIMMER_API_KEY", "env_key")
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OWS_WALLET", raising=False)
    monkeypatch.setattr(
        "simmer_sdk.ows_utils.get_ows_wallet_address",
        lambda name: TEST_OWS_ADDRESS,
    )

    client = SimmerClient.with_ows_wallet("my-agent", api_key="explicit_key")

    assert client.api_key == "explicit_key"
    assert client._ows_wallet == "my-agent"
    assert client._wallet_address == TEST_OWS_ADDRESS


def test_with_ows_wallet_env_fallback_api_key(monkeypatch):
    """with_ows_wallet() falls back to SIMMER_API_KEY when api_key is None."""
    monkeypatch.setenv("SIMMER_API_KEY", "fallback_key")
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("OWS_WALLET", raising=False)
    monkeypatch.setattr(
        "simmer_sdk.ows_utils.get_ows_wallet_address",
        lambda name: TEST_OWS_ADDRESS,
    )

    client = SimmerClient.with_ows_wallet("my-agent")

    assert client.api_key == "fallback_key"
    assert client._ows_wallet == "my-agent"
    assert client._wallet_address == TEST_OWS_ADDRESS


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
    monkeypatch.setattr(
        "simmer_sdk.ows_utils.get_ows_wallet_address",
        lambda name: TEST_OWS_ADDRESS,
    )

    client = SimmerClient.with_ows_wallet(
        "my-agent",
        venue="polymarket",
        base_url="https://example.test",
        live=False,  # avoid risk-alert network call
    )

    assert client.venue == "polymarket"
    assert client.base_url == "https://example.test"
    assert client.live is False
    assert client._ows_wallet == "my-agent"
    assert client._wallet_address == TEST_OWS_ADDRESS
