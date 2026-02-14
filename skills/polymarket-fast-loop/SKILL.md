---
name: polymarket-fast-loop
displayName: Polymarket FastLoop Trader
description: Trade Polymarket BTC 5-minute and 15-minute fast markets using CEX price momentum signals via Simmer API. Default signal is Binance BTC/USDT klines. Use when user wants to trade sprint/fast markets, automate short-term crypto trading, or use CEX momentum as a Polymarket signal.
metadata: {"clawdbot":{"emoji":"⚡","requires":{"env":["SIMMER_API_KEY"]},"cron":null,"autostart":false}}
authors:
  - Simmer (@simmer_markets)
version: "1.0.5"
published: true
---

# Polymarket FastLoop Trader

Trade Polymarket's 5-minute BTC fast markets using real-time price momentum from Binance.

> **Polymarket only.** All trades execute on Polymarket with real USDC. Use `--live` for real trades, dry-run is the default.

**How it works:** Every cycle, the script finds the current live BTC fast market, checks BTC price momentum on Binance, and trades if momentum diverges from market odds.

**This is a template.** The default signal (Binance momentum) gets you started. Your agent's reasoning is the edge — layer on sentiment analysis, multi-exchange spreads, news feeds, or custom signals to improve it.

> ⚠️ Fast markets carry Polymarket's 10% fee (`is_paid: true`). Factor this into your edge calculations.

## When to Use This Skill

Use this skill when the user wants to:
- Trade BTC sprint/fast markets (5-minute or 15-minute)
- Automate short-term crypto prediction trading
- Use CEX price momentum as a Polymarket signal
- Monitor sprint market positions

## Setup Flow

When user asks to install or configure this skill:

1. **Ask for Simmer API key**
   - Get from simmer.markets/dashboard → SDK tab
   - Store in environment as `SIMMER_API_KEY`

2. **Ask about settings** (or confirm defaults)
   - Asset: BTC, ETH, or SOL (default BTC)
   - Entry threshold: Min divergence to trade (default 5¢)
   - Max position: Amount per trade (default $5.00)
   - Window: 5m or 15m (default 5m)

3. **Set up cron or loop** (user drives scheduling — see "How to Run on a Loop")

## Quick Start

```bash
# Set your API key
export SIMMER_API_KEY="your-key-here"

# Dry run — see what would happen
python fastloop_trader.py

# Go live
python fastloop_trader.py --live

# Live + quiet (for cron/heartbeat loops)
python fastloop_trader.py --live --quiet

# Live + smart sizing (5% of balance per trade)
python fastloop_trader.py --live --smart-sizing --quiet
```

## How to Run on a Loop

The script runs **one cycle** — your bot drives the loop. Set up a cron job or heartbeat:

**Every 5 minutes (one per fast market window):**
```
*/5 * * * * cd /path/to/skill && python fastloop_trader.py --live --quiet
```

**Every 1 minute (more aggressive, catches mid-window opportunities):**
```
* * * * * cd /path/to/skill && python fastloop_trader.py --live --quiet
```

**Via OpenClaw heartbeat:** Add to your HEARTBEAT.md:
```
Run: cd /path/to/fast market && python fastloop_trader.py --live --quiet
```

## Configuration

Configure via `config.json`, environment variables, or `--set`:

```bash
# Change entry threshold
python fastloop_trader.py --set entry_threshold=0.08

# Trade ETH instead of BTC
python fastloop_trader.py --set asset=ETH

# Multiple settings
python fastloop_trader.py --set min_momentum_pct=0.3 --set max_position=10
```

### Settings

| Setting | Default | Env Var | Description |
|---------|---------|---------|-------------|
| `entry_threshold` | 0.05 | `SIMMER_SPRINT_ENTRY` | Min price divergence from 50¢ to trigger |
| `min_momentum_pct` | 0.5 | `SIMMER_SPRINT_MOMENTUM` | Min BTC % move to trigger |
| `max_position` | 5.0 | `SIMMER_SPRINT_MAX_POSITION` | Max $ per trade |
| `signal_source` | binance | `SIMMER_SPRINT_SIGNAL` | Price feed (binance, coingecko) |
| `lookback_minutes` | 5 | `SIMMER_SPRINT_LOOKBACK` | Minutes of price history |
| `min_time_remaining` | 60 | `SIMMER_SPRINT_MIN_TIME` | Skip fast markets with less time left (seconds) |
| `asset` | BTC | `SIMMER_SPRINT_ASSET` | Asset to trade (BTC, ETH, SOL) |
| `window` | 5m | `SIMMER_SPRINT_WINDOW` | Market window duration (5m or 15m) |
| `volume_confidence` | true | `SIMMER_SPRINT_VOL_CONF` | Weight signal by Binance volume |

### Example config.json

```json
{
  "entry_threshold": 0.08,
  "min_momentum_pct": 0.3,
  "max_position": 10.0,
  "asset": "BTC",
  "window": "5m",
  "signal_source": "binance"
}
```

## CLI Options

```bash
python fastloop_trader.py                    # Dry run
python fastloop_trader.py --live             # Real trades
python fastloop_trader.py --live --quiet     # Silent except trades/errors
python fastloop_trader.py --smart-sizing     # Portfolio-based sizing
python fastloop_trader.py --positions        # Show open fast market positions
python fastloop_trader.py --config           # Show current config
python fastloop_trader.py --set KEY=VALUE    # Update config
```

## Signal Logic

Default signal (Binance momentum):

1. Fetch last 5 one-minute candles from Binance (`BTCUSDT`)
2. Calculate momentum: `(price_now - price_5min_ago) / price_5min_ago`
3. Compare momentum direction to current Polymarket odds
4. Trade when:
   - Momentum ≥ `min_momentum_pct` (default 0.5%)
   - Price diverges from 50¢ by ≥ `entry_threshold` (default 5¢)
   - Volume ratio > 0.5x average (filters out thin moves)

**Example:** BTC up 0.8% in last 5 min, but fast market YES price is only $0.52. The 3¢ divergence from the expected ~$0.55 → buy YES.

### Customizing Your Signal

The default momentum signal is a starting point. To add your own edge:

- **Multi-exchange:** Compare prices across Binance, Kraken, Bitfinex — divergence between exchanges can predict CLOB direction
- **Sentiment:** Layer in Twitter/social signals — a viral tweet can move fast markets
- **Technical indicators:** RSI, VWAP, order flow analysis
- **News:** Breaking news correlation — use your agent's reasoning to interpret headlines

The skill handles all the Simmer plumbing (discovery, import, trade execution). Your agent provides the alpha.

## Example Output

```
⚡ Simmer FastLoop Trading Skill
==================================================

  [DRY RUN] No trades will be executed. Use --live to enable trading.

⚙️  Configuration:
  Asset:            BTC
  Entry threshold:  0.05 (min divergence from 50¢)
  Min momentum:     0.5% (min price move)
  Max position:     $5.00
  Signal source:    binance
  Lookback:         5 minutes
  Min time left:    60s
  Volume weighting: ✓

🔍 Discovering BTC fast markets...
  Found 3 active fast markets

🎯 Selected: Bitcoin Up or Down - February 15, 5:30AM-5:35AM ET
  Expires in: 185s
  Current YES price: $0.480

📈 Fetching BTC price signal (binance)...
  Price: $97,234.50 (was $96,812.30)
  Momentum: +0.436%
  Direction: up
  Volume ratio: 1.45x avg

🧠 Analyzing...
  ⏸️  Momentum 0.436% < minimum 0.500% — skip

📊 Summary: No trade (momentum too weak: 0.436%)
```

## Source Tagging

All trades are tagged with `source: "sdk:fastloop"`. This means:
- Portfolio shows breakdown by strategy
- Other skills won't interfere with your fast market positions
- You can track fast market P&L separately

## Troubleshooting

**"No active fast markets found"**
- Fast markets may not be running (off-hours, weekends)
- Check Polymarket directly for active BTC fast markets

**"No fast markets with >60s remaining"**
- Current window is about to expire, next one isn't live yet
- Reduce `min_time_remaining` if you want to trade closer to expiry

**"Import failed: Rate limit exceeded"**
- Free tier: 10 imports/day. Pro: 50/day
- Fast market trading needs Pro for reasonable frequency

**"Failed to fetch price data"**
- Binance API may be down or rate limited
- Try `--set signal_source=coingecko` as fallback

**"Trade failed: no liquidity"**
- Fast market has thin book, try smaller position size
