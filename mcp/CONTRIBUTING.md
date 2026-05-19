# Contributing to simmer-mcp (MCP)

## Development setup

```bash
cd mcp
npm install
npm run bundle-skills   # bundles skills + snapshots into dist artefacts
npm run build           # TypeScript → dist/
npm test                # build + all tests (104 tests)
```

## Manual smoke checklist (Phase 4 publish gate)

Run these before tagging a release:

```bash
# 1. Build + tests clean
npm test

# 2. Free-tier: exactly 3 tools (no API key)
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0.0.1"}}}' | \
printf '%s\n{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n' | \
SIMMER_API_KEY="" node dist/mcp-server.js 2>/dev/null | grep '"id":2'
# Expected: tools array with 3 entries (list_skills, get_skill_docs, troubleshoot_error)

# 3. Pro-tier: 19+ tools (with API key)
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0.0.1"}}}\n{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}\n' | \
SIMMER_API_KEY="sk_test_any" node dist/mcp-server.js 2>/dev/null | grep '"id":2'
# Expected: tools array with 26 entries (3 + 4 autoresearch + 19 per-skill)

# 4. Resources present
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0.0.1"}}}\n{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n{"jsonrpc":"2.0","id":2,"method":"resources/list","params":{}}\n' | \
SIMMER_API_KEY="" node dist/mcp-server.js 2>/dev/null | grep '"id":2'
# Expected: resources array with 2+ simmer:// URIs

# 5. Pack check — verify no sensitive files included
npm pack --dry-run
# Verify dist/, skills/, bundled-skills/, LICENSE are included
# Verify tests/, src/, node_modules/, *.env NOT included

# 6. Startup log looks correct
SIMMER_API_KEY="" node dist/mcp-server.js </dev/null 2>&1 | head -5
# Expected: [simmer-mcp] v<version> | tools: 3 (free only) | skills: 19 bundled
```

## Test structure

| File | What it tests |
|---|---|
| `tests/api.test.ts` | `SimmerApi.checkPro()` + `backtest()` BackendError relay |
| `tests/pro-gate.test.ts` | 403 Pro-gate MCP response shape |
| `tests/troubleshoot.test.ts` | `troubleshootError()` live API + local fallback |
| `tests/mcp-protocol.test.ts` | Full JSON-RPC wire protocol (tools/list count, resources/list) |
| `tests/docs-tools.test.ts` | `listSkills()`, `getSkillDocs()`, `listDocResources()` |
| `tests/env-translation.test.ts` | Live-trading gate (dry_run/SIMMER_MCP_ALLOW_LIVE) |
| `tests/blocked-flags.test.ts` | CLI flag sanitization for extra_args |
| `tests/discovery.test.ts` | Skill discovery from bundled-skills |
| `tests/errors.test.ts` | `BackendError` + `toMcpResponse()` |
| `tests/output-parsing.test.ts` | `simmer_managed_output` JSON parsing |
| `tests/per-skill-smoke.test.ts` | Entrypoint + SKILL.md presence + py_compile |
| `tests/runtime-probe.test.ts` | python3/git/simmer-sdk detection |
| `tests/scoring.test.ts` | Confidence scoring math |
| `tests/skill-runner.test.ts` | Process execution + timeout |
| `tests/state.test.ts` | JSONL state read/write/reconstruction |

## Architecture

```
src/
  mcp-server.ts      — entry point, registers all tools + resources
  api.ts             — SimmerApi class (BackendError on 4xx/5xx, checkPro)
  errors.ts          — BackendError + McpErrorResponse
  docs-tools.ts      — listSkills, getSkillDocs, doc resources
  troubleshoot.ts    — troubleshootError (live + local fallback)
  runtime-probe.ts   — python3/git/simmer-sdk detection
  per-skill-tools.ts — buildToolSchema, buildToolDescription, invokeSkillTool
  skill-discovery.ts — discoverSkills (reads bundled-skills/)
  skill-runner.ts    — runSkillProcess (subprocess execution)
  blocked-flags.ts   — filterBlockedFlags (live-trading CLI flag sanitization)
  env-translation.ts — buildEnv (tunables → env vars, live-trading gate)
  output-parsing.ts  — parseSkillOutput (simmer_managed_output JSON)
  core/              — types, scoring, state, git, runner
```

## Release process (Phase 4)

1. Bump `version` in `package.json`
2. Update `BUNDLED_VERSION` constant in `mcp-server.ts`
3. Run smoke checklist above
4. `npm pack --dry-run` to verify manifest
5. `npm publish` (requires npm login with access to the `simmer-mcp` package)
6. Post announcement to `#releases`
