---
name: simmer-mcp-setup
version: "0.2.0"
published: true
description: One-shot bootstrap for the Simmer MCP server. Detects your agent runtime (Claude Code / Cursor / OpenClaw / Hermes / Codex / Grok Bot), installs simmer-mcp via npm, writes the right MCP config, prompts a restart, and verifies the tool handshake. Use after registering an agent on simmer.markets to run pre-built Simmer trading strategies through your MCP-aware agent.
metadata:
  author: "Simmer (@simmer_markets)"
  version: "0.2.0"
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

**Raw trade primitives (added in MCP v3.3.0):** the MCP now exposes raw primitives as standalone tools alongside the per-skill executors — `simmer_get_markets`, `simmer_get_market_context`, `simmer_get_briefing`, `simmer_trade`, `simmer_cancel_order`, and `get_portfolio`/`get_positions`/`get_expiring_positions`. So ad-hoc operations like *"show me Polymarket markets for the world cup"* or *"place a $2 paper YES on this market"* now work directly through MCP. `simmer_trade` is **paper-by-default** — live execution needs the triple gate (`dry_run=false` + a live `trading_venue` + `SIMMER_MCP_ALLOW_LIVE=true`). The [Python SDK](https://clawhub.ai/skills/simmer) still offers the fullest primitive surface (`client.trade()`, `client.get_briefing()`, etc.) for custom logic.

So: MCP and SDK are different shapes, both legitimate. MCP runs pre-built strategies *and* raw primitives through your agent with safety defaults; the SDK builds custom logic with full primitive access.

## What you'll have at the end

- `simmer-mcp` runnable via `npx -y simmer-mcp` (global install optional)
- Optionally `simmer-sdk` on the host plus `SIMMER_MCP_PYTHON` pointing at it — needed
  only by the `preflight` tool, the one bundled skill that runs Python. See Step 3b.
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

**Case B — key from prior dashboard registration.** Get it from [simmer.markets/dashboard](https://simmer.markets/dashboard?ref=sdk-skill&utm_campaign=sdk-skill) → your agent → **API key** tab. Paste it in (don't pipe from clipboard — pastes can pick up trailing characters):
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

**If npm or `npx` is blocked or hangs on your host**, `bun` works as a drop-in:
`bun add -g simmer-mcp`, and `bunx simmer-mcp` in place of `npx -y simmer-mcp` in the
config below. Some agent runtimes gate npm behind a command review that never returns —
observed on Grok Bot's cloud computer, 2026-09-04.

## Step 3b — Python, only if you want the `preflight` tool

**Scope first, because the server's own warning overstates this.** On startup you may see:

```
[simmer-mcp] ⚠ simmer-sdk not installed — per-skill execution will fail. Run: pip install simmer-sdk>=0.13.0
```

That line is wrong in two ways. **Exactly one bundled tool needs Python** — `preflight`,
the only bundled skill with an executable entrypoint. The other four (`simmer`,
`simmer-wallet-setup`, `simmer-briefing`, `simmer-mcp-setup`) return their SKILL.md text
and never start a subprocess, and every raw tool (`simmer_trade`, `simmer_get_markets`,
`get_portfolio`, …) is pure Node. And the version floor is wrong: `preflight` requires
**`simmer-sdk>=0.17.13`**, not `0.13.0`.

So if you do not need `preflight`, skip this step. If you do:

```bash
python3 -m venv .venv && .venv/bin/pip install 'simmer-sdk>=0.17.13'
```

Use a venv — most Linux hosts mark the system Python "externally managed", so a bare
`pip install` fails with PEP 668. If `python3 -m venv` itself fails with
*"ensurepip is not available"*, the venv module is packaged separately on that distro:
`sudo apt install python3-venv` (Debian/Ubuntu), or use `pipx`, or as a last resort
`pip install --break-system-packages`.

⚠️ **Creating the venv is not enough — you must point the server at it.** The server is
launched by your runtime as `npx -y simmer-mcp` and inherits *that* environment, not your
shell's. It resolves Python from `SIMMER_MCP_PYTHON`, then `which python`, then
`which python3` — **it never looks for a `.venv`**. So a venv you made in a terminal is
invisible to it, and `preflight` fails exactly as before.

Add the absolute path to the `env` block of whichever config you write in Step 4:

```json
"env": {
  "SIMMER_API_KEY": "sk_live_...",
  "SIMMER_MCP_PYTHON": "/absolute/path/to/.venv/bin/python"
}
```

The same key works in Hermes' YAML `env:` map. `SIMMER_MCP_PYTHON` is also the fix on
legacy distros where `python` is Python 2.

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

**Find the live config first.** Hermes supports profiles, and each profile has its own
`HERMES_HOME`. Editing the default config does nothing for an agent you run with `-p`:

| You run Hermes as | Config to edit |
|---|---|
| `hermes …` (default) | `~/.hermes/config.yaml` |
| `hermes -p <profile> …` | `~/.hermes/profiles/<profile>/config.yaml` |

If you use both, add the block to both. A profile can have MCP wired and still be useless
if that profile has no inference provider configured — **MCP and inference are separate**,
and the profile that can think is the one that needs the entry.

Hermes uses YAML, not JSON. Add under `mcp_servers` (snake_case — different from the other
runtimes):
```yaml
mcp_servers:
  simmer:
    command: "npx"
    args: ["-y", "simmer-mcp"]
    env:
      SIMMER_API_KEY: "sk_live_..."
```

**Use the CLI rather than editing by hand where you can.** Hermes ships
`hermes mcp add`, `hermes mcp list` and `hermes mcp test` — `hermes mcp test simmer`
connects and counts the tools, which is a faster verification than Step 6's handshake
(expect ~23 tools). `hermes mcp list` confirms which config Hermes actually read.

Stderr from the server goes to `<HERMES_HOME>/logs/mcp-stderr.log`. Read it first when
something is wrong.

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

### Grok Bot

Grok Bot has no MCP config file to edit — servers are added through its **Add MCP**
control, with the same three fields: command `npx` (or `bunx` where npm is blocked),
args `-y simmer-mcp`, and `SIMMER_API_KEY` in env.

⚠️ **Use a separate, unclaimed agent here.** On Grok Bot the MCP server runs on the cloud
computer that **every bot on your account shares**, and its env secrets are stored where
any of those bots can read them. Do not rely on a convention to keep that key harmless —
a Simmer API key is not venue-scoped, so anyone holding it can call the REST API with
`venue="polymarket"` directly and never touch this server's `SIMMER_MCP_ALLOW_LIVE` gate.

The control that actually holds is **claim status**: an unclaimed agent is $SIM-locked
regardless of any `venue=` parameter (Step 1, Case C). So register a second agent for the
shared VM, never send its `claim_url`, and keep your claimed agent's key off that machine.
For real trading on Grok Bot, install the `simmer` skill and drive the SDK on your own
machine through a granted local folder.

Grok **CLI** (the coding agent, a different product) does use a config file — treat it as
"Other / unknown runtime" below.

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
- For Hermes: `hermes mcp list` shows which config it actually read, and
  `hermes mcp test simmer` connects and counts tools. If you run Hermes with `-p`, check
  you edited that profile's config and not the default one.

**Everything works except the `preflight` tool.**
That is the one bundled tool that shells out to Python. Either `simmer-sdk` is missing, or
it is installed in a venv the server cannot see.

**Read the tool's own error first — it is the one signal every host gives you.** Calling
`simmer_preflight` returns exit 2 with the cause:

```
ERROR: simmer-sdk not installed — run: pip install simmer-sdk>=0.17.13
```

The server also prints an interpreter line on startup, which is more useful because it
names *which* Python got resolved:

```
[simmer-mcp] runtime: python3: v3.x (/path) | simmer-sdk: ...
```

⚠️ **Whether you can actually read that line depends on the host, so don't go hunting for
a log file.** The server always emits it; where stderr lands is the host's choice.

| Host | Where the startup line goes |
|---|---|
| Hermes | `<HERMES_HOME>/logs/mcp-stderr.log` — and a profile has its own, e.g. `~/.hermes/profiles/<name>/logs/mcp-stderr.log` |
| Grok Bot | **No file.** stderr is a Unix socket into the MCP host. Use the tool error above. |
| Others | Varies. If there is no log, run `npx -y simmer-mcp` once in a terminal with `SIMMER_API_KEY` set and read the line directly. |

If the resolved path is not your venv, set `SIMMER_MCP_PYTHON` (Step 3b).

Don't reach for any of this when a *different* skill tool fails — the other four bundled
skills never run Python, so their failures have some other cause.

**Tools listed but API calls return 401.**
- `SIMMER_API_KEY` env didn't make it into the MCP subprocess. The env block in the config has to be a direct value, not a `$VAR` reference — most MCP clients don't expand shell vars at server-launch time.
- Verify the key value: `printenv SIMMER_API_KEY | cut -c1-20` — must start with `sk_live_`. A common silent failure: install commands that use `pbpaste` or clipboard-read primitives can write the *install command text itself* as the key value when the user copies the command after copying the key. Fix: get a fresh key from [simmer.markets/dashboard](https://simmer.markets/dashboard?ref=sdk-skill&utm_campaign=sdk-skill), then `export SIMMER_API_KEY="sk_live_..."` typed/pasted directly.

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
- Dashboard: [simmer.markets/dashboard](https://simmer.markets/dashboard?ref=sdk-skill&utm_campaign=sdk-skill)
