"""Dry/mocked coverage for the OpenAI Agents API → simmer-mcp spike (#365).

Does not call OpenAI or Simmer. Secrets stay out of fixtures.
"""

from __future__ import annotations

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

SPIKE_PATH = Path(__file__).resolve().parents[1] / "examples" / "openai_agents_mcp_spike.py"


def load_spike():
    spec = importlib.util.spec_from_file_location("openai_agents_mcp_spike", SPIKE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def spike():
    return load_spike()


def test_http_none_payload_uses_existing_mcp_tools(spike):
    payload = spike.build_session_payload(
        mcp_url="https://example.com/mcp",
        authorization="Bearer sk_live_placeholder",
    )
    spike.assert_paper_only(payload)

    assert payload["environment"] == {"type": "none"}
    tool = payload["agent"]["tools"][0]
    assert tool["type"] == "mcp"
    assert tool["server_label"] == "simmer"
    assert tool["connection_origin"] == "service"
    assert tool["transport"]["type"] == "http"
    assert tool["transport"]["server_url"] == "https://example.com/mcp"
    assert tool["required"] is True
    for name in (
        "simmer_get_markets",
        "simmer_browse_markets",
        "simmer_trade",
        "simmer_get_briefing",
    ):
        assert name in tool["allowed_tools"]


def test_stdio_sandbox_is_smallest_hosted_fallback(spike):
    payload = spike.build_session_payload(
        stdio_sandbox=True,
        simmer_api_key="sk_live_placeholder",
    )
    spike.assert_paper_only(payload)

    env = payload["environment"]
    assert env["type"] == "openai_hosted"
    assert env["network"] == {"access": "enabled"}
    assert env["packages"] == {"npm": ["simmer-mcp"]}
    assert env["env"]["SIMMER_API_KEY"] == "sk_live_placeholder"

    tool = payload["agent"]["tools"][0]
    assert tool["transport"]["type"] == "stdio"
    assert tool["transport"]["command"] == "npx"
    assert tool["transport"]["args"] == ["-y", "simmer-mcp"]
    assert "SIMMER_API_KEY" in tool["transport"]["env_vars"]


def test_redact_secrets_strips_keys(spike):
    http_payload = spike.build_session_payload(
        mcp_url="https://example.com/mcp",
        authorization="Bearer sk_live_secret",
    )
    hosted_payload = spike.build_session_payload(
        stdio_sandbox=True,
        simmer_api_key="sk_live_secret",
    )
    for payload in (http_payload, hosted_payload):
        blob = json.dumps(spike.redact_secrets(payload))
        assert "sk_live_secret" not in blob
        assert "***" in blob


def test_https_mcp_url_required(spike):
    with pytest.raises(ValueError, match="https"):
        spike._require_https_mcp_url("http://localhost:8787/mcp")


def test_mock_trace_is_paper_sim_only(spike):
    for step in spike.MOCK_TOOL_TRACE:
        args = step["arguments"]
        if step["tool"] == "simmer_trade":
            assert args["venue"] == "sim"
            assert args["dry_run"] is True
            assert args["amount"] == 1
        assert args.get("venue") in (None, "sim")


def test_cli_dry_run_and_mock(spike, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("SIMMER_API_KEY", raising=False)
    monkeypatch.delenv("SIMMER_MCP_URL", raising=False)

    dry = io.StringIO()
    with redirect_stdout(dry):
        assert spike.main([]) == 0
    dry_out = dry.getvalue()
    assert '"type": "none"' in dry_out
    assert "sk_live_" not in dry_out
    assert "Dry-run only" in dry_out

    mocked = io.StringIO()
    with redirect_stdout(mocked):
        assert spike.main(["--mock"]) == 0
    mock_out = mocked.getvalue()
    assert "simmer_get_markets" in mock_out
    assert "simmer_trade" in mock_out
    assert "simmer_get_briefing" in mock_out
    assert "1.00 $SIM" in mock_out


def test_cli_run_without_url_exits(spike, monkeypatch):
    monkeypatch.delenv("SIMMER_MCP_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert spike.main(["--run"]) == 2


def test_cli_run_without_openai_key_exits(spike, monkeypatch):
    monkeypatch.setenv("SIMMER_MCP_URL", "https://example.com/mcp")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert spike.main(["--run"]) == 2
