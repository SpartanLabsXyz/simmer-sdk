---
name: simmer-mcp-setup
version: "0.1.3"
published: true
description: One-shot bootstrap for the Simmer MCP server. Detects your agent runtime (Claude Code / Cursor / OpenClaw / Hermes / Codex), installs simmer-mcp via npm, writes the right MCP config, prompts a restart, and verifies the tool handshake. Use after registering an agent on simmer.markets to run pre-built Simmer trading strategies through your MCP-aware agent.
metadata:
  author: "Simmer (@simmer_markets)"
  version: "0.1.3"
  displayName: Simmer MCP Setup
  difficulty: beginner
  primaryEnv: SIMMER_API_KEY
  envVars:
    - name: SIMMER_API_KEY
      required: true
      description: "Your Simmer SDK API key (from agent registration or simmer.markets/dashboard)."
---

# Simmer MCP Setup

One-shot bootstrap that wires the Simmer MCP server into your agent runtime. Read-once, run-once — after this completes, the skill itself isn't needed again.

## What the Simmer MCP is (and isn't)

The Simmer MCP gives your agent raw market/trade tools plus a small pinned catalog of **core Simmer skills** it can invoke as tools. Strategy skills such as `polymarket-copytrading`, `polymarket-fast-loop`, and `kalshi-weather-trader` install on demand from ClawHub instead of shipping inside the npm package.

**What this MCP is for:** querying markets, checking account state, placing guarded direct trades, and running the core bundled Simmer playbooks through your agent. For situational strategies, ask the agent to install the current ClawHub skill first (for example, `clawhub install polymarket-copytrading`) and then follow that skill's instructions. Real trades land on the configured venue — paper `sim` by default, real venues require an explicit triple opt-in (`dry_run=false` + `venue=polymarket|kalshi` + `SIMMER_MCP_ALLOW_LIVE=true` env var on the MCP server).

**What this MCP doesn't do (yet):** expose raw trade primitives like `place_order` or `get_briefing` as standalone tools. Ad-hoc operations like *"buy $10 yes on this BTC market"* or *"show me my current portfolio"* aren't possible through MCP today — those still need the [Python SDK](https://clawhub.ai/skills/simmer), which exposes `client.trade()`, `client.get_briefing()`, etc. directly. Raw MCP primitives are a tracked follow-up.

So: MCP and SDK are different shapes, both legitimate. MCP runs pre-built strategies through your agent with safety defaults; SDK builds custom logic with full primitive access.

## What you'll have at the end

- `simmer-mcp` runnable via `npx -y simmer-mcp` (global install optional)
- Your agent runtime's MCP config updated with a `simmer` entry
- `SIMMER_API_KEY` plumbed into the MCP subprocess
- Simmer tools visible to your agent:
  - **3 free utility tools** (always available): `list_skills`, `get_skill_docs`, `troubleshoot_error`
  - **Core Simmer skill tools** — the npm package bundles only foundational, pinned skills (`simmer`, `simmer-wallet-setup`, `simmer-mcp-setup`, `simmer-briefing`, and `preflight`). Situational strategies such as combo, shock-ladder, copytrading, weather, and DCA install on demand from ClawHub so they stay current.
  - **Raw market/trade tools** — `simmer_get_markets`, `simmer_get_market_context`, `simmer_get_briefing`, `simmer_trade`, portfolio/position tools, and guarded order cancellation.
  - **4 Pro-gated autoresearch tools** (`init_experiment`, `run_experiment`, `log_experiment`, `backtest_experiment`) — only registered if you're on the Pro plan.

## Step 1 — confirm you have an API key

This skill needs `SIMMER_API_KEY` to wire into the MCP config. Three cases:

**Case A — key already in env.** Verify:
```bash
[[ "$SIMMER_API_KEY" == sk_live_* ]] && echo "OK" || echo "MISSING or malformed"
```
If "OK", skip to Step 2.

**Case B — key from prior dashboard registration.** Get it from [simmer.markets/dashboard](https://simmer.markets/dashboard) → your agent → **API key** tab. Paste it in (don't pipe from clipboard — pastes can pick up trailing characters):
```bash
read -s -p 'SIMMER_API_KEY: ' KEY && export SIMMER_API_KEY=$KEY
```

**Case C — no agent registered yet.** Register one now:
```bash
curl -X POST https://api.simmer.markets/api/sdk/agents/register \
  -H "Content-Type: application/json" \
  -d '{"name": "my-agent", "description": "What this agent does"}'
```
Response includes `api_key` and `claim_url`. Save the API key:
```bash
export SIMMER_API_KEY="sk_live_..."
```
Send the `claim_url` to the human user — they need to visit it before any real-money trading is unlocked. Until then, all trades stay on the `sim` paper venue (this is intentional, not a bug).

## Step 2 — confirm Node.js and npm are installed

The simmer-mcp server runs on Node.js. Check:
```bash
node --version  # need v18 or higher
npm --version
```

If both return a version → skip to Step 3.

If `command not found`, the user needs to install Node.js. **Don't auto-install via `curl | sh`** — it modifies the user's system without their approval. Instead, show them the platform-specific option:

| Platform | Recommended install |
|---|---|
| macOS | Download installer from [nodejs.org](https://nodejs.org) (LTS) and double-click. Or `brew install node` if Homebrew is installed. |
| Windows | Download installer from [nodejs.org](https://nodejs.org) (LTS) and double-click. |
| Linux (Debian/Ubuntu) | `sudo apt update && sudo apt install nodejs npm` |
| Linux (Fedora/RHEL) | `sudo dnf install nodejs npm` |

The Node.js installer bundles npm, so installing Node.js gives you both. After install, the user needs to reopen their terminal so `node`/`npm` land on PATH, then re-run this step.

> **Why not nvm?** nvm is great for developers who switch Node versions across projects. For a one-time global install of a CLI tool like simmer-mcp, the official installer is simpler.

## Step 3 — install simmer-mcp (optional but recommended)

```bash
npm install -g simmer-mcp
```

This step is **optional**. The MCP config in Step 4 uses `npx -y simmer-mcp`, which fetches the package on first launch even without a global install. Installing globally just makes the first launch slightly faster (no fetch delay). If you skip Step 3, everything still works.

If you do install it and get an EACCES permission error on Linux/macOS: do NOT `sudo npm install` (creates permission tangles later). Either fix npm's global directory permissions per [npm's docs](https://docs.npmjs.com/resolving-eacces-permissions-errors-when-installing-packages-globally), or just skip the global install — the `npx -y simmer-mcp` form in the config works either way.

> **Why no `--version` check?** simmer-mcp's binary doesn't have CLI flags — every invocation starts the stdio MCP server. Verification happens in Step 6 when your agent calls a simmer tool and gets a real response.

## Step 4 — wire up the MCP config

Detect which runtime your agent is in, then write the right config. Pattern is "use the native CLI if the runtime has one; fall back to direct config-file write."

### Claude Code

Decide scope first:

| Scope | What it means | Flag | Config file |
|---|---|---|---|
| **User (global)** | Simmer available in every Claude Code session, every project | `-s user` | `~/.claude.json` |
| **Local (default)** | Simmer available only when Claude Code is run from the current directory tree | (none — default) | `~/.claude.json` under per-cwd entries |
| **Project** | Simmer available to anyone working in this project (committed to repo) | `-s project` | `.mcp.json` in cwd |

Most onboarding flows want **user scope** — install once, available everywhere.

**Preferred (no file editing):**
```bash
claude mcp add -s user simmer -e SIMMER_API_KEY="$SIMMER_API_KEY" -- npx -y simmer-mcp
```

> ⚠️ Flag order matters: `-e KEY=value` goes **after** the server name `simmer`, then `--`, then the command. The example in `claude mcp add --help` puts `-e` before the name; that form fails because `-e` is variadic and greedily consumes the server name as an env var. The order shown here is the form that actually works in current Claude Code versions (verified on Claude Code via real add/remove cycle).

This writes `~/.claude.json` for you with the correct `command`/`args`/`env` structure. The `"$SIMMER_API_KEY"` expansion bakes the literal key value into the config (MCP runtimes don't expand shell vars at server-launch time).

**Fallback (if `claude mcp add` isn't available in this Claude Code version):** add the following to `~/.claude.json` under `mcpServers` (create the key if it doesn't exist) — use the literal API key value, not `$VAR`:
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

For **project scope** instead: use the same JSON shape but write it to `.mcp.json` in the project root (single top-level `mcpServers` key, no other fields).

### Cursor

Edit `~/.cursor/mcp.json` (create the file if it doesn't exist):
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

Project-scoped: use `.cursor/mcp.json` in the project root.

### OpenClaw

Edit `~/.openclaw/openclaw.json` and add `simmer` under `mcp.servers`:
```json
{
  "mcp": {
    "servers": {
      "simmer": {
        "command": "npx",
        "args": ["-y", "simmer-mcp"],
        "env": {
          "SIMMER_API_KEY": "sk_live_..."
        }
      }
    }
  }
}
```

Restart your OpenClaw runtime so it picks up the new server.

### Hermes

Hermes uses YAML, not JSON. Edit `~/.hermes/config.yaml` and add under `mcp_servers` (snake_case — different from the other runtimes):
```yaml
mcp_servers:
  simmer:
    command: "npx"
    args: ["-y", "simmer-mcp"]
    env:
      SIMMER_API_KEY: "sk_live_..."
    enabled: true
```

### Codex

The canonical Codex MCP config path varies by install — consult [Codex's MCP docs](https://openai.com/index/introducing-codex) for the exact file. The block to add is the standard MCP shape:
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

### Other / unknown runtime

If you're on a runtime not listed above but it speaks MCP, you almost certainly need this exact block in its MCP-server config file (check the runtime's docs for where that lives):
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

## Step 5 — restart the runtime

MCP servers are loaded at startup. Quit and reopen your agent runtime so it picks up the new `simmer` entry. Some runtimes (OpenClaw, Hermes daemon mode) have a reload command — use that instead of a full restart if available.

## Step 6 — verify

Don't trust "looks installed" — verify with a real tool call.

Ask your agent:
> What simmer tools can you see? List them.

The agent should respond with the 3 utility tools, raw market/trade tools, and the core bundled skill tools:
- `list_skills`
- `get_skill_docs`
- `troubleshoot_error`
- Core bundled skill tools (`simmer_simmer`, `simmer_simmer_wallet_setup`, `simmer_simmer_mcp_setup`, `simmer_simmer_briefing`, `simmer_preflight`)
- Raw market/trade tools (`simmer_get_markets`, `simmer_get_market_context`, `simmer_get_briefing`, `simmer_trade`, and portfolio/position tools)

Then ask the agent to do something safe that exercises the API:
> Use the simmer tools to show me a few of the most active markets on the sim venue.

The `sim` venue is paper money — no real funds at risk. If this returns market data, the handshake works end-to-end and Simmer is ready to use.

## Troubleshooting

**Agent says "no simmer tools available" after restart.**
- Confirm the runtime fully restarted (not just reloaded the conversation).
- Check the config file actually got written — `cat ~/.claude.json` (or equivalent) and look for the `simmer` entry under `mcpServers`.
- For Claude Code: `claude mcp list` shows registered servers and their status.

**Tools listed but API calls return 401.**
- `SIMMER_API_KEY` env didn't make it into the MCP subprocess. The env block in the config has to be a direct value, not a `$VAR` reference — most MCP clients don't expand shell vars at server-launch time.
- Verify the key value: `printenv SIMMER_API_KEY | cut -c1-20` — must start with `sk_live_`. A common silent failure: install commands that use `pbpaste` or clipboard-read primitives can write the *install command text itself* as the key value when the user copies the command after copying the key. Fix: get a fresh key from [simmer.markets/dashboard](https://simmer.markets/dashboard), then `export SIMMER_API_KEY="sk_live_..."` typed/pasted directly.

**`npm install -g simmer-mcp` fails with EACCES on Linux/macOS.**
- Don't `sudo npm install` — that creates permission problems later. Either fix npm's global directory permissions per [npm's docs](https://docs.npmjs.com/resolving-eacces-permissions-errors-when-installing-packages-globally), or just use the `npx -y simmer-mcp` form in your config (no global install needed; npx fetches on first launch).

**`claude mcp add` fails with "command not found".**
- Older Claude Code versions don't have the `mcp add` subcommand. Use the JSON-write fallback under [Step 4 — Claude Code](#claude-code).

**Tools work for `list_skills` but not for trading.**
- Trading tools require both `SIMMER_API_KEY` and (for real-money venues) wallet linking. If the user wants real-money trading, they also need [`simmer-wallet-setup`](https://clawhub.ai/skills/simmer-wallet-setup) — that's a separate skill. Trading on the `sim` venue works without wallet setup.

## Anti-patterns

- **Don't auto-install Node.js via `curl | sh`** — modifying the user's system without explicit approval is bad practice. Show the platform-specific install hint and let the user decide.
- **Don't paste the API key from clipboard into a pipe.** Use `read -s` (per [SIM-2118](https://github.com/SpartanLabsXyz/simmer/issues/2118)).
- **Don't `sudo npm install -g`.** Fix the underlying npm permissions, or use `npx -y simmer-mcp` in the config (no global install needed).
- **Don't tell the user "it should work now" without verifying.** Run Step 6 — confirm a real tool call returns real data.

## Links

- simmer-mcp package: [npmjs.com/package/simmer-mcp](https://www.npmjs.com/package/simmer-mcp)
- General Simmer skill (Python SDK path): [clawhub.ai/skills/simmer](https://clawhub.ai/skills/simmer)
- Wallet setup (real-money trading): [clawhub.ai/skills/simmer-wallet-setup](https://clawhub.ai/skills/simmer-wallet-setup)
- Full Simmer docs: [docs.simmer.markets](https://docs.simmer.markets)
- Dashboard: [simmer.markets/dashboard](https://simmer.markets/dashboard)
