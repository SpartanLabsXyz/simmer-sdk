---
name: polymarket-elon-tweets
description: 'Trade Polymarket "Elon Musk # tweets" markets using XTracker post count data. Buys adjacent range buckets when combined cost < $1 for structural edge. Use when user wants to trade tweet count markets, automate Elon tweet bets, check XTracker stats, or run noovd-style trading.'
metadata:
  author: Simmer (@simmer_markets)
  version: "1.1.0"
  displayName: Polymarket Elon Tweet Trader
  difficulty: advanced
  attribution: Strategy inspired by @noovd
---
# Polymarket Elon Tweet Trader

Trade "Elon Musk # tweets" markets on Polymarket using XTracker post count data.

## When to Use This Skill

Use this skill when the user wants to:
- Trade Elon Musk tweet count markets automatically
- Set up @noovd-style bucket trading
- Check XTracker pace and stats for current tweet events
- Monitor and exit existing tweet market positions
- Configure bucket spread or entry thresholds

## How the Strategy Works

Polymarket runs weekly "How many tweets will Elon post?" events with range buckets (e.g., 200-219, 220-239, 240-259). Exactly one bucket resolves YES = $1. The strategy:

1. **Get XTracker pace** — XTracker tracks Elon's real-time post count and projects the final total
2. **Find center bucket** — The bucket containing XTracker's projected pace
3. **Buy adjacent buckets** — Buy the center + neighbors (configurable spread)
4. **Check combined cost** — Only buy if the sum of bucket prices < $1 (the +EV threshold)
5. **One bucket pays $1** — When the event resolves, one of your buckets pays $1, covering costs

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

4. **Ask about settings** (or confirm defaults)
   - Max bucket sum: Combined price threshold (default 90¢)
   - Max position: Amount per bucket (default $5.00)
   - Bucket spread: How many neighbors to buy (default 1 = center ± 1)
   - Exit threshold: When to sell (default 65¢)

5. **Save settings to config.json or environment variables**

6. **Set up cron** (disabled by default — user must enable scheduling)

## Configuration

| Setting | Env Variable | Config Key | Default | Description |
|---------|-------------|------------|---------|-------------|
| Max bucket sum | `SIMMER_ELON_MAX_BUCKET_SUM` | `max_bucket_sum` | 0.90 | Only buy if cluster prices sum < this |
| Max position | `SIMMER_ELON_MAX_POSITION` | `max_position_usd` | 5.00 | Maximum USD per bucket |
| Bucket spread | `SIMMER_ELON_BUCKET_SPREAD` | `bucket_spread` | 1 | Neighbors on each side (1 = 3 buckets) |
| Smart sizing % | `SIMMER_ELON_SIZING_PCT` | `sizing_pct` | 0.05 | % of balance per trade |
| Max trades/run | `SIMMER_ELON_MAX_TRADES` | `max_trades_per_run` | 6 | Maximum trades per scan cycle |
| Exit threshold | `SIMMER_ELON_EXIT` | `exit_threshold` | 0.65 | Sell when bucket price above this |
| Slippage max | `SIMMER_ELON_SLIPPAGE_MAX` | `slippage_max_pct` | 0.05 | Skip trade if slippage exceeds this |
| Min position | `SIMMER_ELON_MIN_POSITION` | `min_position_usd` | 2.00 | Floor for smart sizing (USD) |
| Data source | `SIMMER_ELON_DATA_SOURCE` | `data_source` | xtracker | Data source (xtracker) |

Config priority: config.json > environment variables > defaults.

## Quick Commands

```bash
# Check account balance and positions
python scripts/status.py

# Detailed position list
python scripts/status.py --positions
```

**API Reference:**
- Base URL: `https://api.simmer.markets`
- Auth: `Authorization: Bearer $SIMMER_API_KEY`
- Portfolio: `GET /api/sdk/portfolio`
- Positions: `GET /api/sdk/positions`

## Running the Skill

```bash
# Dry run (default — shows opportunities, no trades)
python elon_tweets.py

# Execute real trades
python elon_tweets.py --live

# With smart position sizing (uses portfolio balance)
python elon_tweets.py --live --smart-sizing

# Show XTracker stats only
python elon_tweets.py --stats

# Check positions only
python elon_tweets.py --positions

# View config
python elon_tweets.py --config

# Update config
python elon_tweets.py --set max_position_usd=10.00

# Disable safeguards (not recommended)
python elon_tweets.py --no-safeguards

# Quiet mode — only output on trades/errors (ideal for cron)
python elon_tweets.py --live --quiet

# Combine: frequent scanning, minimal noise
python elon_tweets.py --live --smart-sizing --quiet
```

## How It Works

Each cycle the script:
1. Fetches active XTracker trackings for Elon Musk tweet events
2. Gets real-time stats: current count, projected pace, days remaining
3. Searches Simmer for matching tweet count markets (auto-imports if missing)
4. Finds the bucket matching XTracker's pace projection
5. Evaluates adjacent buckets (center ± spread)
6. **Entry**: If sum of cluster prices < max_bucket_sum → BUY each bucket
7. **Exit**: Checks open positions, sells if any bucket price > exit_threshold
8. **Safeguards**: Checks context for flip-flop warnings, slippage
9. **Tagging**: All trades tagged with `sdk:elon-tweets` for tracking

## Auto-Import

If tweet count markets aren't on Simmer yet, the skill automatically imports them:
- Derives the Polymarket event URL from the XTracker tracking title
- Uses the SDK import endpoint (supports multi-outcome events)
- Imports all outcome buckets as a group
- Counts as 1 daily import regardless of bucket count

## Smart Sizing

With `--smart-sizing`, position size is calculated as:
- 5% of available USDC balance (configurable via `sizing_pct`)
- Capped at max position setting ($5.00 default)
- Falls back to fixed size if portfolio unavailable

## Safeguards

Before trading, the skill checks:
- **Flip-flop warning**: Skips if you've been reversing too much
- **Slippage**: Skips if estimated slippage > 15%
- **Market status**: Skips closed or resolved markets
- **Extreme prices**: Skips buckets priced > 98% or < 2%

Disable with `--no-safeguards` (not recommended).

## Source Tagging

All trades are tagged with `source: "sdk:elon-tweets"`. This means:
- Portfolio shows breakdown by strategy
- Other skills won't sell your tweet positions
- You can track tweet trading P&L separately

## Example Output

```
🐦 Simmer Elon Tweet Trader
==================================================

⚙️ Configuration:
  Max bucket sum:  $0.90
  Max position:    $5.00
  Bucket spread:   1 (center ± 1 = 3 buckets)
  Exit threshold:  65%
  Data source:     xtracker

📊 XTracker Stats:
  Tracking: Elon Musk # tweets Feb 13 - Feb 20
  Current count: 187 posts
  Pace: 243 projected
  Days remaining: 2.3

🎯 Target cluster: 240-259 (center) + 220-239, 260-279
  240-259: $0.35
  220-239: $0.22
  260-279: $0.18
  Cluster sum: $0.75 (< $0.90 threshold) ✅

  Executing trades...
  ✅ Bought 14.3 shares of 240-259 @ $0.35
  ✅ Bought 22.7 shares of 220-239 @ $0.22
  ✅ Bought 27.8 shares of 260-279 @ $0.18

📊 Summary:
  Events scanned: 2
  Clusters evaluated: 2
  Trades executed: 3
  Exits: 0
```

## Troubleshooting

**"No XTracker trackings found"**
- XTracker may not have active Elon tweet events
- New events usually start on Wednesdays/Thursdays

**"Cluster sum $X.XX exceeds threshold"**
- Buckets are too expensive — edge is too thin
- Wait for prices to drop or widen bucket_spread

**"No matching Simmer markets found"**
- Markets may not be imported yet — skill will auto-import on next run
- Check that your API key has import capacity (10/day free, 50/day Pro)

**"Safeguard blocked: flip-flop warning"**
- You've been changing direction too much on this market
- Wait before trading again

**"External wallet requires a pre-signed order"**
- `WALLET_PRIVATE_KEY` is not set in the environment
- The SDK signs orders automatically when this env var is present — no manual signing code needed
- Fix: `export WALLET_PRIVATE_KEY=0x<your-polymarket-wallet-private-key>`
- Do NOT attempt to sign orders manually or modify the skill code — the SDK handles it

**"Balance shows $0 but I have USDC on Polygon"**
- Polymarket uses **USDC.e** (bridged USDC, contract `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`) — not native USDC
- If you bridged USDC to Polygon recently, you likely received native USDC
- Swap native USDC to USDC.e, then retry

**"API key invalid"**
- Get new key from simmer.markets/dashboard → SDK tab
