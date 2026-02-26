---
name: polymarket-copytrading
displayName: Polymarket Copytrading
description: Mirror positions from top Polymarket traders using Simmer API. Size-weighted aggregation across multiple wallets.
metadata: {"clawdbot":{"emoji":"🐋","requires":{"env":["SIMMER_API_KEY"],"pip":["simmer-sdk"]},"cron":null,"autostart":false,"automaton":{"managed":true,"entrypoint":"copytrading_trader.py"},"tunables":[{"env":"SIMMER_COPYTRADING_MAX_USD","type":"number","default":50,"range":[1,200],"step":5,"label":"Max bet per trade"},{"env":"SIMMER_COPYTRADING_TOP_N","type":"number","default":5,"range":[1,20],"step":1,"label":"Positions to track"},{"env":"SIMMER_COPYTRADING_MAX_TRADES","type":"number","default":5,"range":[1,20],"step":1,"label":"Max trades per run"}]}}
authors:
  - Simmer (@simmer_markets)
version: "1.4.0"
published: true
---

# Polymarket Copytrading

Mirror positions from successful Polymarket traders using the Simmer SDK.

> **This is a template.** The default logic mirrors whale wallets by size-weighted allocation — remix it with your own wallet selection criteria, position filters, or rebalancing rules. The skill handles all the plumbing (wallet fetching, conflict detection, trade execution). Your agent provides the alpha.

## When to Use This Skill

Use this skill when the user wants to:
- Copytrade whale wallets on Polymarket
- Paper trade (copytrade with $SIM) to test strategies without real money
- Check what positions a wallet holds
- Follow specific trader addresses
- Check their copytrading positions

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

## Finding Whale Wallets

- **[predicting.top](https://predicting.top)** — Leaderboard of top Polymarket traders with wallet addresses
- **[alphawhale.trade](https://alphawhale.trade)** — Tools for copying and tracking top performers
- **Polymarket Leaderboard** — Official rankings (requires account)

## Quick Start (Ad-Hoc Usage)

**User provides wallet(s) directly in chat:**
```
User: "Copytrade this wallet: 0x1234...abcd"
User: "What positions does 0x5678...efgh have?"
User: "Follow these whales: 0xaaa..., 0xbbb..."
```

→ Run with `--wallets` flag:
```bash
python copytrading_trader.py --wallets 0x1234...abcd
python copytrading_trader.py --wallets 0xaaa...,0xbbb... --dry-run
```

This is the simplest way - no setup needed, just pass wallets directly.

## Persistent Setup (Optional)

For automated recurring scans, wallets can be saved in environment:

| Setting | Environment Variable | Default |
|---------|---------------------|---------|
| Target wallets | `SIMMER_COPYTRADING_WALLETS` | (none) |
| Top N positions | `SIMMER_COPYTRADING_TOP_N` | auto |
| Max per position | `SIMMER_COPYTRADING_MAX_USD` | 50 |
| Max trades/run | `SIMMER_COPYTRADING_MAX_TRADES` | 10 |

**Top N auto-calculation (when not specified):**
- Balance < $50: Top 5 positions
- Balance $50-200: Top 10 positions
- Balance $200-500: Top 25 positions
- Balance $500+: Top 50 positions

**Polymarket Constraints:**
- Minimum 5 shares per order
- SDK enforces $1.00 minimum position value (filters dust positions)

> ⚠️ **Start Conservative:** Begin with small amounts (`--max-usd 5-10`) and `--dry-run` to understand how the skill behaves before scaling up.

## How It Works

Each cycle the script:
1. Fetches positions from all target wallets via Simmer API
2. Combines using size-weighted aggregation (larger wallets = more influence)
3. Detects conflicts (one wallet long YES, another long NO) and skips those markets
4. Applies Top-N filtering to concentrate on highest-conviction positions
5. Auto-imports missing markets from Polymarket
6. Calculates rebalance trades to match target allocations
7. Executes trades via Simmer SDK (respects spending limits)
8. Reports results back to user

## $SIM Paper Trading

Copytrading supports $SIM mode — mirror whale positions using simulated money on Simmer's LMSR markets. No wallet or USDC required.

```bash
# Paper trade with $SIM (explicit)
python copytrading_trader.py --venue simmer --wallets 0x123... --live

# Auto-detect: if your account has no linked wallet, $SIM is used automatically
python copytrading_trader.py --wallets 0x123... --live
```

In $SIM mode:
- Trades execute on Simmer's LMSR at real Polymarket prices
- Each market gets an independent $10K $SIM balance
- Positions tracked in your Simmer portfolio (source: `sdk:copytrading`)
- Whale signals still come from real Polymarket data

## Running the Skill

**Run a scan (dry run by default — no trades):**
```bash
python copytrading_trader.py
```

**Execute real trades:**
```bash
python copytrading_trader.py --live
```

**Check positions only:**
```bash
python copytrading_trader.py --positions
```

**View current config:**
```bash
python copytrading_trader.py --config
```

**Override wallets for one run:**
```bash
python copytrading_trader.py --wallets 0x123...,0x456...
```

**Full rebalance mode (includes sells):**
```bash
python copytrading_trader.py --rebalance
```

**Sell when whales exit positions:**
```bash
python copytrading_trader.py --whale-exits
```

## Reporting Results

After each run, message the user with:
- Current configuration (wallets, Top N, max position)
- Number of wallets fetched and total positions found
- Markets skipped due to conflicts
- Trades executed (or skipped with reason)
- Current portfolio positions

Example output to share:
```
🐋 Copytrading Scan Complete

Configuration:
• Following 2 wallets
• Top 10 positions, max $50 each
• Balance: $250.00 USDC

Fetched positions:
• 0x1234...abcd: 15 positions
• 0x5678...efgh: 22 positions
• Combined: 28 unique markets
• Conflicts skipped: 2

Top 10 by allocation:
1. "Will BTC hit $100k?" - 18.5% → BUY YES
2. "Trump pardons X?" - 12.3% → BUY NO
3. "Fed rate cut Jan?" - 9.8% → Already held
...

Trades executed: 4 buys ($180 total)
• Bought 45 YES shares on "Will BTC hit $100k?" @ $0.82
• Bought 120 NO shares on "Trump pardons X?" @ $0.15
...

Next scan in 4 hours.
```

## Example Conversations

**User: "Copytrade 0x1234...abcd"**
→ Run: `python copytrading_trader.py --wallets 0x1234...abcd`
→ Report what positions that wallet has and what trades would execute

**User: "What is 0x5678...efgh holding?"**
→ Run: `python copytrading_trader.py --wallets 0x5678...efgh --dry-run`
→ Show their positions without trading

**User: "Follow these wallets: 0xaaa..., 0xbbb..., 0xccc..."**
→ Run: `python copytrading_trader.py --wallets 0xaaa...,0xbbb...,0xccc...`
→ Aggregate positions across all wallets, report results

**User: "Copytrade this whale but only top 5 positions"**
→ Run: `python copytrading_trader.py --wallets 0x... --top-n 5`

**User: "How are my positions doing?"**
→ Run: `python copytrading_trader.py --positions`
→ Show current Polymarket positions with P&L

**User: "Show copytrading config"**
→ Run: `python copytrading_trader.py --config`
→ Display current settings

**User: "Sell positions that whales have exited"**
→ Run: `python copytrading_trader.py --whale-exits`
→ Compares your positions to whales, sells any they've closed

**User: "Do a full rebalance to match the whales"**
→ Run: `python copytrading_trader.py --rebalance`
→ Includes both buys AND sells to match whale allocations

## Finding Good Wallets to Follow

Common approaches:
1. **Leaderboard tracking**: Check Polymarket leaderboards for consistent performers
2. **Whale watchers**: Follow known profitable traders on social media
3. **Specific strategies**: Follow wallets known for weather, politics, or crypto trades

The skill works best when:
- Following 2-5 wallets for diversification
- Wallets have similar conviction (don't mix degen with conservative)
- Wallets trade markets available on Polymarket

## Troubleshooting

**"Order too small" / "below minimum (5)"**
- Polymarket requires minimum 5 shares per order
- Increase `--max-usd` or reduce `--top-n` to concentrate into fewer positions

**"No wallets specified"**
- Provide wallet addresses in your message, e.g., "copytrade 0x1234..."
- Or set SIMMER_COPYTRADING_WALLETS environment variable for recurring scans

**"Agent has no USDC balance"**
- Need USDC in your Polymarket wallet, or use `--venue simmer` for $SIM paper trading
- Check wallet is linked at simmer.markets/dashboard

**"Conflict skipped"**
- Wallets disagree on this market (one long YES, other long NO)
- Markets with net position < 10% are skipped

**"Insufficient balance"**
- Not enough USDC for all trades
- Reduce SIMMER_COPYTRADING_TOP_N or SIMMER_COPYTRADING_MAX_USD

**"Market could not be imported"**
- Some markets may not be importable (resolved, private, etc.)
- These are skipped automatically

**"External wallet requires a pre-signed order"**
- `WALLET_PRIVATE_KEY` is not set in the environment
- The SDK signs orders automatically when this env var is present — no manual signing code needed
- Fix: `export WALLET_PRIVATE_KEY=0x<your-polymarket-wallet-private-key>`
- Do NOT attempt to sign orders manually or modify the skill code — the SDK handles it

**"Balance shows $0 but I have USDC on Polygon"**
- Polymarket uses **USDC.e** (bridged USDC, contract `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174`) — not native USDC
- If you bridged USDC to Polygon recently, you likely received native USDC
- Swap native USDC to USDC.e, then retry
