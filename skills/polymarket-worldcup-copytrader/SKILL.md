---
name: polymarket-worldcup-copytrader
description: Copy the top World Cup traders on Polymarket — auto-curated daily by Simmer. No wallet list to configure; the skill sources leaders via PolyNode's slippage-adjusted copy-PnL screen. Regular mode (daily rebalance). Free tier.
category: world-cup
tags:
  - world-cup
  - soccer
  - copytrading
metadata:
  author: Simmer (@simmer_markets)
  version: "0.1.0"
  displayName: World Cup Copytrader
  difficulty: beginner
  sensitivity: sensitive
  sensitivity_reason: "Novel-risk automation: the skill executes trades without per-trade approval by mirroring a curated set of external wallets. Copyability screening reduces slippage risk; it does not remove market risk. Scope approved 2026-06-10."
---
# World Cup Copytrader

Copy the top World Cup traders on Polymarket using Simmer's auto-curated leader set. No
wallet list to configure — Simmer's daily curation job screens the top WC traders for
copyability (slippage-adjusted copy P&L via PolyNode) and serves the qualified set.

> 🚨 **Read [DISCLAIMER.md](./DISCLAIMER.md) before going live.** This skill executes trades
> automatically. Dry-run is the default; pass `--live` to execute.

> **Sim-first.** The default venue is `$SIM` (Simmer's LMSR). Validate the skill works
> correctly on sim before switching to `--venue polymarket` with real USDC.

> **Copyability screening reduces slippage risk; it does not remove market risk.**

## What it does

1. Fetches the daily-curated WC leader set from Simmer (`GET /api/sdk/wc/copy-leaders`).
2. Runs the leaders' wallets through Simmer's copytrading engine to compute a
   portfolio-level rebalance: size-weighted aggregation across all leaders, conflict
   detection, Top-N filtering, drift/stale checks.
3. Executes the rebalance trades via your Simmer wallet (managed or self-custody).

The curation pipeline runs once daily at 02:00 UTC: PolyNode top-traders → PolyNode
slippage-adjusted copy-PnL screen (`exclude_toxic=true`) → top-10 copyable WC sharps.
You follow this set, not wallets you chose yourself.

## How it differs from `polymarket-copytrading`

| | **polymarket-copytrading** | **polymarket-worldcup-copytrader** |
|---|---|---|
| Wallet list | User configures manually | Auto-curated from server |
| Scope | All Polymarket markets | World Cup markets only |
| Curation | None (follows whoever you set) | PolyNode copy-PnL screen |
| Modes | Polling + Reactor | Regular (daily rebalance) |
| Tier | Free | Free |

## Setup

1. **Install the Simmer SDK** (0.17.27 or newer):
   ```bash
   pip install -U 'simmer-sdk>=0.17.27'
   ```

2. **Set your Simmer API key**:
   ```bash
   export SIMMER_API_KEY=...   # simmer.markets/dashboard → SDK tab
   ```

3. **Optional — Polymarket wallet key** (only for `--venue polymarket --live`):
   ```bash
   export WALLET_PRIVATE_KEY=0x...
   ```
   Not needed for `$SIM` paper trading.

## Quick start (sim-first)

```bash
# 1. Dry run on sim — show what would trade, no orders placed
python copytrader.py

# 2. Live on sim — real trades using $SIM (no real money)
python copytrader.py --live

# 3. Show leader set
python copytrader.py --leaders

# 4. Show positions
python copytrader.py --positions

# 5. Live on Polymarket (real USDC — only after sim validation)
python copytrader.py --venue polymarket --live
```

## Running on a schedule

Run daily (after 02:00 UTC when leaders refresh):

```bash
# Linux crontab — daily at 03:00 UTC
0 3 * * * cd /path/to/skill && python copytrader.py --live

# OpenClaw daily cron
openclaw cron add --name "wc-copytrader" --cron "0 3 * * *" --tz UTC \
  --message "Run: cd /path/to/skill && python copytrader.py --live"
```

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `SIMMER_API_KEY` | — | Required. Your Simmer SDK API key. |
| `TRADING_VENUE` | `sim` | Venue: `sim` for $SIM, `polymarket` for real USDC. |
| `WC_COPYTRADER_MAX_USD` | `30` | Max per-position size in USDC / $SIM. |
| `WC_COPYTRADER_MAX_TRADES` | `10` | Max trades per run. |
| `WC_COPYTRADER_BUY_ONLY` | `true` | Buy-only mode. Set `false` for full rebalance. |
| `WC_COPYTRADER_DETECT_EXITS` | `true` | Sell when leaders exit. |
| `WC_COPYTRADER_MIN_LEADERS` | `5` | Minimum curated leaders required to trade. Below this the run exits cleanly (degraded-cache guard). |
| `WC_COPYTRADER_MAX_SLIPPAGE` | `0.02` | Max slippage vs the plan price, as a fraction. Live Polymarket orders are price-capped at `estimated_price × (1 ± this)`. Clamped to [0.005, 0.10]. |
| `WALLET_PRIVATE_KEY` | — | External / self-custody Polymarket key (Polymarket venue only). |

## Options

```
--live             Execute trades (default: dry-run)
--dry-run          Show trade plan without executing
--positions        Show current positions
--leaders          Show current curated leader set
--config           Show configuration
--venue            sim | polymarket  (overrides TRADING_VENUE)
--rebalance        Buy + sell to fully match leaders (default: buy-only)
--no-exits         Disable leader-exit detection
```

## Order handling

All live orders are placed as **FAK** (fill-and-kill): whatever is available at the
quoted price fills immediately and the rest is cancelled. The skill never leaves
resting limit orders on the book. This matters for a once-daily fire-and-forget
automation — each run recomputes its plan from current *positions*, not open orders,
so a resting GTC from a previous run could double-fill later and silently bypass the
`WC_COPYTRADER_MAX_USD` / `WC_COPYTRADER_MAX_TRADES` caps. The cost of FAK is that
thin books may give partial fills; the next daily run simply tops up.

Live Polymarket orders are also **price-bounded**: each FAK carries a limit price of
the plan's `estimated_price` ± `WC_COPYTRADER_MAX_SLIPPAGE` (default 2%), so a market
that moved between planning and execution can't fill at an arbitrarily worse price —
the unfillable remainder is killed (recorded as a failed trade, nothing rests). A
planned trade with no usable `estimated_price` is skipped rather than sent unbounded.
The cap is rounded directionally to the coarsest Polymarket tick (1¢ — buys floor,
sells ceil) so tick rounding at signing time can never push the price outside the
bound; on finer-tick markets this gives up under a cent of headroom.

## Cold-start note

At the start of the tournament, `min_trade_count` filtering means the leader set may
be small until enough fills accumulate. The daily curation widens the lookback window
(7d → 14d → 30d → 90d) until at least 10 leaders qualify. If the cache is empty or
returns fewer than expected leaders, the skill exits cleanly and retries on the next
scheduled run.

## Troubleshooting

**"Leader cache not yet populated"**
- The daily curation job runs at 02:00 UTC. Run after that time.
- Check curation status: `python copytrader.py --leaders`

**"No trades needed"**
- Your portfolio already mirrors the leaders. Normal result on subsequent runs.

**"Conflict skipped"**
- Some leaders disagree on a market. The engine skips conflicted markets.

**"Insufficient balance"**
- Reduce `WC_COPYTRADER_MAX_USD` or fund your wallet.
- For $SIM: each market starts with a $10,000 $SIM balance.

**"External wallet requires a pre-signed order"**
- `WALLET_PRIVATE_KEY` is not set. Required for `--venue polymarket --live` with an
  external wallet. Not needed for managed wallets or `--venue sim`.
