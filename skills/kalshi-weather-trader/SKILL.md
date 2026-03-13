---
name: kalshi-weather-trader
description: Trade Kalshi weather markets using NOAA forecasts via Simmer SDK and DFlow on Solana. Port of the popular polymarket-weather-trader. Use when user wants to trade temperature markets on Kalshi, automate weather bets, or check NOAA forecasts.
metadata:
  author: Simmer (@simmer_markets)
  version: "1.0.0"
  displayName: Kalshi Weather Trader
  difficulty: intermediate
  attribution: Strategy inspired by gopfan2, powered by DFlow
---
# Kalshi Weather Trader

Trade temperature markets on Kalshi using NOAA forecast data, via DFlow on Solana.

> **This is a template.** The default signal is NOAA temperature forecasts — remix it with other weather APIs, different forecast models, or additional market types (precipitation, wind, etc.). The skill handles all the plumbing (market discovery, NOAA parsing, trade execution, safeguards). Your agent provides the alpha.

> **Powered by DFlow.** Kalshi trades execute via DFlow's Solana-based prediction market infrastructure. KYC verification through Proof is required for buys.

## When to Use This Skill

Use this skill when the user wants to:
- Trade weather markets on Kalshi (not Polymarket)
- Set up automated temperature trading on Kalshi
- Check their Kalshi weather trading positions
- Configure trading thresholds or locations

## Setup Flow

When user asks to install or configure this skill:

1. **Ask for Simmer API key**
   - They can get it from simmer.markets/dashboard → SDK tab
   - Store in environment as `SIMMER_API_KEY`

2. **Ask for Solana private key** (required for live trading)
   - This is the base58-encoded secret key for their Solana wallet
   - Store in environment as `SOLANA_PRIVATE_KEY`
   - The SDK uses this to sign transactions client-side automatically

3. **Verify KYC**
   - Required for Kalshi buys (not sells)
   - Complete at [dflow.net/proof](https://dflow.net/proof)
   - Check status: `curl "https://api.simmer.markets/api/proof/status?wallet=YOUR_SOLANA_ADDRESS"`

4. **Fund the wallet**
   - SOL on Solana mainnet for transaction fees (~0.01 SOL)
   - USDC on Solana mainnet for trading capital

5. **Ask about settings** (or confirm defaults)
   - Entry threshold: When to buy (default 15¢)
   - Exit threshold: When to sell (default 45¢)
   - Max position: Amount per trade (default $2.00)
   - Locations: Which cities to trade (default NYC)

6. **Save settings to environment variables**

7. **Set up cron** (disabled by default — user must enable scheduling)

## Configuration

| Setting | Environment Variable | Default | Description |
|---------|---------------------|---------|-------------|
| Entry threshold | `SIMMER_WEATHER_ENTRY_THRESHOLD` | 0.15 | Buy when price below this |
| Exit threshold | `SIMMER_WEATHER_EXIT_THRESHOLD` | 0.45 | Sell when price above this |
| Max position | `SIMMER_WEATHER_MAX_POSITION_USD` | 2.00 | Maximum USD per trade |
| Max trades/run | `SIMMER_WEATHER_MAX_TRADES_PER_RUN` | 5 | Maximum trades per scan cycle |
| Locations | `SIMMER_WEATHER_LOCATIONS` | NYC | Comma-separated cities (NYC, Chicago, Seattle, Atlanta, Dallas, Miami) |
| Binary only | `SIMMER_WEATHER_BINARY_ONLY` | false | Skip range-bucket events, only trade binary yes/no markets |
| Smart sizing % | `SIMMER_WEATHER_SIZING_PCT` | 0.05 | % of balance per trade |
| Slippage max | `SIMMER_WEATHER_SLIPPAGE_MAX` | 0.15 | Skip trades with slippage above this (0.15 = 15%) |
| Min liquidity | `SIMMER_WEATHER_MIN_LIQUIDITY` | 0 | Skip markets with liquidity below this USD amount (0 = disabled) |

**Supported locations:** NYC, Chicago, Seattle, Atlanta, Dallas, Miami

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
python weather_trader.py

# Execute real trades
python weather_trader.py --live

# With smart position sizing (uses portfolio balance)
python weather_trader.py --live --smart-sizing

# Check positions only
python weather_trader.py --positions

# View config
python weather_trader.py --config

# Quiet mode — only output on trades/errors (ideal for high-frequency runs)
python weather_trader.py --live --smart-sizing --quiet
```

## How It Works

Each cycle the script:
1. Fetches active weather markets from Simmer API
2. Groups markets by event (each temperature day is one event)
3. Parses event names to get location and date
4. Fetches NOAA forecast for that location/date
5. Finds the temperature bucket that matches the forecast
6. **Safeguards**: Checks context for flip-flop warnings, slippage, time decay
7. **Trend Detection**: Looks for recent price drops (stronger buy signal)
8. **Entry**: If bucket price < threshold and safeguards pass → BUY
9. **Exit**: Checks open positions, sells if price > exit threshold
10. **Tagging**: All trades tagged with `sdk:kalshi-weather` for tracking

## Safeguards

Before trading, the skill checks:
- **Flip-flop warning**: Skips if you've been reversing too much
- **Slippage**: Skips if estimated slippage > 15%
- **Time decay**: Skips if market resolves in < 2 hours
- **Market status**: Skips if market already resolved
- **Kalshi maintenance**: Kalshi's clearinghouse has a weekly maintenance window on Thursdays 3:00-5:00 AM ET — orders during this window will fail

Disable with `--no-safeguards` (not recommended).

## Troubleshooting

**"Safeguard blocked: Severe flip-flop warning"**
- You've been changing direction too much on this market
- Wait before trading again

**"Slippage too high"**
- Market is illiquid, reduce position size or skip

**"No weather markets found"**
- Weather markets may not be active (seasonal)
- Make sure you've imported Kalshi weather markets first: `POST /api/sdk/markets/import/kalshi`

**"KYC verification required"**
- Complete verification at [dflow.net/proof](https://dflow.net/proof)
- Only required for buys, not sells

**"SOLANA_PRIVATE_KEY not set"**
- The SDK signs transactions automatically when this env var is present
- Fix: `export SOLANA_PRIVATE_KEY=<your-base58-secret-key>`

**"Insufficient SOL for transaction fees"**
- Fund your Solana wallet with at least 0.05 SOL for gas

**"API key invalid"**
- Get new key from simmer.markets/dashboard → SDK tab
