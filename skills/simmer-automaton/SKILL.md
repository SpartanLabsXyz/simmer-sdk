---
name: simmer-automaton
displayName: Simmer Automaton
description: Conway-inspired survival meta-skill. Strategy allocator with survival pressure — discovers installed trading skills, selects which to run each cycle using a multi-armed bandit, and degrades gracefully as budget depletes. Deploy with $50 and a time horizon. Survive or die.
metadata: {"clawdbot":{"emoji":"🧬","requires":{"env":["SIMMER_API_KEY"],"pip":["simmer-sdk"]},"cron":null,"autostart":false,"automaton":{"managed":false}}}
authors:
  - Simmer (@simmer_markets)
attribution: "Inspired by Conway Automaton (conway.ai)"
version: "1.0.0"
published: false
---

# Simmer Automaton

Deploy with $50. Survive 30 days. Or die.

> **This is a template.** The default behavior is epsilon-greedy skill selection across your installed trading skills. Remix it with different bandit algorithms (UCB1, Thompson sampling), custom tier logic, or additional survival mechanics. The automaton handles plumbing (skill discovery, P&L tracking, tier management). Your agent provides the strategy.

## What It Does

The automaton is not a trading strategy — it's a **strategy allocator** with survival pressure.

1. **Discovers** installed trading skills via SKILL.md metadata
2. **Selects** which skills to run each cycle using an epsilon-greedy bandit
3. **Tracks** per-skill P&L via source tags from the Simmer API
4. **Adapts** — winning skills run more often, losing skills get dropped
5. **Degrades** gracefully as budget depletes: thriving → normal → conserving → critical → dead

Works across all venues — Polymarket, Kalshi, and future integrations. The automaton sits above the venue layer.

## Survival Tiers

| Tier | Budget | Skills/Cycle | Exploration | Status |
|------|--------|-------------|-------------|--------|
| Thriving | >70% + profitable | max concurrent | full | Your agent is earning |
| Normal | 30-100% | max concurrent | full | Steady state |
| Conserving | 10-30% | 1 only | reduced (50%) | Tightening up |
| Critical | <10% | 1 (best only) | none (exploit) | Fight for survival |
| Dead | 0% or expired | 0 | N/A | Game over |

When budget runs out or the horizon expires, the automaton refuses to run. Deploy again or top up.

## When to Use This Skill

Use this skill when the user wants to:
- Run multiple trading skills and let the agent pick the best ones
- Deploy an autonomous trader with a fixed budget and time limit
- Add survival pressure to their trading setup
- Track which strategies perform best over time

## Setup Flow

1. **Install trading skills** — the automaton needs skills to manage:
   ```bash
   clawhub install polymarket-weather-trader
   clawhub install polymarket-fast-loop
   # Any skill with automaton.managed: true in its SKILL.md
   ```

2. **Set Simmer API key**:
   ```bash
   export SIMMER_API_KEY=sk_live_...
   ```

3. **Set wallet key** (for live trading):
   ```bash
   export WALLET_PRIVATE_KEY=0x...
   ```

4. **Start the automaton**:
   ```bash
   python automaton.py --budget 50 --days 30 --live
   ```

5. **Schedule with cron** (run every 5 minutes):

   **Linux crontab** (local/VPS installs):
   ```
   */5 * * * * cd /path/to/simmer-automaton && python automaton.py --live --quiet
   ```

   **OpenClaw native cron** (containerized or OpenClaw-managed setups):
   ```bash
   openclaw cron add \
     --name "Simmer Automaton" \
     --cron "*/5 * * * *" \
     --tz "UTC" \
     --session isolated \
     --message "Run the simmer automaton: cd /path/to/simmer-automaton && python automaton.py --live --quiet. Show the output summary." \
     --announce
   ```

## Configuration

| Key | Default | Env Var | Description |
|-----|---------|---------|-------------|
| `budget_usd` | 50.0 | `SIMMER_AUTOMATON_BUDGET` | Total trading budget |
| `horizon_days` | 30 | `SIMMER_AUTOMATON_DAYS` | Survival time limit |
| `epsilon` | 0.2 | `SIMMER_AUTOMATON_EPSILON` | Exploration rate (0-1) |
| `epsilon_decay` | 0.995 | `SIMMER_AUTOMATON_EPSILON_DECAY` | Per-cycle decay |
| `min_epsilon` | 0.05 | `SIMMER_AUTOMATON_MIN_EPSILON` | Minimum exploration |
| `max_concurrent` | 2 | `SIMMER_AUTOMATON_MAX_SKILLS` | Max skills per cycle |
| `cycle_interval` | 300 | `SIMMER_AUTOMATON_INTERVAL` | Seconds between cycles (display) |

Update via CLI: `python automaton.py --set epsilon=0.3`

## CLI Reference

```bash
# Start or continue with budget
python automaton.py --budget 50 --days 30 --live

# Cron mode (silent except trades and errors)
python automaton.py --live --quiet

# Check survival stats
python automaton.py --status

# Check skill performance
python automaton.py --skills

# Show config
python automaton.py --config

# Update config
python automaton.py --set KEY=VALUE

# Reset state (fresh start)
python automaton.py --reset

# Dry run (no trades, just show what would happen)
python automaton.py --budget 50 --days 30
```

## How It Works

### Multi-Armed Bandit

Each trading skill is an "arm" of the bandit. The automaton uses epsilon-greedy selection:

- **Explore** (probability = epsilon): Pick a random skill to try
- **Exploit** (probability = 1 - epsilon): Pick the skill with the best average P&L
- **New skills** always get tried first (infinite avg reward until played)
- Epsilon decays over time: starts at 0.2, decays by 0.995 per cycle, minimum 0.05

### P&L Tracking

The automaton fetches trade history from the Simmer API (`GET /api/sdk/trades`) across all venues. Trades are grouped by source tag (e.g., `sdk:weather`, `sdk:fastloop`). Net P&L = sum of sells - sum of buys per source.

### Skill Discovery

On each cycle, the automaton scans sibling skill directories for SKILL.md files with `automaton.managed: true` in their metadata. It reads the entrypoint and source tag from each skill. New skills are automatically added to the bandit.

### State

All state persists in `state.json` — budget tracking, bandit weights, tier history. Each cycle loads, updates, and saves state. Reset anytime with `--reset`.

## Troubleshooting

**No skills found**
- Ensure trading skills have `"automaton": {"managed": true, "entrypoint": "script.py"}` in their SKILL.md metadata
- Skills must be in sibling directories (same parent as simmer-automaton)

**Dead on first run**
- Budget too low or horizon already expired
- Reset with `--reset` and try a higher budget

**Skills failing**
- Check that each skill works standalone: `python skill.py --live`
- Skills must accept `--live` and `--quiet` flags
- Check stderr output in `--status` for errors

**P&L not updating**
- Skills must use source-tagged trades (e.g., `source="sdk:weather"`)
- P&L is fetched from the Simmer API, not calculated locally

## API Endpoints Used

- `GET /api/sdk/trades?venue=polymarket&limit=200` — Trade history
- `GET /api/sdk/trades?venue=kalshi&limit=200` — Kalshi trade history
- `GET /api/sdk/portfolio` — Portfolio with by-source breakdown
