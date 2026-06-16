---
name: polymarket-world-cup-delta-pairs
description: Scan all World Cup markets on Polymarket and surface implied-probability discrepancies between correlated markets for the same team — e.g., a team's group-stage odds vs. their tournament-win odds. Research/analysis tool; does not auto-trade.
category: world-cup
tags:
  - world-cup
  - soccer
  - research
  - analysis
  - pairs
metadata:
  author: Alyna (community) / Simmer (@simmer_markets)
  version: "0.2.0"
  displayName: World Cup Delta-Pairs
  difficulty: beginner
---
# World Cup Delta-Pairs

Surface implied-probability discrepancies across Polymarket World Cup markets. The
skill fetches all WC markets via `tags="world-cup"`, groups them by team, and computes
the price delta between markets at different tournament stages for the same team.

A large delta — for example, a team priced at 0.05 to win the tournament but 0.80 to
advance from their group — may signal mispricing between correlated markets.

> This is a **research tool**. It surfaces analysis only; it does not execute trades.
> Read [DISCLAIMER.md](./DISCLAIMER.md) before acting on any output.

## What it does

1. Fetches all active World Cup markets using `tags="world-cup"` (targeted filter,
   not a bulk 700-market scan).
2. Extracts team names from market questions and groups markets by team.
3. Pairs markets at different tournament stages for the same team (group → advance →
   final → champion).
4. Scores each pair by the implied conditional gap: how far `price_B / price_A`
   deviates from structural expectations.
5. Prints the top-N pairs ranked by delta magnitude, flagging structurally-inverted
   pairs (later stage priced higher than earlier stage).

## Setup

1. **Install the Simmer SDK** (0.17.0 or newer):
   ```bash
   pip install -U 'simmer-sdk>=0.17.0'
   ```

2. **Set your Simmer API key**:
   ```bash
   export SIMMER_API_KEY=...   # simmer.markets/dashboard → SDK tab
   ```

## Quick start

```bash
# Show top 20 delta pairs (default)
python world_cup_delta_pairs.py

# Show top 10 with minimum delta of 15%
python world_cup_delta_pairs.py --top 10 --min-delta 0.15

# Use live Polymarket prices
python world_cup_delta_pairs.py --venue polymarket

# Machine-readable JSON output
python world_cup_delta_pairs.py --json

# Show configuration
python world_cup_delta_pairs.py --config
```

## Understanding the output

```
  1. Argentina  ⚠️  INVERTED
     Stage advance     (0.820)  Will Argentina advance from Group D?
     Stage champion    (0.210)  2026 FIFA World Cup Winner: Argentina?
     Delta: 0.610  |  Implied B|A: 0.256  (normal — 25.6% win rate from knockout stage)

  2. France
     Stage group_win   (0.750)  Will France win Group E?
     Stage champion    (0.180)  2026 FIFA World Cup Winner: France?
     Delta: 0.570  |  Implied B|A: 0.240
```

- **INVERTED**: Later stage priced higher than earlier stage (structurally impossible —
  a team cannot win the final without advancing from the group). Strongest signal.
- **Delta**: Absolute price difference between the two markets.
- **Implied B|A**: `price_B / price_A` — conditional probability of reaching stage B
  given stage A. Values > 1.0 confirm an inversion.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SIMMER_API_KEY` | — | Required. Your Simmer SDK API key. |
| `TRADING_VENUE` | `sim` | Venue: `sim` for $SIM prices, `polymarket` for live market prices. |
| `WC_DELTA_TOP` | `20` | Number of pairs to display per run. |
| `WC_DELTA_MIN` | `0.10` | Minimum delta threshold (pairs below this are filtered). |
| `WC_DELTA_LIMIT` | `200` | Max WC markets to fetch. Higher = more coverage, slower. |

## Options

```
--top N            Number of pairs to show (default: 20)
--min-delta FLOAT  Minimum delta to include, e.g. 0.15 (default: 0.10)
--venue VENUE      sim (default) or polymarket
--limit N          Max WC markets to fetch (default: 200)
--json             Machine-readable JSON output
--config           Show configuration
```

## Caveats

- **Thin books**: A $50 book at a given price is not the same as a $50,000 book.
  Check volume before acting on any signal.
- **Timing**: Group-stage markets can move fast around match results. Run close to
  market open for the freshest prices.
- **Team extraction**: The skill uses a regex heuristic on question text. Ambiguous
  or unusual question formats may miss some pairs.
- **Stage classification**: Markets that don't match known stage keywords are labelled
  `other` and excluded from pair computation.

## Troubleshooting

**"No pairs found with delta ≥ X%"**
- Try `--min-delta 0.05` to lower the threshold.
- Run `--venue polymarket` for live market prices if WC markets haven't fully synced
  to the sim venue yet.

**"Error fetching WC markets"**
- Check `SIMMER_API_KEY` is set: `echo $SIMMER_API_KEY`
- Verify network access: `python -c "import simmer_sdk; print(simmer_sdk.__version__)"`

**"0 active WC markets"**
- Check the World Cup is currently in progress and markets are open.
- Verify the Simmer WC sync is running: markets auto-import with `tags=["world-cup"]`.
