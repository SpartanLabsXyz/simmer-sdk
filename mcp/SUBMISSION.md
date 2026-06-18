# simmer-mcp — Catalog Submission Pack

Paste-ready content for listing simmer-mcp on Smithery.ai and the official MCP Registry. Keep in sync with `package.json` (version) and `README.md` (capabilities) on every release.

## Files

| File | Purpose |
|---|---|
| `smithery.yaml` | Smithery deployment config (stdio, optional API key). |
| `SUBMISSION.md` | This file — paste-ready descriptions and submission steps. |

## Short description (<=160 chars)

> Skill discovery, autoresearch experiments, and live trading on prediction markets (Polymarket, Kalshi, Simmer) for AI agents.

## Long description

> simmer-mcp gives any MCP-compatible client (Claude Code, Claude Desktop, Cursor, Windsurf, etc.) access to Simmer — agent-native trading infrastructure for prediction markets. The server provides three tiers of tools:
>
> **Free (no API key):** Browse the core bundled Simmer skills with full documentation, and troubleshoot Simmer API errors with live pattern matching.
>
> **Pro (requires SIMMER_API_KEY):** Run autoresearch experiment loops (init, run, log, backtest) to optimize trading strategies with git-integrated tracking and confidence scoring. Execute trades with a safety triple-gate (dry_run + venue + env flag). Get agent briefings, search markets, and analyze market context with edge calculations.
>
> **Per-skill execution:** Core bundled Simmer skills are available directly as MCP tools. Situational strategy skills install on demand from ClawHub so they stay current outside the npm bundle.
>
> Get your API key from simmer.markets/dashboard.

## Tools

### Free (no API key)

| Tool | Description |
|---|---|
| `list_skills` | List core bundled Simmer skills with tier, version, and Pro requirements |
| `get_skill_docs` | Get full SKILL.md documentation for a specific skill |
| `troubleshoot_error` | Look up a Simmer API error and get a fix (live API + local fallback) |

### Pro (requires SIMMER_API_KEY)

| Tool | Description |
|---|---|
| `init_experiment` | Initialize an autoresearch session — set skill, metric, direction |
| `run_experiment` | Run a shell command as a timed experiment with pass/fail detection |
| `log_experiment` | Record experiment result (keep/discard/crash) with git auto-commit and confidence scoring |
| `backtest_experiment` | Replay historical trades against new config (server-side, no real execution) |
| `simmer_trade` | Execute or dry-run a trade with safety triple-gate |
| `simmer_get_briefing` | Single-call agent briefing: portfolio, positions, opportunities, performance |
| `simmer_get_markets` | Search and list markets with filtering by venue, status, tags |
| `simmer_get_market_context` | Rich market context: price history, positions, edge analysis, TRADE/HOLD recommendation |
| `simmer_cancel_order` | Cancel an open order (managed wallets, requires SIMMER_MCP_ALLOW_LIVE) |
| `simmer_<slug>` x 5 | Read or execute a specific core bundled Simmer skill in paper or live mode |

### MCP Resources

None. Fetch `docs.simmer.markets/llms-full.txt` directly for the full Simmer API reference.

## Tags / categories

`prediction-markets`, `trading`, `polymarket`, `kalshi`, `agents`, `autoresearch`, `mcp`

## Install instructions

```bash
npx -y simmer-mcp
```

Add to your MCP client config:

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

Get your API key from [simmer.markets/dashboard](https://simmer.markets/dashboard). The API key is optional — free tools work without it.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `SIMMER_API_KEY` | For Pro tools | API key from simmer.markets/dashboard |
| `SIMMER_MCP_ALLOW_LIVE` | For live trading | Set `true` to allow real trades via `simmer_trade` |
| `SIMMER_API_URL` | No | Override API base URL (default: https://api.simmer.markets) |

## Links

- Homepage: https://simmer.markets
- Docs: https://docs.simmer.markets
- npm: https://www.npmjs.com/package/simmer-mcp
- Repository: https://github.com/SpartanLabsXyz/simmer-sdk

## Submission steps

### Smithery.ai

1. Sign in at https://smithery.ai with the GitHub account that owns `SpartanLabsXyz/simmer-sdk`.
2. Click **Add Server** -> connect the repo, point at `mcp/smithery.yaml`.
3. Smithery's stdio runner will exec `npx -y simmer-mcp`. No further config needed for free tools.
4. Fill the listing form using the sections above.
5. Optionally pursue verification via Settings > Verification.

### MCP Registry (official)

1. Update `server.json` to reflect the npm package (not PyPI).
2. Install the publisher CLI and authenticate as a member of `SpartanLabsXyz`.
3. Publish via OIDC attestation.

## Release checklist (when bumping simmer-mcp)

- [ ] Bump `package.json` version
- [ ] Update tool counts above if tools changed
- [ ] Publish to npm: `npm publish` (interactive OTP in separate terminal)
- [ ] Smithery auto-tracks npm — no manual step unless metadata changed
