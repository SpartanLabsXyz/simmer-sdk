# simmer-mcp

MCP server that gives your AI agent access to Simmer trading skill management, experiment tracking, and AI-powered market research.

[![npm version](https://badge.fury.io/js/simmer-mcp.svg)](https://www.npmjs.com/package/simmer-mcp)

> **Migrating from `simmer-autoresearch`?** Run `npm install -g simmer-mcp` and update your agent config to use `simmer-mcp` as the command. Everything else is the same.

## Install

```bash
npx simmer-mcp install-skill
```

This installs the `SKILL.md` for your agent runtime (OpenClaw / Hermes). For Claude Code, paste the content into your project's `CLAUDE.md`.

## Claude Desktop config

```json
{
  "mcpServers": {
    "simmer": {
      "command": "npx",
      "args": ["-y", "simmer-mcp"],
      "env": {
        "SIMMER_API_KEY": "sk_live_..."
      }
    }
  }
}
```

Get your API key from [simmer.markets/dashboard](https://simmer.markets/dashboard).

## Tools by tier

### Free (no API key)

| Tool | Description |
|---|---|
| `list_skills` | List the core bundled Simmer skills with tier and Pro requirements |
| `get_skill_docs` | Get full SKILL.md for a specific skill |
| `troubleshoot_error` | Look up a Simmer API error and get a fix |

### Pro (requires SIMMER_API_KEY)

| Tool | Description |
|---|---|
| `init_experiment` | Initialize an autoresearch session for a skill |
| `run_experiment` | Run a shell command as a timed experiment |
| `log_experiment` | Record experiment result (keep/discard/crash) with git commit |
| `backtest_experiment` | Replay historical trades against new config (server-side) |
| `simmer_<slug>` × 5 | Read or execute a core bundled Simmer skill in paper or live mode |
| Raw market/trade tools | `simmer_get_markets`, `simmer_get_market_context`, `simmer_get_briefing`, `simmer_trade`, portfolio and position tools |

### MCP Resources

None as of v3.1.0. The previous `simmer://docs/api-reference` and `simmer://docs/skill-reference` resources shipped static markdown snapshots that drifted from the canonical source. Fetch [`docs.simmer.markets/llms-full.txt`](https://docs.simmer.markets/llms-full.txt) directly for the full Simmer API reference — it's always current.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `SIMMER_API_KEY` | For Pro tools | API key from simmer.markets/dashboard |
| `SIMMER_API_URL` | No | Override API base URL (default: `https://api.simmer.markets`) |
| `SIMMER_MCP_PYTHON` | No | Absolute path to the Python binary to use for skill execution (see [Pinning a Python interpreter](#pinning-a-python-interpreter)) |
| `AUTORESEARCH_MAX_EXPERIMENTS` | No | Cap experiments per session (default: 50) |
| `SIMMER_MCP_ALLOW_LIVE` | No | Set `true` to allow live trading via guarded trade tools |
| `SIMMER_MCP_ALLOW_EXTRA_ARGS` | No | Set `true` to pass `extra_args` through to skill CLI |

## Pinning a Python interpreter

By default, `simmer-mcp` resolves the Python binary in this order:

1. `SIMMER_MCP_PYTHON` env var (if set, used verbatim — no PATH lookup)
2. `python` on your PATH
3. `python3` on your PATH
4. Literal `python3` as a last resort

**Set `SIMMER_MCP_PYTHON` when:**

- You run skills inside a dedicated venv (e.g. the Hermes venv or a custom environment):
  ```
  SIMMER_MCP_PYTHON=/path/to/venv/bin/python
  ```
- Your system `python` is Python 2 (RHEL 7, Ubuntu 18.04, and other legacy Linux distros ship `python` → Python 2.x). In that case the fallback to `python` silently picks up Py2, `simmer-sdk` import fails, and skills report `simmer-sdk: not installed`. Pinning to `python3` or your venv's interpreter avoids this.

> **Legacy Linux warning:** On systems where `python` resolves to Python 2, all per-skill tools will fail with a silent import error unless `SIMMER_MCP_PYTHON` is set to a Python 3.8+ binary (the simmer-sdk minimum, per `pyproject.toml`).

### Example: Claude Desktop config with a pinned interpreter

```json
{
  "mcpServers": {
    "simmer": {
      "command": "npx",
      "args": ["-y", "simmer-mcp"],
      "env": {
        "SIMMER_API_KEY": "sk_live_...",
        "SIMMER_MCP_PYTHON": "/home/user/.venvs/simmer/bin/python"
      }
    }
  }
}
```

Replace the path with the output of `which python3` (or your venv's `bin/python`) on your system.

## Bundled skills (5)

Only foundational skills are included in this package. Each has a corresponding
`simmer_<slug>` MCP tool when `SIMMER_API_KEY` is set:

- `simmer` — Simmer overview and identity
- `simmer-wallet-setup` — Real-money wallet setup guidance
- `simmer-mcp-setup` — MCP runtime setup guidance
- `simmer-briefing` — Agent briefing and heartbeat guidance
- `preflight` — Trade-readiness check before real-money orders

Situational strategies such as combo, shock-ladder, copytrading, weather, and
DCA install on demand from ClawHub instead of shipping inside the npm bundle.

## Requirements

- Node.js 18+
- Python 3.9+ (for per-skill execution)
- `pip install simmer-sdk>=0.13.0` (for per-skill execution)

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for development setup, test structure, and release checklist.
