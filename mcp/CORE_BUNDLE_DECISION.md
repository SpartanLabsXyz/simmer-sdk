# simmer-mcp core bundle decision

Date: 2026-06-20

## Decision

The canonical `simmer-mcp` npm bundle contains exactly these five pinned skills:

- `simmer`
- `simmer-wallet-setup`
- `simmer-mcp-setup`
- `simmer-briefing`
- `preflight`

All situational strategy skills, including `polymarket-btc-up-down-trader`,
`polymarket-combo-builder`, and `polymarket-soccer-shock-ladder`, install on
demand from ClawHub instead of shipping in the npm tarball.

## Rationale

The npm bundle should include only foundational skills that every agent needs
before it can safely install more specific skills from ClawHub:

- `simmer` gives the base product identity and orientation.
- `simmer-wallet-setup` covers real-money wallet onboarding.
- `simmer-mcp-setup` covers MCP runtime setup and troubleshooting.
- `simmer-briefing` gives agent heartbeat and operating guidance.
- `preflight` provides the safety check before real-money orders.

Base market and trading capability is provided by the TypeScript MCP server
tools, not by a bundled strategy directory. The audited surface includes
`simmer_get_markets`, `simmer_get_market_context`, `simmer_get_briefing`,
`simmer_trade`, portfolio/position query tools, and `simmer_cancel_order`.
Keeping those tools in the server means the Aeon "Polymarket Trader by Simmer"
pack can continue to wrap `simmer-mcp` without depending on a pinned copy of any
long-tail strategy skill.

## Non-goals

- Do not bundle market-specific strategy skills as "core", even if they are
  useful demos or reference implementations. They drift fastest and should be
  resolved through ClawHub.
- Do not bundle skill-authoring or publishing helpers such as
  `simmer-skill-builder`. They are development workflow tools, not foundational
  runtime dependencies for every MCP user.
- Do not duplicate CI gates. `npm run check:bundle-skills` and the skill
  governance script are the blocking checks for bundle freshness and publish
  governance.
