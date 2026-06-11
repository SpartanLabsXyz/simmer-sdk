"""SIM-3070: base_url resolution — explicit arg > SIMMER_API_URL env > production.

The env override lets harnesses (replay engine) redirect an UNMODIFIED skill
to a local server. Loopback http is already allowed by the HTTPS guard.
"""

import os

import pytest

from simmer_sdk import SimmerClient


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("SIMMER_API_URL", raising=False)


def test_explicit_arg_wins(monkeypatch):
    monkeypatch.setenv("SIMMER_API_URL", "http://127.0.0.1:1")
    c = SimmerClient(api_key="sk_test", base_url="http://localhost:9999", live=False)
    assert c.base_url == "http://localhost:9999"


def test_env_overrides_default(monkeypatch):
    monkeypatch.setenv("SIMMER_API_URL", "http://127.0.0.1:8123")
    c = SimmerClient(api_key="sk_test", live=False)
    assert c.base_url == "http://127.0.0.1:8123"


def test_default_is_production():
    c = SimmerClient(api_key="sk_test", live=False)
    assert c.base_url == "https://api.simmer.markets"


def test_non_loopback_http_still_rejected(monkeypatch):
    monkeypatch.setenv("SIMMER_API_URL", "http://evil.example.com")
    with pytest.raises(ValueError, match="https"):
        SimmerClient(api_key="sk_test", live=False)
