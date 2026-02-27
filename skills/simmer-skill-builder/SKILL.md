---
name: simmer-skill-builder
displayName: Simmer Skill Builder
description: Generate complete, installable OpenClaw trading skills from natural language strategy descriptions. Use when your human wants to create a new trading strategy, build a bot, generate a skill, automate a trade idea, turn a tweet into a strategy, or asks "build me a skill that...". Produces a full skill folder (SKILL.md + Python script + config) ready to install and run.
metadata: {"clawdbot":{"emoji":"🛠️","requires":{"env":["SIMMER_API_KEY"],"pip":["simmer-sdk"]},"cron":null,"autostart":false,"automaton":{"managed":false,"entrypoint":null}}}
authors:
  - Simmer (@simmer_markets)
version: "1.0.5"
difficulty: beginner
published: true
---

# Simmer Skill Builder

Generate complete, runnable Simmer trading skills from a strategy description.

> You are building an OpenClaw skill that trades prediction markets through the Simmer SDK. The skill you generate will be installed into your skill library and run by you — it must be a complete, self-contained folder that works out of the box.

## Workflow

### Step 1: Understand the Strategy

Ask your human what their strategy does. They might:
- Describe a trading thesis in plain language
- Paste a tweet or thread about a strategy
- Reference an external data source (Synth, NOAA, Binance, RSS, etc.)
- Say something like "build me a bot that buys weather markets" or "create a skill for crypto momentum"

Clarify until you understand:
1. **Signal** — What data drives the decision? (external API, market price, on-chain data, timing, etc.)
2. **Entry logic** — When to buy? (price threshold, signal divergence, timing window, etc.)
3. **Exit logic** — When to sell? (take profit threshold, time-based, signal reversal, or rely on auto-risk monitors)
4. **Market selection** — Which markets? (by tag, keyword, category, or discovery logic)
5. **Position sizing** — Fixed amount or smart sizing? What default max per trade?

### Step 2: Load References

Read these files to understand the patterns:

1. **`references/skill-template.md`** — The canonical skill skeleton. Copy the boilerplate blocks verbatim (config system, get_client, safeguards, execute_trade, CLI args).
2. **`references/simmer-api.md`** — Simmer SDK API surface. All available methods, field names, return types.

If the Simmer MCP server is available (`simmer://docs/skill-reference` resource), prefer reading that for the most up-to-date API docs. Otherwise use `references/simmer-api.md`.

For real examples of working skills, read:
- **`references/example-weather-trader.md`** — Pattern: external API signal + Simmer SDK trading
- **`references/example-mert-sniper.md`** — Pattern: Simmer API only, filter-and-trade

### Step 3: Get External API Docs (If Needed)

If the strategy uses an external data source:

- **Polymarket CLOB data:** If the Polymarket MCP server is available, search it for relevant endpoints (orderbook, prices, spreads). If not available, the key public endpoints are:
  - `GET https://clob.polymarket.com/book?token_id=<token_id>` — orderbook
  - `GET https://clob.polymarket.com/midpoint?token_id=<token_id>` — midpoint price
  - `GET https://clob.polymarket.com/prices-history?market=<token_id>&interval=1w&fidelity=60` — price history
  - Get `polymarket_token_id` from the Simmer market response.
- **Other APIs (Synth, NOAA, Binance, RSS, etc.):** Ask your human to provide the relevant API docs, or web-fetch them if you have access.

### Step 4: Generate the Skill

Create a complete folder on disk:

```
<skill-slug>/
├── SKILL.md          # Metadata + documentation
├── <script>.py       # Main trading script
└── scripts/
    └── status.py     # Portfolio viewer (copy from references)
```

#### SKILL.md Requirements

- YAML frontmatter with `name`, `displayName`, `description`, `metadata` (including automaton entrypoint), `version: "1.0.0"`, `published: true`
- `requires.pip` must include `simmer-sdk`
- `requires.env` must include `SIMMER_API_KEY`
- Body must include: "This is a template" callout, setup flow, configuration table, quick commands, example output, troubleshooting section

#### Python Script Requirements

Copy these verbatim from `references/skill-template.md`:
- Config system (`from simmer_sdk.skill import load_config, update_config, get_config_path`)
- `get_client()` singleton
- `check_context_safeguards()`
- `execute_trade()`
- `calculate_position_size()`
- CLI entry point with standard args (`--live`, `--positions`, `--config`, `--set`, `--smart-sizing`, `--no-safeguards`, `--quiet`)

Customize:
- `CONFIG_SCHEMA` — skill-specific params with `SIMMER_<SKILLNAME>_<PARAM>` env vars
- `TRADE_SOURCE` — unique tag like `"sdk:<skillname>"`
- `SKILL_SLUG` — must match the ClawHub slug exactly (e.g., `"polymarket-weather-trader"`)
- Signal logic — your human's strategy
- Market fetching/filtering — how to find relevant markets
- Main strategy function — the core loop

### Step 5: Validate

Run the validator against the generated skill:

```bash
python /path/to/simmer-skill-builder/scripts/validate_skill.py /path/to/generated-skill/
```

Fix any FAIL results before delivering to your human.

### Step 6: Publish to ClawHub

Once validated, publish the skill so it appears in the Simmer registry automatically:

```bash
npx clawhub@latest publish /path/to/generated-skill/ --slug <skill-slug> --version 1.0.0
```

After publishing, the Simmer sync job picks it up within 6 hours and lists it at [simmer.markets/skills](https://simmer.markets/skills). No submission or approval needed — publishing to ClawHub with `simmer-sdk` as a dependency is all it takes.

Tell your human:
> ✅ Skill published to ClawHub. It will appear in the Simmer Skills Registry within 6 hours at simmer.markets/skills.

For full publishing details: [simmer.markets/skillregistry.md](https://simmer.markets/skillregistry.md)

## Hard Rules

1. **Always use `SimmerClient` for trades.** Never import `py_clob_client`, `polymarket`, or call the CLOB API directly for order placement. Simmer handles wallet signing, safety rails, and trade tracking.
2. **Always default to dry-run.** The `--live` flag must be explicitly passed for real trades.
3. **Always tag trades** with `source=TRADE_SOURCE` and `skill_slug=SKILL_SLUG`. `SKILL_SLUG` must match the ClawHub slug exactly — Simmer uses it to track per-skill volume.
4. **Always include safeguards** — the `check_context_safeguards()` function, skippable with `--no-safeguards`.
5. **Always include reasoning** in `execute_trade()` — it's displayed publicly and builds your reputation.
6. **Use stdlib only** for HTTP (urllib). Don't add `requests`, `httpx`, or `aiohttp` as dependencies unless your human specifically needs them. The only pip dependency should be `simmer-sdk`.
7. **Polymarket minimums:** 5 shares per order, $0.01 min tick. Always check before trading.
8. **Include `sys.stdout.reconfigure(line_buffering=True)`** — required for cron/Docker/OpenClaw visibility.
9. **`get_positions()` returns dataclasses** — always convert with `from dataclasses import asdict`.
10. **Never expose API keys in generated code.** Always read from `SIMMER_API_KEY` env var via `get_client()`.

## Naming Convention

- Skill slug: `polymarket-<strategy>` for Polymarket-specific, `simmer-<strategy>` for platform-agnostic
- Trade source: `sdk:<shortname>` (e.g. `sdk:synthvol`, `sdk:rssniper`, `sdk:momentum`) — used for rebuy/conflict detection
- Skill slug: must match the ClawHub slug exactly (e.g. `SKILL_SLUG = "polymarket-synth-volatility"`) — used for volume attribution
- Env vars: `SIMMER_<SHORTNAME>_<PARAM>` (e.g. `SIMMER_SYNTHVOL_ENTRY`)
- Script name: `<descriptive_name>.py` (e.g. `synth_volatility.py`, `rss_sniper.py`)

## Example: Tweet to Skill

Your human pastes:
> "Build a bot that uses Synth volatility forecasts to trade Polymarket crypto hourly contracts. Buy YES when Synth probability > market price by 7%+ and Kelly size based on edge."

You would:
1. Understand: Signal = Synth API probability vs Polymarket price. Entry = 7% divergence. Sizing = Kelly. Markets = crypto hourly contracts.
2. Read `references/skill-template.md` for the skeleton.
3. Read `references/simmer-api.md` for SDK methods.
4. Read `references/example-weather-trader.md` — closest pattern (external API signal).
5. Ask your human for Synth API docs or web-fetch them.
6. Generate `polymarket-synth-volatility/` with:
   - SKILL.md (setup, config table, commands)
   - `synth_volatility.py` (fetch Synth forecast, compare to market price, Kelly size, trade)
   - `scripts/status.py` (copied)
7. Validate with `scripts/validate_skill.py`.
8. Publish: `npx clawhub@latest publish polymarket-synth-volatility/ --slug polymarket-synth-volatility --version 1.0.0`
