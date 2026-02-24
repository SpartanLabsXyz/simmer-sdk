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

# View cycle journal (last N cycles with skip reasons, errors, hints)
python automaton.py --journal 20

# Reset state (fresh start)
python automaton.py --reset

# Dry run (no trades, just show what would happen)
python automaton.py --budget 50 --days 30
```

**Debugging tip:** When trades aren't executing, check the journal first: `python automaton.py --journal 10`. It shows per-cycle skip reasons, execution errors, and tuning hints — everything you need to diagnose why signals aren't converting to trades.

## Skill Report Protocol

Skills emit a JSON report line during execution. The automaton parses this to populate the cycle journal and update state.

**Skill report format:**
```json
{
  "automaton": {
    "signals": 5,
    "trades_attempted": 3,
    "trades_executed": 2,
    "amount_usd": 15.50,
    "skip_reason": "safeguard: slippage > 10%",
    "execution_errors": ["insufficient_funds on trade 2"],
    "trades": [
      {
        "market_id": "abc123xyz",
        "question": "Will Bitcoin hit $100k by Feb 28?",
        "side": "yes",
        "shares": 45.2,
        "entry_price": 0.475,
        "amount_usd": 21.47,
        "success": true,
        "notes": "Kelly-sized position"
      },
      {
        "market_id": "def456uvw",
        "question": "Will Fed raise rates?",
        "side": "no",
        "shares": 22.8,
        "entry_price": 0.625,
        "amount_usd": 14.25,
        "success": true,
        "notes": "Positioned for deflationary signal"
      }
    ]
  }
}
```

**Fields:**
- `signals` (required) — Number of opportunities identified this cycle
- `trades_attempted` (required) — Number of trades tried to execute
- `trades_executed` (required) — Number that actually filled
- `amount_usd` (required) — Total USD spent this cycle
- `skip_reason` (optional) — Comma-separated skip reasons (e.g. "safeguard: slippage, already_holding")
- `execution_errors` (optional) — Array of error messages from failed trade attempts
- `trades` (optional but **recommended**) — Array of trade detail objects (see below)

**Trade detail object (when `trades_executed > 0`):**
- `market_id` (required) — Polymarket/Kalshi market ID
- `question` (required) — Market question (for audit trail)
- `side` (required) — `"yes"` or `"no"`
- `shares` (required) — Number of shares bought
- `entry_price` (required) — Price per share when filled
- `amount_usd` (required) — Total USD spent on this trade
- `success` (required) — `true` if fill confirmed, `false` if rejected/failed
- `notes` (optional) — Why this trade was chosen (e.g. "Kelly-sized", "sentiment spike")

**How to emit the report:**

From within your skill script (Python):
```python
import json
import sys

# ... trading logic ...

report = {
  "automaton": {
    "signals": 5,
    "trades_attempted": 2,
    "trades_executed": 1,
    "amount_usd": 10.0,
    "skip_reason": "position too small, already_holding",
    "execution_errors": [],
    "trades": [
      {
        "market_id": "abc123",
        "question": "Will X?",
        "side": "yes",
        "shares": 50,
        "entry_price": 0.20,
        "amount_usd": 10.0,
        "success": True,
      }
    ]
  }
}

# Emit as final JSON line to stdout
print(json.dumps(report))
sys.exit(0)
```

The automaton reads your last JSON line, extracts the `automaton` key, and logs it to the cycle journal. Include trade details for full audit trails and debugging.

## Journal Schema (`cycle_journal.jsonl`)

Each line is a JSON object representing one cycle. Your agent can read this file programmatically to build custom dashboards, alerts, or insights.

| Field | Type | Description |
|-------|------|-------------|
| `cycle` | int | Cycle number (monotonically increasing) |
| `timestamp` | string | ISO 8601 UTC time of cycle completion |
| `tier` | string | Budget tier: `"normal"`, `"cautious"`, or `"critical"` |
| `epsilon` | float | Current bandit exploration rate (0.05–0.2) |
| `budget_remaining_pct` | float | Fraction of budget remaining (0.0–1.0) |
| `selected_skills` | string[] | Skill slugs selected this cycle |
| `selection_reasoning` | object | `{slug: {reason, score}}` — why bandit picked each skill |
| `results` | object | Per-skill results (see below) |
| `pnl_total` | float | Cumulative P&L across all skills ($USD) |
| `spent_total` | float | Cumulative spend across all skills ($USD) |
| `runtime_sec` | float | Cycle wall-clock time in seconds |
| `circuit_breakers` | array | `[{skill, reason}]` — skills disabled this cycle |
| `tuning_hints` | string[] | Actionable suggestions (see Post-Cycle Analysis) |

**`results[slug]` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `signals` | int | Number of trading signals found |
| `trades_attempted` | int | Signals that passed filters |
| `trades_executed` | int | Trades that filled on-chain |
| `amount_usd` | float | Total USD spent this cycle |
| `skip_reason` | string\|null | Comma-separated skip reasons (e.g., `"already_holding, spread_too_wide"`) |
| `skip_counts` | object | `{category: count}` — skip reasons bucketed |
| `execution_errors` | string[] | Error messages from failed trades |
| `exit_code` | int | Skill process exit code (0 = success) |
| `success` | bool | Whether the skill ran without crashing |

**Example: build your own insights**

```python
import json
from datetime import datetime, timezone, timedelta

# Read last 6 hours of cycles
cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
cycles = []
for line in open("cycle_journal.jsonl"):
    entry = json.loads(line)
    if datetime.fromisoformat(entry["timestamp"]) > cutoff:
        cycles.append(entry)

# Per-skill P&L (aggregate trades_executed and amount_usd)
skill_stats = {}
for c in cycles:
    for slug, r in c["results"].items():
        s = skill_stats.setdefault(slug, {"executed": 0, "spent": 0})
        s["executed"] += r["trades_executed"]
        s["spent"] += r["amount_usd"]

# Circuit breaker events
cb_events = [cb for c in cycles for cb in c["circuit_breakers"]]

# Tuning hints (deduplicated)
hints = list(set(h for c in cycles for h in c["tuning_hints"]))

print(f"Cycles: {len(cycles)} | P&L: ${cycles[-1]['pnl_total'] if cycles else 0}")
print(f"Circuit breakers: {len(cb_events)} | Hints: {hints}")
```

Your agent can run this on a schedule, pipe it to Telegram/Discord, or use it as context for LLM-driven tuning decisions. The automaton handles trading; your agent handles analysis.

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

## Post-Cycle Analysis (for your Clawbot)

After each cycle, the automaton emits `tuning_hints` — deterministic pattern detections that your Clawbot's LLM can reason about and act on. No extra inference cost; the automaton just surfaces the data.

**Hint types and recommended actions:**

| Hint | Meaning | What to do |
|------|---------|------------|
| `zero_signals_streak` | Skill found 0 signals for 5+ cycles | Loosen thresholds: `python <skill>.py --set <param>=<value>` |
| `concentrated_loss` | Single skill lost >20% of total budget | Disable or reduce max bet size |
| `inert` | 50+ signals found, 0 executed | Execution thresholds too tight — lower confidence or min_edge |
| `win_rate_collapse` | Win rate <20% over 10+ cycles | Strategy may not suit current market conditions |
| `safeguard_dominant` | >80% of skips are safeguard blocks | Markets may be too volatile or near resolution |

**Example Clawbot actions:**

```bash
# Widen mert-sniper's window
cd polymarket-mert-sniper && python mert_sniper.py --set expiry_window_mins=10

# Reduce signal-sniper's bet size
cd polymarket-signal-sniper && python signal_sniper.py --set max_usd=2.00

# Check a skill's current config
cd polymarket-weather-trader && python weather_trader.py --config

# Review cycle history for patterns
cd simmer-automaton && python automaton.py --journal 50
```

The automaton handles skill selection; your Clawbot handles skill tuning.

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
