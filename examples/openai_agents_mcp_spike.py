#!/usr/bin/env python3
"""Spike: OpenAI Agents API session driving simmer-mcp (#365).

This is a proof, not a product harness. It builds a session that calls the
existing simmer-mcp tool surface (markets / paper trade / briefing). It does
not add a new agent runtime, fork Codex, or place live-venue orders.

simmer-mcp ships as stdio (`npx simmer-mcp`). Agents API
`environment.type: none` can call remote MCP only over HTTP, so you need a
public Streamable HTTP URL in front of that same process. Credentials stay in
the environment (`SIMMER_API_KEY`, `OPENAI_API_KEY`). Never commit them.

Default mode is dry-run: print a redacted session payload and exit. CI uses
that path. `--mock` prints the intended MCP tool sequence without calling
OpenAI. `--run` creates a real session when keys (and a reachable MCP URL)
are present.

Usage:
    python examples/openai_agents_mcp_spike.py
    python examples/openai_agents_mcp_spike.py --mock
    SIMMER_MCP_URL=https://your-host/mcp \\
      OPENAI_API_KEY=... SIMMER_API_KEY=sk_live_... \\
      python examples/openai_agents_mcp_spike.py --run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

AGENTS_BETA_HEADER = "agents=v1"
AGENTS_SESSIONS_URL = "https://api.openai.com/v1/agents/sessions"
DEFAULT_MODEL = "gpt-6-astra"
PLACEHOLDER_MCP_URL = "https://mcp.example.invalid/mcp"

# MCP names (not SDK aliases). simmer_browse_markets is the keyless stand-in
# for simmer_get_markets when the HTTP process has no SIMMER_API_KEY.
PROOF_TOOLS = (
    "simmer_get_markets",
    "simmer_browse_markets",
    "simmer_trade",
    "simmer_get_briefing",
)

SPIKE_INSTRUCTIONS = """You are a Simmer paper-trading spike agent.

Use only the simmer MCP tools. Stay on the virtual SIM venue.
Currency on that venue is written as a $SIM suffix (example: 1.00 $SIM).

Hard rules:
- Call simmer_get_markets (or simmer_browse_markets if the keyless catalogue is all you have).
- Preview one paper trade with simmer_trade: venue=sim, dry_run=true, amount=1, side=yes.
- Then call simmer_get_briefing.
- Never set dry_run=false. Never use venue=polymarket or venue=kalshi.
- Never ask for or print API keys.
"""

SPIKE_TASK = """Using simmer MCP tools only, do these three things and summarize the results:

1. List a few active markets. Prefer simmer_get_markets with sort=volume and a small limit. If that tool is missing, use simmer_browse_markets.
2. Preview a paper $SIM trade with simmer_trade: venue=sim, dry_run=true, amount=1, side=yes, action=buy. Pick any returned market_id. Do not place a live order.
3. Call simmer_get_briefing.

Report what each tool returned. Stop after the briefing.
"""

MOCK_TOOL_TRACE = (
    {
        "tool": "simmer_get_markets",
        "arguments": {"sort": "volume", "limit": 5, "venue": "sim"},
        "result": {
            "markets": [
                {
                    "id": "00000000-0000-4000-8000-000000000001",
                    "question": "Mock spike market (dry path)",
                    "venue": "sim",
                    "status": "active",
                }
            ]
        },
    },
    {
        "tool": "simmer_trade",
        "arguments": {
            "market_id": "00000000-0000-4000-8000-000000000001",
            "side": "yes",
            "action": "buy",
            "amount": 1,
            "venue": "sim",
            "dry_run": True,
            "reasoning": "Agents API spike paper preview",
            "source": "sdk:openai-agents-mcp-spike",
        },
        "result": {
            "success": True,
            "dry_run": True,
            "venue": "sim",
            "cost_sim": "1.00 $SIM",
        },
    },
    {
        "tool": "simmer_get_briefing",
        "arguments": {},
        "result": {
            "balance": "10000.00 $SIM",
            "open_positions": 0,
            "note": "Mock briefing. Live --run uses the real MCP tool.",
        },
    },
)


def _require_https_mcp_url(url: str) -> str:
    parsed = url.strip()
    if not parsed.startswith("https://"):
        raise ValueError(
            "SIMMER_MCP_URL must be an https Streamable HTTP endpoint "
            "reachable from OpenAI (environment.type=none)."
        )
    return parsed


def build_http_mcp_tool(
    server_url: str,
    *,
    authorization: Optional[str] = None,
) -> Dict[str, Any]:
    """Remote MCP tool for environment.type=none (OpenAI dials HTTP)."""
    transport: Dict[str, Any] = {
        "type": "http",
        "server_url": server_url,
    }
    if authorization:
        # Optional. simmer-mcp itself reads SIMMER_API_KEY from process env,
        # not from HTTP headers. Set this only if your HTTP front-end maps
        # Authorization onto that process.
        transport["authorization"] = authorization
    return {
        "type": "mcp",
        "server_label": "simmer",
        "transport": transport,
        "connection_origin": "service",
        "required": True,
        "allowed_tools": list(PROOF_TOOLS),
    }


def build_stdio_mcp_tool() -> Dict[str, Any]:
    """Fallback: executor starts published simmer-mcp inside a sandbox."""
    return {
        "type": "mcp",
        "server_label": "simmer",
        "transport": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "simmer-mcp"],
            "cwd": "/workspace",
            "env_vars": ["SIMMER_API_KEY"],
        },
        "required": True,
        "allowed_tools": list(PROOF_TOOLS),
    }


def build_session_payload(
    *,
    model: str = DEFAULT_MODEL,
    mcp_url: Optional[str] = None,
    authorization: Optional[str] = None,
    stdio_sandbox: bool = False,
    simmer_api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Session body for POST /v1/agents/sessions (OpenAI-Beta: agents=v1)."""
    if stdio_sandbox:
        environment: Dict[str, Any] = {
            "type": "openai_hosted",
            "network": {"access": "enabled"},
            "packages": {"npm": ["simmer-mcp"]},
        }
        if simmer_api_key:
            # Visible to agent-generated code in the sandbox. Prefer HTTP +
            # process-env on a host you control.
            environment["env"] = {"SIMMER_API_KEY": simmer_api_key}
        tools: List[Dict[str, Any]] = [build_stdio_mcp_tool()]
    else:
        environment = {"type": "none"}
        tools = [
            build_http_mcp_tool(
                mcp_url or PLACEHOLDER_MCP_URL,
                authorization=authorization,
            )
        ]

    return {
        "agent": {
            "model": model,
            "instructions": SPIKE_INSTRUCTIONS,
            "tools": tools,
        },
        "environment": environment,
        "input": SPIKE_TASK,
    }


def redact_secrets(value: Any) -> Any:
    """Strip key material from printed payloads. Never log secrets."""
    secret_keys = {
        "authorization",
        "simmer_api_key",
        "openai_api_key",
        "api_key",
    }
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if lowered in secret_keys or lowered.endswith("_api_key"):
                redacted[key] = "***"
            elif lowered == "headers" and isinstance(item, dict):
                redacted[key] = {
                    hk: ("***" if "auth" in hk.lower() or "key" in hk.lower() else redact_secrets(hv))
                    for hk, hv in item.items()
                }
            elif lowered == "env" and isinstance(item, dict):
                redacted[key] = {ek: "***" for ek in item}
            else:
                redacted[key] = redact_secrets(item)
        return redacted
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def assert_paper_only(payload: Dict[str, Any]) -> None:
    """Fail closed if the spike payload asks for a live venue."""
    blob = json.dumps(payload)
    if "SIMMER_MCP_ALLOW_LIVE" in blob:
        raise AssertionError("spike must not enable SIMMER_MCP_ALLOW_LIVE")
    instructions = payload["agent"]["instructions"]
    if "dry_run=true" not in instructions or "venue=sim" not in instructions:
        raise AssertionError("instructions must pin venue=sim and dry_run=true")
    if "Never set dry_run=false" not in instructions:
        raise AssertionError("instructions must forbid live execution")


def create_session_via_sdk(payload: Dict[str, Any], openai_api_key: str) -> Any:
    """Preferred live path: client.beta.agents.sessions.create."""
    from openai import OpenAI  # type: ignore

    client = OpenAI(api_key=openai_api_key)
    return client.beta.agents.sessions.create(
        extra_headers={"OpenAI-Beta": AGENTS_BETA_HEADER},
        stream=True,
        **payload,
    )


def create_session_via_http(payload: Dict[str, Any], openai_api_key: str) -> Any:
    """Stdlib fallback when the OpenAI SDK is not installed."""
    body = dict(payload)
    body["stream"] = False
    data = json.dumps(body).encode("utf-8")
    request = Request(
        AGENTS_SESSIONS_URL,
        data=data,
        method="POST",
        headers={
            "Authorization": "Bearer %s" % openai_api_key,
            "Content-Type": "application/json",
            "OpenAI-Beta": AGENTS_BETA_HEADER,
        },
    )
    try:
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError("Agents API HTTP %s: %s" % (exc.code, detail)) from exc
    except URLError as exc:
        raise RuntimeError("Agents API request failed: %s" % exc.reason) from exc


def print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def print_mock_trace(events: Iterable[Dict[str, Any]] = MOCK_TOOL_TRACE) -> None:
    print("# Mock MCP proof (no OpenAI call, no Simmer network)")
    print_json({"steps": list(events), "venue": "sim", "dry_run": True})


def run_live(payload: Dict[str, Any], openai_api_key: str) -> int:
    try:
        events = create_session_via_sdk(payload, openai_api_key)
        print("# Streaming client.beta.agents.sessions.create (OpenAI-Beta: agents=v1)")
        with events:
            for event in events:
                rendered = event.to_json(indent=None) if hasattr(event, "to_json") else str(event)
                print(rendered, flush=True)
        return 0
    except ImportError:
        print(
            "# openai SDK missing; falling back to stdlib POST "
            "(pip install openai for client.beta.agents.sessions)",
            file=sys.stderr,
        )
        result = create_session_via_http(payload, openai_api_key)
        print_json(redact_secrets(result))
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--run",
        action="store_true",
        help="Create a real Agents API session. Requires OPENAI_API_KEY and a reachable MCP.",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Print the intended MCP tool sequence without calling OpenAI.",
    )
    parser.add_argument(
        "--stdio-sandbox",
        action="store_true",
        help="Use smallest openai_hosted + stdio simmer-mcp instead of HTTP / environment.none.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENAI_AGENTS_MODEL", DEFAULT_MODEL),
        help="Agents API model (default: gpt-6-astra or OPENAI_AGENTS_MODEL).",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    mcp_url = os.environ.get("SIMMER_MCP_URL")
    simmer_key = os.environ.get("SIMMER_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    authorization = os.environ.get("SIMMER_MCP_AUTHORIZATION")

    if mcp_url:
        mcp_url = _require_https_mcp_url(mcp_url)

    if args.run and not args.stdio_sandbox and not mcp_url:
        print(
            "error: --run with environment.type=none needs SIMMER_MCP_URL "
            "(public https Streamable HTTP in front of simmer-mcp).\n"
            "Or pass --stdio-sandbox to start npx simmer-mcp inside openai_hosted.",
            file=sys.stderr,
        )
        return 2

    payload = build_session_payload(
        model=args.model,
        mcp_url=mcp_url,
        authorization=authorization,
        stdio_sandbox=args.stdio_sandbox,
        simmer_api_key=simmer_key,
    )
    assert_paper_only(payload)

    print("# Redacted session payload (secrets from env only; never committed)")
    print_json(redact_secrets(payload))

    if args.mock:
        print()
        print_mock_trace()
        return 0

    if not args.run:
        print()
        print(
            "Dry-run only. Re-run with --mock for the tool sequence, "
            "or --run after exporting OPENAI_API_KEY and SIMMER_MCP_URL."
        )
        return 0

    if not openai_key:
        print("error: --run requires OPENAI_API_KEY", file=sys.stderr)
        return 2

    print()
    return run_live(payload, openai_key)


if __name__ == "__main__":
    sys.exit(main())
