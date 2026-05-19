# @simmer/mcp

MCP server that exposes all [Simmer SDK](https://simmer.markets) skills as native Claude tools.

Any Claude Code or Claude Desktop user can install with a single command and run prediction-market skills directly from chat.

## Quick start

**From the `simmer-sdk` repo** (builds locally, auto-discovers skills):

```bash
export SIMMER_API_KEY=sk_live_...   # https://simmer.markets/dashboard
./add-mcp
```

**Via npx** (no repo clone needed):

```bash
export SIMMER_API_KEY=sk_live_...
claude mcp add simmer -- npx -y @simmer/mcp
```

## What gets registered

After install, Claude gets two built-in meta-tools plus one tool per discovered skill:

| Tool | Description |
|------|-------------|
| `simmer_list_skills` | Browse all available skills |
| `simmer_get_skill_docs` | Read a skill's full documentation |
| `simmer_<slug>` | Run or query a specific skill (one per skill) |

In Claude, type: `list all simmer skills` to get started.

## Options (repo install only)

```
./add-mcp [--global] [--npx]

  --global   Register globally (Claude Desktop + all projects)
  --npx      Use npx @simmer/mcp instead of local build
```

## Trading safety

All automaton skill tools default to `dry_run=true` (paper venue). Real-money execution requires `dry_run: false` explicitly passed in the tool call.

## Requirements

- Node.js 18+
- [Claude Code](https://claude.ai/code) or Claude Desktop
- `SIMMER_API_KEY` for most skills
