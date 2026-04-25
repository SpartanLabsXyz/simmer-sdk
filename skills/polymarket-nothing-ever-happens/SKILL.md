---
name: polymarket-nothing-ever-happens
description: Buy NO on standalone non-sports yes/no Polymarket markets priced below a configurable cap. Based on the "nothing-ever-happens" thesis — binary markets often resolve NO, and cheap NO shares offer asymmetric value. Scans for candidates via Gamma API, filters out sports and grouped markets, checks fees, and executes.
metadata:
  author: Simmer (@simmer_markets)
  version: "1.0.2"
  displayName: Polymarket Nothing-Ever-Happens
  difficulty: beginner
---
# Polymarket Nothing-Ever-Happens Trader

Buy NO on standalone yes/no Polymarket markets priced below a configurable cap.

> **This is a template.** The default logic buys NO on any non-sports standalone market where NO costs ≤5¢. Remix it with custom filters (minimum volume thresholds, specific categories, date ranges) or pair it with a signal to skip markets where YES might actually happen. The skill handles plumbing (discovery, import, fee checks, execution). You define which markets to trade.

> **Based on:** [sterlingcrispin/nothing-ever-happens](https://github.com/sterlingcrispin/nothing-ever-happens)

## The Thesis

On most standalone binary prediction markets, the event resolves NO — nothing dramatic happens. Markets systematically overprice dramatic YES outcomes. When NO is trading at 3¢–5¢, you're getting 20–33x payout if you're right, and the base rate of "nothing happens" is often much higher than the implied 3–5%.

## What It Does

1. **Scans** Polymarket events via Gamma API for standalone yes/no markets
2. **Filters** out sports, grouped events, low-liquidity markets
3. **Selects** markets where NO ask ≤ price cap (default 5¢)
4. **Imports** each candidate into Simmer
5. **Checks** fees (only trades zero-fee markets) and safeguards
6. **Buys NO** via Simmer SDK, sized by `max_bet_usd`

## Setup Flow

When user asks to install or configure this skill:

1. **Install the Simmer SDK**
   ```bash
   pip install simmer-sdk
   ```

2. **Ask for Simmer API key**
   - They can get it from simmer.markets/dashboard → SDK tab
   - Store in environment as `SIMMER_API_KEY`

3. **Ask for wallet private key** (required for live trading)
   - This is the private key for their Polymarket wallet (the wallet that holds USDC)
   - Store in environment as `WALLET_PRIVATE_KEY`
   - The SDK uses this to sign orders client-side automatically — no manual signing needed
   - Not needed for $SIM paper trading on the Simmer venue

## Quick Commands

```bash
# Scan for candidates (no trades)
python nothing_ever_happens.py --scan

# Dry run — show what would trade
python nothing_ever_happens.py

# Execute real trades
python nothing_ever_happens.py --live

# Quiet mode (for cron — only prints on trades/errors)
python nothing_ever_happens.py --live --quiet

# Show config
python nothing_ever_happens.py --config

# Update config
python nothing_ever_happens.py --set price_cap=0.03
```

## Configuration

| Key | Env Var | Default | Description |
|-----|---------|---------|-------------|
| `price_cap` | `SIMMER_NEH_PRICE_CAP` | 0.05 | Max NO price to buy (0.05 = 5¢) |
| `max_bet_usd` | `SIMMER_NEH_MAX_BET_USD` | 5.0 | USDC per trade |
| `max_trades_per_run` | `SIMMER_NEH_MAX_TRADES_PER_RUN` | 3 | Max trades per execution |
| `daily_budget` | `SIMMER_NEH_DAILY_BUDGET_USD` | 15.0 | Daily spend limit |
| `min_liquidity` | `SIMMER_NEH_MIN_LIQUIDITY` | 500.0 | Min market liquidity (USDC) |
| `min_volume_24h` | `SIMMER_NEH_MIN_VOLUME_24H` | 100.0 | Min 24h volume (USDC) |
| `candidate_pages` | `SIMMER_NEH_CANDIDATE_PAGES` | 3 | Gamma API pages to scan |

Update via CLI: `python nothing_ever_happens.py --set price_cap=0.03`

## How It Works

### Market Discovery

The skill fetches active Polymarket events via the Gamma API, sorted by 24h volume. It only considers **standalone events** — events with exactly one market. Grouped events (e.g. "Who wins Iowa?" alongside "Who wins Florida?" in a presidential election group) are excluded because:
- They're often higher-profile and more efficiently priced
- The original strategy targets isolated, under-the-radar markets

### Filters Applied

1. **Standalone** — event has exactly 1 market
2. **Binary yes/no** — outcomes are exactly `["Yes", "No"]`
3. **Non-sports** — category and tags must not be sports-related
4. **Price cap** — NO price ≤ configured cap (default 5¢)
5. **Liquidity** — event liquidity ≥ 500 USDC
6. **Volume** — event 24h volume ≥ 100 USDC

### After Candidate Selection

For each candidate, the skill:
1. **Imports** the market into Simmer (needed to get a Simmer market ID for trading)
2. **Skips** markets where you already hold a position
3. **Checks fees** — only trades zero-fee markets (fee drag destroys edge on cheap NO)
4. **Checks safeguards** — skips markets with severe flip-flop warnings
5. **Executes** `client.trade(side="no", amount=max_bet_usd)`

### Risk Profile

- You pay the ask price for NO shares (default ≤5¢ each)
- If YES resolves: you lose your bet. If NO resolves: you collect $1/share
- Expected value depends on how often YES actually resolves in these markets
- The thesis: base rate of NO is much higher than 5%, so 20x payout is favorable
- **This is not guaranteed profit.** The edge depends on market selection quality.

## API Endpoints Used

- Gamma API `/events` — Discover active Polymarket events
- `POST /api/sdk/import` — Import market to Simmer (via `client.import_market()`)
- `GET /api/sdk/context/{market_id}` — Fee rate and safeguards
- `POST /api/sdk/trade` — Trade execution
- `GET /api/sdk/positions` — Current positions (avoid doubling up)

## Troubleshooting

**"No candidates below price cap"**
→ All standalone non-sports markets have NO > cap. Lower `price_cap` (e.g. `--set price_cap=0.08`) or wait — cheap NO opportunities appear sporadically.

**"Daily budget exhausted"**
→ Hit daily limit. Adjust with `--set daily_budget=30`.

**"Import failed"**
→ The market may have already resolved, or the slug is incorrect. The skill skips and continues.

**All candidates have fees**
→ The fee filter is intentional — 10% fee on a 5¢ NO position eliminates most of the edge. By design.

**"gamma_api.py not found"**
→ Copy `gamma_api.py` from the `polymarket-ai-divergence` skill into this skill's directory, or install both skills together.
