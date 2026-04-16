---
name: polymarket-copytrading
description: Mirror positions from top Polymarket traders. Polling mode (free) for portfolio-style copying, Reactor mode (Pro) for event-driven real-time mirroring via Simmer's on-chain signal infrastructure.
metadata:
  author: Simmer (@simmer_markets)
  version: "1.9.0"
  displayName: Polymarket Copytrading
  difficulty: beginner
---
# Polymarket Copytrading

Mirror positions from successful Polymarket traders using the Simmer SDK. Two modes share the same skill, use whichever fits your strategy:

| | **Polling mode** (free) | **Reactor mode** (Pro) |
|---|---|---|
| Entrypoint | `copytrading_trader.py` | `copytrading_trader.py --reactor` |
| Cadence | Batch scan, runs on cron or manual | Polls every 2s for pre-resolved whale signals |
| Latency | Minutes (Polymarket Data API polling) | Seconds (Simmer detects events in real-time, pre-confirmation) |
| Strategy | Size-weighted aggregation across wallets, conviction tiering, rebalance to target allocations, drift/stale filters | Event-by-event mirror with fixed `mirror_fraction` sizing, programmatic filters |
| Best for | Portfolio-aware, multi-whale, periodic scans | Real-time reaction to specific whales as they trade |
| Requires | `SIMMER_API_KEY` | `SIMMER_API_KEY` + Simmer Pro plan |

> **This is a template.** The default logic mirrors whale wallets — remix it with your own wallet selection, sizing rules, filters, or cap logic. The skill handles all the plumbing (signal polling, trade execution, dedup, signing). Your agent provides the alpha.

## Setup Flow

When user asks to install or configure this skill:

1. **Install the Simmer SDK**
   ```bash
   pip install simmer-sdk
   ```

2. **Ask for Simmer API key**
   - They can get it from simmer.markets/dashboard → SDK tab
   - Store in environment as `SIMMER_API_KEY`

3. **Ask for wallet private key** (required for live trading on Polymarket)
   - This is the private key for their Polymarket wallet (the wallet that holds USDC)
   - Store in environment as `WALLET_PRIVATE_KEY`
   - The SDK uses this to sign orders client-side automatically — no manual signing needed
   - Not needed for $SIM paper trading

4. **Ask about settings** (or confirm defaults)
   - Target wallets: Whale addresses to copy
   - Max per position: Amount per trade (default $50)
   - Top N positions: How many positions to track (auto-calculated from balance)
   - Max trades per run: Safety cap (default 10)

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
| Order type | `SIMMER_COPYTRADING_ORDER_TYPE` | GTC |

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

> **By default, only buys execute.** Pass `--rebalance` to also sell positions the whales have exited, or `--whale-exits` to sell only on whale exits.

Each cycle the script:
1. Fetches positions from all target wallets via Simmer API
2. Combines using size-weighted aggregation (larger wallets = more influence)
3. Detects conflicts (one wallet long YES, another long NO) and skips those markets
4. Scores by conviction: positions held by 2+ wallets get full sizing, single-wallet positions get 50% sizing
5. Filters out drifted (>30% from entry) and stale (>90% or <10% price) positions
6. Applies Top-N filtering to concentrate on highest-conviction positions
7. Auto-imports missing markets from Polymarket
8. Calculates rebalance trades to match target allocations
9. Executes trades via Simmer SDK (respects spending limits)
10. Reports results back to user

## Reactor Mode (Pro) — event-driven real-time mirroring

> **Requires Simmer Pro.** The reactor stream is gated by `users.is_pro`. Upgrade at simmer.markets/dashboard if you see a 402 error on connect.

Reactor mode polls Simmer for pre-resolved whale trade signals derived from real-time on-chain settlement data. Simmer detects whale trades as they happen — even before on-chain confirmation — and delivers trade-ready signals to your skill. Unlike polling mode (which batches and rebalances), reactor reacts to each whale trade individually.

### How it's different from polling mode

- **Event-driven, not batched.** Each whale settlement is evaluated and acted on independently.
- **Fixed per-event sizing.** `mirror_fraction` × whale size, capped at `max_size`. No conviction tiering, no rebalance math.
- **Server-side watchlist filter.** Your watchlist + `min_size` are stored in Simmer's reactor config and applied on the server before events reach your skill — you only see matches.
- **Pre-resolved signals.** The server resolves Polymarket condition IDs to Simmer market UUIDs before writing the signal — the skill receives trade-ready payloads.
- **Two run modes.** Loop mode (default, polls every 2s) or `--once` for cron-style single poll and exit.
- **Circuit breaker.** 5 consecutive trade failures → signals are skipped until the next success. Prevents runaway failures from draining your wallet.
- **Server-side dedup.** Signals have a 60-second TTL in Redis and are deleted after successful execution. No local state files needed.
- **Buys only (MVP).** Reactor currently mirrors whale **buys only** — sell signals are filtered out server-side by the relay. If a whale exits a position, reactor won't mirror the sell. Sell mirroring is planned for a future release.

### Configure your reactor watchlist

Reactor uses Simmer-side config (not env vars), so dashboard edits take effect in seconds without restarting the skill.

```bash
# Set your watchlist via the Simmer API
curl -X PATCH "https://api.simmer.markets/api/sdk/reactor/config" \
  -H "Authorization: Bearer $SIMMER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "wallets": ["0x1234...abcd", "0x5678...efgh"],
    "min_size": 1000,
    "max_size": 50,
    "mirror_fraction": 0.01,
    "daily_cap": 100,
    "venue": "sim",
    "enabled": true
  }'
```

Fields:

| Field | Meaning |
|---|---|
| `wallets` | Whale addresses to follow (EVM format, lowercased server-side) |
| `min_size` | Minimum whale trade size to consider (shares) |
| `max_size` | Cap on your mirror trade size (shares) |
| `mirror_fraction` | Fraction of whale size to mirror (e.g. 0.01 = 1%) |
| `daily_cap` | Max total spend per day in venue-native units |
| `venue` | `sim` / `polymarket` / `kalshi` |
| `enabled` | Pause reactor by setting `false` — server will stop delivering events |
| `price_buffer` | Fraction added above whale's fill price for your buy order. Default 0.02 (2%). Prevents order failures on thin books after whale clears liquidity. Range 0–0.2. |

### Run reactor mode

**Recommended: cron with `--once`** — polls for pending signals once and exits. Run on a 1-minute cron for reliable, persistent coverage:

```bash
# Linux crontab
*/1 * * * * cd /path/to/skill && python copytrading_trader.py --reactor --once --live

# OpenClaw cron
openclaw cron add --name "reactor-poll" --cron "*/1 * * * *" --tz UTC --session isolated \
  --message "Run: cd /path/to/skill && python3 copytrading_trader.py --reactor --once"

# One-off check
python copytrading_trader.py --reactor --once
```

> **Why cron?** Reactor signals expire after a short window. A cron ensures your agent checks for signals reliably, even after reboots or process crashes. If your polling process stops, signals expire silently — cron prevents this.

**Advanced: loop mode** — polls every 2s continuously. Lower latency but requires a process manager (launchd, systemd, supervisor) to auto-restart on crash. Not recommended for agent runtimes with exec timeouts.

```bash
# With a process manager (launchd, systemd, supervisor)
python copytrading_trader.py --reactor

# Plain shell (will not auto-restart)
nohup python copytrading_trader.py --reactor > reactor.log 2>&1 &
```

Set `REACTOR_POLL_INTERVAL_SECONDS` to tune the polling cadence (default 2s).

> **Note:** Reactor mode always executes live trades (venue is set in your reactor config). Use `venue: "sim"` in your config to paper trade.

### What happens per signal

1. Skill polls `GET /api/sdk/reactor/pending` for pre-resolved whale signals
2. Circuit breaker check: if 5+ consecutive failures in recent reactions, skip this tick
3. For each signal: compute mirror size (`taker_size × mirror_fraction`, capped by `max_size`, floored at 5-share Polymarket minimum)
4. If below minimum → `skipped_filter` reaction, no trade
5. `SimmerClient.trade()` with `skill_slug="polymarket-copytrading"` and `source="sdk:copytrading:reactor"`
6. On success: DELETE the signal from `/api/sdk/reactor/pending/{tx_hash}` and POST a `mirrored` reaction
7. On failure: POST a `failed` reaction, leave signal (60s TTL clears it)

### Example output

```
[reactor] price_buffer=0.020 (from config)
[reactor] --once: single poll against /api/sdk/reactor/pending
[reactor] 0 pending signals
```

When a whale trade matches your watchlist:

```
[reactor] price_buffer=0.020 (from config)
[reactor] loop mode: polling /api/sdk/reactor/pending every 2.0s
[reactor] 1 pending signal(s)
[reactor] 0xbaa2bc... BUY 7067 shares on "Will Iran strike Iraq by April 30, 2026?"
[reactor] mirror: 70.67 shares @ $0.673 (buffer +2.0%) → GTC order placed
[reactor] ✅ mirrored — trade_id=a23dc52a, signal deleted
```

### External wallets just work

Reactor mode runs in your harness, so `SimmerClient.trade()` signs locally with your existing wallet setup (managed, or external via `WALLET_PRIVATE_KEY`). No server-side signing, no OWS dependency, no new keys to manage.

### When to use polling vs reactor

- **Use polling** when you want portfolio-style copying: aggregate across multiple whales, rebalance to target allocations, run periodically from cron, filter drifted/stale positions. Doesn't require Pro.
- **Use reactor** when you want real-time reaction to individual whale trades, fixed per-event sizing, and pre-resolved signals. Requires Pro.
- **Use both** if you want: polling for your steady-state portfolio alignment + reactor for opportunistic real-time mirroring. Different flags, same skill, same API key.

---

## $SIM Paper Trading

Copytrading supports $SIM mode — mirror whale positions using simulated money on Simmer's LMSR markets. No wallet or USDC required.

```bash
# Paper trade with $SIM (explicit)
python copytrading_trader.py --venue sim --wallets 0x123... --live

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
- Following 2-5 wallets with **overlapping strategies** (e.g. all politics-focused, or all crypto-focused)
- Wallets have similar conviction — mixing very different traders means most positions only appear in one wallet and get reduced sizing (50%)
- Wallets trade markets available on Polymarket

### Conviction Tiers

When following multiple wallets, positions are scored by conviction:

- **High conviction** (held by 2+ wallets): full position sizing (`max_usd`)
- **Low conviction** (held by 1 wallet): 50% position sizing

High-conviction positions are prioritized in Top N selection. Single-wallet positions still trade, but with reduced size. Using just 1 wallet disables conviction scoring (all positions get full sizing).

## Troubleshooting

**"Order too small" / "below minimum (5)"**
- Polymarket requires minimum 5 shares per order
- Increase `--max-usd` or reduce `--top-n` to concentrate into fewer positions

**"No wallets specified"**
- Provide wallet addresses in your message, e.g., "copytrade 0x1234..."
- Or set SIMMER_COPYTRADING_WALLETS environment variable for recurring scans

**"Agent has no USDC balance"**
- Need USDC in your Polymarket wallet, or use `--venue sim` for $SIM paper trading
- Check wallet is linked at simmer.markets/dashboard

**"Conflict skipped"**
- Wallets disagree on this market (one long YES, other long NO)
- Markets with net position < 10% are skipped

**"All N positions filtered (X conflicts, Y drifted, Z stale)"**
- All whale positions were removed by safety filters
- Try different target wallets, or reduce to 1 wallet to disable conviction scoring
- Drift filter skips positions where price moved >30% from whale's entry
- Stale filter skips near-resolved markets (price >90% or <10%)

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
