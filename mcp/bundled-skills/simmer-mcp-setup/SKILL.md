---
name: simmer-mcp-setup
version: "0.3.3"
published: true
description: One-shot bootstrap for the Simmer MCP server. Detects your agent runtime (Claude Code / Cursor / OpenClaw / Hermes / Codex / Grok Bot), installs simmer-mcp via npm, writes the right MCP config, prompts a restart, and verifies the tool handshake. Use after registering an agent on simmer.markets to run pre-built Simmer trading strategies through your MCP-aware agent.
metadata:
  author: "Simmer (@simmer_markets)"
  version: "0.3.3"
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
  - **Keyless utility tools** (always available, no key needed): `list_skills`, `get_skill_docs`, `troubleshoot_error`, `simmer_browse_markets`, `simmer_get_leaderboard`, and on newer builds `simmer_register_agent`. The startup banner prints the exact keyless count for your build.
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

**Scope first.** On startup you may see:

```
[simmer-mcp] ⚠ simmer-sdk not installed — the preflight tool will fail (other tools are
unaffected). Run: pip install 'simmer-sdk>=0.17.13', then set SIMMER_MCP_PYTHON to that
interpreter.
```

**Exactly one bundled tool needs Python** — `preflight`, the only bundled skill with an
executable entrypoint. The other four (`simmer`, `simmer-wallet-setup`, `simmer-briefing`,
`simmer-mcp-setup`) return their SKILL.md text and never start a subprocess, and every raw
tool (`simmer_trade`, `simmer_get_markets`, `get_portfolio`, …) is pure Node.

⚠️ **On an older server the same warning reads `per-skill execution will fail` and
`>=0.13.0`. Both were wrong** — it is one tool, not all of them, and `preflight` requires
`>=0.17.13`. If you see that older wording, trust this page over the log line.

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

The same key works in Hermes' YAML `env:` map and OpenClaw's `env` object.
`SIMMER_MCP_PYTHON` is also the fix on legacy distros where `python` is Python 2.

**Verified 2026-09-04**, both directions: pointed at a venv *without* the SDK the server
reports `simmer-sdk: not installed`; pointed at one *with* it, `simmer-sdk: v0.24.6`. The
startup line names the interpreter it resolved, so it tells you whether the variable took
effect. With the variable unset it resolves from `PATH` — so it can appear to work by
accident if the launching process happens to have a venv activated, and break when the
same config runs under a runtime with a different `PATH`.

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

> ⚠️ Flag order matters: `-e KEY=value` goes **after** the server name `simmer`, then `--`, then the command. `-e` is variadic, so placed before the name it swallows the name as an env var. (An earlier version of this note said `claude mcp add --help` showed the wrong order; 2.1.260's help shows this one.) Verified 2026-09-04 on Claude Code 2.1.260 with a real add → `claude mcp get simmer` → remove cycle at local scope; the `-s user` form is the same command with the flag.

**On macOS the server's stderr is kept** — `~/Library/Caches/claude-cli-nodejs/<cwd-slug>/mcp-logs-simmer/*.jsonl`, one file per launch, and the startup banner (tool count, resolved Python) is in it. The slug is the absolute working directory with every `/` and `.` turned into `-` (`/Users/me/proj` → `-Users-me-proj`), or just glob `*/mcp-logs-simmer/`. On Linux the same tree sits under `~/.cache/claude-cli-nodejs/` (or `$XDG_CACHE_HOME/claude-cli-nodejs/` where that is set). Windows not measured; if the directory is not there, use the "Others" row under Troubleshooting. To check registration without a log, `claude mcp list` prints name, command and connection status — **and nothing secret.** `claude mcp get simmer` adds the scope but prints the `env` block **unmasked**, key included, so keep it out of any transcript you persist.

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

**Prefer the CLI.** OpenClaw ships `openclaw mcp add`, and `openclaw mcp doctor simmer --probe`
verifies the handshake and counts tools — faster and less error-prone than editing JSON by
hand.

```bash
export SIMMER_API_KEY=sk_live_...   # the gateway must see this too — see below
openclaw mcp add simmer --command npx --arg -y --arg simmer-mcp \
  --env 'SIMMER_API_KEY=${SIMMER_API_KEY}'
openclaw mcp doctor simmer --probe
```

`--arg` is repeatable (one per argv element) and `--env` takes `KEY=VALUE` — both per
OpenClaw's own `mcp` CLI docs. Quote the `${…}` so your shell does not expand it before
OpenClaw sees it.

⚠️ Known upstream issue at the time of writing: entries added via `openclaw mcp add` can
pass `doctor` yet never reach a `claude-cli`-backed agent session
(`openclaw/openclaw#122712`). If the tools show in `doctor` but not in the agent, that is
the first thing to check, and the hand-edit path below is the workaround. A cold retest on
2026-09-05 did not hit it: after `mcp add`, `openclaw agent --local` listed all 24 tools.

`SIMMER_MCP_PYTHON` (Step 3b, only for `preflight`) goes in the same entry. `openclaw mcp
configure` has no `--env`, so add it to the entry's `env` object in `openclaw.json` by
hand. `openclaw mcp reload` then refreshes `agent --local` and CLI sessions (verified on
the retest); the gateway daemon picks the edit up through its own config hot-reload, and
`mcp reload` sends it nothing. If a gateway-hosted agent's banner still shows the old
Python, restart the gateway.

⚠️ **Do not paste the key in literally if you can avoid it.** OpenClaw supports env
references — `"SIMMER_API_KEY": "${SIMMER_API_KEY}"` (also `"$SIMMER_API_KEY"`, or the
object form `{ source: "env", id: "SIMMER_API_KEY" }`) — and its own doctor warns when
it finds a literal key in the config. `~/.openclaw/openclaw.json` is a file other processes
on that machine can read; a reference keeps the secret in the environment.

**The OpenClaw gateway process itself must carry that variable**, not just your
interactive shell. Per OpenClaw's secrets docs, a missing or empty env value **fails
resolution** — the reference does not silently become an empty string, and it does not
fall through to any other credential. So the symptom of a variable the gateway cannot see
is a resolution error from OpenClaw, not a quietly keyless server. Export it wherever the
gateway is launched from, or keep it in a file the gateway sources.

Do not write `ref(env:NAME)` as the value. That is only how OpenClaw *labels* a resolved
reference in its own output; as an input it is treated as a literal string, which is
truthy — so the server registers every tool against a garbage key and prints only the
`sk_live_` warning. That looks like a healthy install with a bad key, which sends you
debugging the wrong thing.

In source, doctor's literal-secret check is "the authored value does not start with `$`",
so `${…}` is the form it accepts. **Do not treat the warning as proof either way.** On a
cold retest (2026-09-05) doctor printed "literal sensitive value" for an entry whose
reference resolved and traded, and `openclaw mcp show` redacts a reference as
`sk_live_***` as if it were a literal. Read the JSON instead: the value in
`~/.openclaw/openclaw.json` must begin with `${` with no quote character before it. A
`'${SIMMER_API_KEY}'` with the shell's quotes copied in is a literal, and a truthy one, so
the server registers every tool against it and only `preflight` or a trade tells you.

⚠️ **Don't check the tool count against a number in this document — check it against the
server's own banner.** The server prints `[simmer-mcp] v<x> | tools: N (…, K keyless)` on
startup; that N is the truth for the build you are running, and it changes between
releases. As of 2026-09-04 the published npm build (`simmer-mcp@3.5.1`) reports
**24 tools, 10 keyless**; the 3.5.0 build reported 23 and 9. Quoted only as a rough scale,
not a target to match, since a build from source can differ from the published package at
the same version number.

⚠️ **A low count usually means the key did not resolve, not that tools are missing.**
Without a usable `SIMMER_API_KEY` the server starts in **keyless mode** with only the
keyless subset. It does say so — look for this line, which is the fastest diagnosis
available:

```
[simmer-mcp] No SIMMER_API_KEY set — discovery tools only (skills, docs, market browse,
leaderboard, troubleshooting). Trading and portfolio tools need a key
```

A key that is present but malformed gets a different line naming `sk_live_`. So if the
banner shows roughly a third of the tools you expected, read the two lines above it before
debugging anything else.

⚠️ **`mcp doctor` reports green even when the install is half-broken.** It probes the
handshake, and the handshake succeeds without Python. Confirmed on OpenClaw 2026.9.1,
2026-09-04: doctor showed 23 tools while the server's stderr said `simmer-sdk not
installed`. A green doctor does not mean `preflight` works — see Step 3b.

Headless hosts (agent seats, CI) cannot run the interactive onboarding at all; it exits
with `Onboarding needs an interactive TTY`. Use
`openclaw onboard --non-interactive --accept-risk`.

If you edit the file by hand instead, add `simmer` under `mcp.servers`:
```json
{
  "mcp": {
    "servers": {
      "simmer": {
        "command": "npx",
        "args": ["-y", "simmer-mcp"],
        "env": {
          "SIMMER_API_KEY": "${SIMMER_API_KEY}"
        }
      }
    }
  }
}
```

Then `openclaw mcp reload` for `agent --local` and CLI sessions; a running gateway picks
the file up through hot-reload, or restart it.

Where the startup banner lands depends on which OpenClaw process launched the server.
Under the **gateway** it was read from `/tmp/openclaw/openclaw-YYYY-MM-DD.log` on
2026-09-04 (the `simmer-sdk not installed` line quoted above came from there). Under
`openclaw agent --local` and the `mcp` CLI on 2026-09-05 that log held CLI messages only
and no `[simmer-mcp]` line at all. If it is not there, run the server once by hand
(Troubleshooting, "Others").

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

**Use the CLI rather than editing by hand where you can.** Verified on a profile,
2026-09-05 (drop `-p <profile>` for the default config):

```bash
hermes -p <profile> mcp add simmer --command npx \
  --env SIMMER_API_KEY="$SIMMER_API_KEY" SIMMER_MCP_PYTHON=/absolute/path/to/.venv/bin/python \
  --args -y simmer-mcp
hermes -p <profile> mcp test simmer    # connects and counts the tools
hermes -p <profile> mcp list           # confirms which config Hermes actually read
```

⚠️ **`--args` must be the last option.** Hermes takes everything after it as the server's
argv, so an `--env` placed after `--args` is written into `args:` as two more arguments and
never reaches the `env:` map: the server starts keyless, registers only the discovery
tools, and your key sits in plain argv in `config.yaml`. `--env` takes several
`KEY=VALUE` tokens, so both variables go in the one flag as shown; drop the second if you
do not need `preflight`.

⚠️ **`mcp add` and `mcp remove` prompt.** `add` asks `Enable all N tools? [Y/n/select]:`,
`remove` asks `Remove server 'simmer'? [Y/n]:`, and an `add` over an existing entry first
asks `Server 'simmer' already exists. Overwrite? [y/N]:`. With an open, idle stdin the
prompt waits forever; with stdin closed it reads EOF, prints `Cancelled.` and saves
nothing. From a script, pipe the answers: `echo y |` for a fresh add,
`printf 'y\ny\n' |` when replacing an entry. `add` also writes `enabled: true` into the
entry; the hand-written block above works without it.

`hermes mcp test simmer` is a faster verification than Step 6's handshake. Editing the
entry's `env:` map in the YAML also works. A new chat session picked such an edit up
without restarting the Hermes daemon.

Stderr from the server goes to `<HERMES_HOME>/logs/mcp-stderr.log`. For a profile that is
`~/.hermes/profiles/<profile>/logs/mcp-stderr.log`. Read it first when something is
wrong; the startup banner and the resolved Python are in it.

### Codex

Codex (the OpenAI CLI, `codex-cli`) reads **TOML**, not JSON, and ships its own MCP CLI —
do not paste the JSON block from the other runtimes into it. Its config file is
`~/.codex/config.toml`, or `$CODEX_HOME/config.toml` when that variable is set (the ChatGPT
desktop app sets it); every path below means whichever one your Codex reads.

**Write this block** under `mcp_servers` in that file. It is the whole entry; there is
nothing else to add for Simmer:

```toml
[mcp_servers.simmer]
command = "npx"
args = ["-y", "simmer-mcp"]
env_vars = ["SIMMER_API_KEY"]   # forwarded from the environment Codex runs in
startup_timeout_sec = 60       # default 10; a cold npx fetch can miss it (see below)
required = true                # a server that fails to start aborts the session, loudly

[mcp_servers.simmer.env]       # only if you need the preflight tool (Step 3b)
SIMMER_MCP_PYTHON = "/absolute/path/to/.venv/bin/python"
```

Three things about that shape, each verified on codex-cli 0.149.1, 2026-09-04:

- **`env_vars` forwards the key by name**, so the file never holds the secret. With
  `SIMMER_API_KEY` exported in the shell that launches Codex the server registered the
  full set (24 on 3.5.1); with it unset the server came up keyless (10) — no error, just
  fewer tools, the same low-count symptom described under OpenClaw above.
- **Keys must sit in the right table.** `SIMMER_MCP_PYTHON` is a literal value and belongs
  in the `[mcp_servers.simmer.env]` sub-table. Put it directly under `[mcp_servers.simmer]`
  and Codex drops it silently — `codex mcp get simmer` still exits 0 and shows `env: -`.
- **`required = true` is the trade-off you are choosing.** Without it a server that misses
  its startup timeout is dropped with no error in the transcript and no simmer tools.
  With it, the same event aborts the session with
  `required MCP servers failed to initialize: simmer: timed out handshaking with MCP server`
  — and because this file is global, that means **every** Codex session on the machine,
  in any project, fails while simmer cannot start (offline, npm registry down). To work
  on something else during such an outage, set `enabled = false` on the entry or drop
  `required`.

`codex mcp add simmer --env SIMMER_API_KEY="$SIMMER_API_KEY" -- npx -y simmer-mcp` also
works, but it writes the **literal** key into `[mcp_servers.simmer.env]` — there is no
`env_vars` flag on `codex mcp add` in 0.149.1. If you started that way and switch to
`env_vars`, delete the `SIMMER_API_KEY = "..."` line yourself; Codex accepts both at once
and never tells you the secret is still on disk. `codex mcp get simmer` and
`codex mcp list` show the entry with env values masked.

**Project scope exists but is gated.** `.codex/config.toml` in the project root is read
only for a project listed as `trust_level = "trusted"` under `[projects."<abs path>"]` in
the global config — the file itself, not a `-c` override on the command line. Untrusted
or overridden, the server is silently absent: the `simmer` row is just missing from
`codex mcp list`, alongside whatever other servers you have.

⚠️ **The startup timeout is the failure to know about.** `startup_timeout_sec` defaults to
**10** (Codex's own MCP docs), and a cold `npx` fetch can miss it. Seen once in four
identical `codex exec` runs on 2026-09-04 (the other three registered 24), and reproduced
on demand by setting `startup_timeout_sec = 1`. The block above raises the timeout and
makes a miss loud; the global install in Step 3 (`npm install -g simmer-mcp`, then
`command = "simmer-mcp"` with no args) takes the fetch off the startup path as well.

Where the server's stderr goes depends on how you run Codex. An interactive `codex`
session records it in the `logs` table of `logs_2.sqlite` next to `config.toml`:

```bash
sqlite3 -readonly ~/.codex/logs_2.sqlite \
  "select feedback_log_body from logs where feedback_log_body like 'MCP server stderr (simmer)%'"
```

Headless `codex exec` wrote nothing to that database in any run here, so for a headless
host the banner is unreadable from inside Codex — run `npx -y simmer-mcp` once in a
terminal with the same env instead (Troubleshooting, "Others" row).

### Grok Bot

Grok Bot has no MCP config file to edit — servers are added through its **Add MCP**
control, with the same three fields: command `npx` with args `-y simmer-mcp` (or command
`bunx` with args `simmer-mcp` where npm is blocked, as in Step 3), and `SIMMER_API_KEY` in
env. `SIMMER_MCP_PYTHON` (Step 3b) goes in that same env field as a second entry; there is
no JSON to write. The value must be a path **on the cloud computer**, since that is where
the server runs — a venv on your own machine is invisible to it, and the server does not
check the path exists, so a wrong one still starts with every tool and fails only when
`preflight` is called.

Two mechanics the control does not tell you (cold retest, 2026-09-05): a server entry
cannot be edited in place, so changing an env value means removing the entry and adding
it again with the new value. And if a `simmer` entry already exists on the account's cloud
computer from an earlier attempt, remove it before adding; a fresh install on that retest
only started clean after the old entry was gone.

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

The agent should respond with the keyless utility tools, raw market/trade tools, and the core bundled skill tools:
- Keyless utilities, available with or without a key — `list_skills`, `get_skill_docs`, `troubleshoot_error`, `simmer_browse_markets`, `simmer_get_leaderboard`, and on newer builds `simmer_register_agent`. The exact set depends on the build you installed; the startup banner's keyless count is authoritative, not this list.
- Core bundled skill tools (`simmer_simmer`, `simmer_simmer_wallet_setup`, `simmer_simmer_mcp_setup`, `simmer_simmer_briefing`, `simmer_preflight`)
- Raw market/trade tools (`simmer_get_markets`, `simmer_get_market_context`, `simmer_get_briefing`, `simmer_trade`, and portfolio/position tools)

Then ask the agent to do something safe that exercises the API:
> Use the simmer tools to show me a few of the most active markets on the sim venue.

The `sim` venue is paper money — no real funds at risk. If this returns market data, the handshake works end-to-end and Simmer is ready to use.

## Troubleshooting

**Agent says "no simmer tools available" after restart.**
- Confirm the runtime fully restarted (not just reloaded the conversation).
- Check the config file actually got written and look for the `simmer` entry. The key differs per runtime: `mcpServers` in `~/.claude.json` and `~/.cursor/mcp.json`, `mcp.servers` in `~/.openclaw/openclaw.json`, `mcp_servers:` in Hermes' YAML, `[mcp_servers.simmer]` in Codex's `config.toml`. Grepping one runtime's key in another's file finds nothing and proves nothing.
- For Claude Code: `claude mcp list` shows registered servers and their status without
  printing secrets (`claude mcp get simmer` adds the scope but prints the key in clear).
  Local scope is keyed on the directory you ran `claude mcp add` from — start Claude Code
  from that directory.
- For Codex: `codex mcp list` / `codex mcp get simmer`. If the entry is there but the
  agent sees no tools and you did not set `required = true`, it is usually the 10-second
  startup timeout; with `required = true` that case aborts the session with a named error
  instead (Step 4, Codex). A project-scoped entry also needs the project trusted in the
  global file.
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
| OpenClaw | Gateway-launched: `/tmp/openclaw/openclaw-YYYY-MM-DD.log` (`bundle-mcp::` entries). `agent --local` and the `mcp` CLI: **not in that log** — fall back to "Others". |
| Claude Code | macOS: `~/Library/Caches/claude-cli-nodejs/*/mcp-logs-simmer/*.jsonl`; Linux: `~/.cache/claude-cli-nodejs/*/mcp-logs-simmer/*.jsonl`, or under `$XDG_CACHE_HOME` where set (same slug rule, Step 4). One file per launch. Windows not measured — fall back to "Others". |
| Codex | Interactive session: `logs_2.sqlite` beside `config.toml`, rows starting `MCP server stderr (simmer)` (query in Step 4). Headless `codex exec`: **no file** — fall back to "Others". |
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
