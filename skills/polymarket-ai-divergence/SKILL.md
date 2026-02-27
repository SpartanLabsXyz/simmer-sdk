---
name: polymarket-ai-divergence
displayName: Polymarket AI Divergence
description: Find markets where Simmer's AI consensus diverges from the real market price, then trade on the mispriced side using Kelly sizing. Scans for divergence, checks fees and safeguards, and executes trades on zero-fee markets with sufficient edge.
metadata: {"clawdbot":{"emoji":"🔮","requires":{"env":["SIMMER_API_KEY"],"pip":["simmer-sdk"]},"cron":null,"autostart":false,"automaton":{"managed":true,"entrypoint":"ai_divergence.py"},"tunables":[{"env":"SIMMER_DIVERGENCE_MIN_EDGE","type":"number","default":0.05,"range":[0.01,0.30],"step":0.01,"label":"Minimum edge threshold"},{"env":"SIMMER_DIVERGENCE_MAX_BET_USD","type":"number","default":50,"range":[1,200],"step":5,"label":"Max bet per trade"},{"env":"SIMMER_DIVERGENCE_KELLY_CAP","type":"number","default":0.25,"range":[0.01,1.0],"step":0.01,"label":"Kelly fraction cap"},{"env":"SIMMER_DIVERGENCE_DAILY_BUDGET_USD","type":"number","default":100,"range":[10,500],"step":10,"label":"Daily budget"},{"env":"SIMMER_DIVERGENCE_MAX_TRADES_PER_RUN","type":"number","default":5,"range":[1,20],"step":1,"label":"Max trades per run"},{"env":"SIMMER_DIVERGENCE_DIRECTION_FILTER","type":"enum","default":"any","options":["any","yes_only","no_only"],"label":"Direction filter"}]}}
authors:
  - Simmer (@simmer_markets)
version: "2.0.2"
difficulty: intermediate
published: true
---

# Polymarket AI Divergence Trader

Find markets where Simmer's AI consensus diverges from the real market price, then trade the edge.

> **This is a template.** The default logic trades when AI divergence exceeds 2% on zero-fee markets, using Kelly sizing capped at 25%. Remix it with different edge thresholds, sizing strategies, or additional filters (e.g., only trade markets resolving within 7 days). The skill handles plumbing (divergence scanning, fee checks, safeguards, execution). Your agent provides the alpha.

## What It Does

1. **Scans** all active markets for AI vs market price divergence
2. **Filters** to markets with edge above threshold (default 2%) and zero fees
3. **Checks** safeguards (flip-flop detection, existing positions)
4. **Sizes** using Kelly criterion, capped conservatively
5. **Executes** trades on the mispriced side (YES when AI is bullish, NO when bearish)

## Quick Commands

```bash
# Scan only (dry run, no trades)
python ai_divergence.py

# Scan + execute trades
python ai_divergence.py --live

# Only show bullish divergences
python ai_divergence.py --bullish

# Only >15% divergence
python ai_divergence.py --min 15

# JSON output
python ai_divergence.py --json

# Cron mode (quiet, trades only)
python ai_divergence.py --live --quiet

# Show config
python ai_divergence.py --config

# Update config
python ai_divergence.py --set max_bet_usd=10
```

## Configuration

| Key | Env Var | Default | Description |
|-----|---------|---------|-------------|
| `min_divergence` | `SIMMER_DIVERGENCE_MIN` | 5.0 | Min divergence % for scanner display |
| `min_edge` | `SIMMER_DIVERGENCE_MIN_EDGE` | 0.02 | Min divergence to trade (2%) |
| `max_bet_usd` | `SIMMER_DIVERGENCE_MAX_BET` | 5.0 | Max bet per trade |
| `max_trades_per_run` | `SIMMER_DIVERGENCE_MAX_TRADES` | 3 | Max trades per cycle |
| `kelly_cap` | `SIMMER_DIVERGENCE_KELLY_CAP` | 0.25 | Kelly fraction cap |
| `daily_budget` | `SIMMER_DIVERGENCE_DAILY_BUDGET` | 25.0 | Daily spend limit |
| `default_direction` | `SIMMER_DIVERGENCE_DIRECTION` | (both) | Filter: "bullish" or "bearish" |

Update via CLI: `python ai_divergence.py --set max_bet_usd=10`

## How It Works

### Divergence Signal

Each imported market has two prices:
- **AI consensus** (`current_probability`) — Simmer's AI consensus price, derived from multi-model ensemble forecasting
- **External price** (`external_price_yes`) — Real market price on Polymarket/Kalshi

`divergence = AI consensus - external price`

When divergence > 0: AI thinks the market is underpriced → buy YES
When divergence < 0: AI thinks the market is overpriced → buy NO

### Kelly Sizing

Position size uses the Kelly criterion:
```
kelly_fraction = edge / (1 - price)
position_size = kelly_fraction * max_bet_usd
```
Capped at `kelly_cap` (default 25%) to limit risk.

### Fee Filtering

75% of Polymarket markets have 0% fees. The remaining 25% charge 10% (short-duration crypto/sports). This skill **only trades zero-fee markets** to avoid fee drag eroding the edge.

### Safeguards

- **Fee check**: Skips markets with any taker fee
- **Flip-flop detection**: Uses SDK's context API to detect contradictory trades
- **Position check**: Skips markets where you already hold a position
- **Daily budget**: Stops trading when daily spend limit is reached
- **Kelly sizing**: Conservative sizing prevents over-betting

## API Endpoints Used

- `GET /api/sdk/markets/opportunities` — Divergence-ranked market list
- `GET /api/sdk/context/{market_id}` — Fee rate and safeguards per market
- `POST /api/sdk/trade` — Trade execution (via SDK client)
- `GET /api/sdk/positions` — Current portfolio positions

## Troubleshooting

**"No markets above min edge threshold"**
→ All divergences are below the `min_edge` setting. Lower it with `--set min_edge=0.01` or wait for larger divergences.

**"Daily budget exhausted"**
→ The skill has hit its daily spend limit. Adjust with `--set daily_budget=50`.

**All markets skipped for fees**
→ Only zero-fee markets are traded. If all available divergence opportunities have fees, no trades execute. This is by design.

**"context fetch failed"**
→ The SDK context endpoint is rate-limited (18 req/min). If running frequently, reduce `max_trades_per_run`.
