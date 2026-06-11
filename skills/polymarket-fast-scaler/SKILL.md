---
name: polymarket-fast-scaler
description: Trade Polymarket BTC 5-minute fast markets using a magnitude-gated conviction-ladder strategy. Only fires when |1m BTC momentum| >= 0.10% — the backtested threshold above which the strategy shows positive EV. Position size scales with signal strength (3 conviction tiers). Use when user wants disciplined, gate-filtered BTC fast-market trading.
metadata:
  author: Simmer (@simmer_markets)
  version: "1.0.0"
  displayName: Polymarket FastScaler
  difficulty: advanced
---
# Polymarket FastScaler

Trade Polymarket BTC 5-minute fast markets with a conviction-ladder strategy. Only enters when Binance 1m momentum exceeds the 0.10% magnitude gate — the EV-positive regime validated by backtest. Position size scales with signal strength across 3 tiers.

> 🚨 **Framework, not a production trading system.** Read [DISCLAIMER.md](./DISCLAIMER.md) before connecting to a wallet with real funds.

> **Polymarket only.** All trades execute on Polymarket with real USDC. Paper mode is the default.

> ⚠️ **Strategy invariants.** Do not lower `magnitude_gate_pct` below 0.10% without re-running the backtest. The 89.4% win rate is gated on this threshold. Below it, the strategy enters the noise zone where fees dominate.

> ⚠️ **Risk monitoring does not apply to sub-15-minute markets.** Simmer's stop-loss and take-profit monitors check positions every 15 minutes — they will never fire on 5m markets before resolution. Size accordingly.

## Strategy

**Signal**: Binance 1m candle at window-open — `momentum = (close - open) / open × 100`

**Gate**: `|momentum| >= magnitude_gate_pct` (default 0.10%). Below = no trade.

**Side**: `momentum > 0 → YES`, `momentum < 0 → NO`. Pure direction — no divergence filter.

**Sizing (conviction ladder)**:
| Tier | |momentum| | Position |
|------|-----------|----------|
| 1 | 0.10% – 0.20% | $3 |
| 2 | 0.20% – 0.35% | $5 |
| 3 | ≥ 0.35% | $10 |

**Hold**: position held to expiry. No exit logic.

**Why this works**: Polymarket fast markets resolve on Chainlink oracle snapshots. At the moment of the 1m window-open candle, strong BTC momentum correlates with the resolution direction before the market re-prices. The 0.10% gate filters the noise zone where this correlation collapses and fees dominate.

**Backtest (BTC 5m, 30d)**: +5.04% gross / 218 markets / 89.4% win rate at |momentum| ≥ 0.10%. Past performance does not guarantee future results — see DISCLAIMER.md.

## When to Use This Skill

Use this skill when the user wants to:
- Trade BTC 5-minute fast markets on Polymarket with a validated magnitude filter
- Automate conviction-scaled position sizing based on signal strength
- Run a disciplined, gate-filtered fast-market strategy (not raw momentum)

**Do NOT use for**: ETH/SOL/XRP fast markets (separate backtest required), 15m windows (backtest pending), or any strategy where the user wants to trade below the 0.10% magnitude gate.

## Setup Flow

1. **Install the Simmer SDK**
   ```bash
   pip install simmer-sdk
   ```

2. **Set your Simmer API key**
   ```bash
   export SIMMER_API_KEY="your-key-here"
   # Get from: simmer.markets/dashboard → SDK tab
   ```

3. **Run in paper mode first**
   ```bash
   python fast_scaler.py
   ```

4. **Set up cron (every minute)**
   ```bash
   # crontab -e
   * * * * * cd /path/to/skill && python fast_scaler.py --live --quiet
   ```

## Quick Start

```bash
# Paper mode (default) — see what the strategy would do
python fast_scaler.py

# Live trading
python fast_scaler.py --live

# Live + quiet (for cron)
python fast_scaler.py --live --quiet

# Show current positions
python fast_scaler.py --positions

# Tune the magnitude gate (don't go below 0.10%)
python fast_scaler.py --set magnitude_gate_pct=0.12

# Adjust position sizes
python fast_scaler.py --set position_tier3_usd=15
```

## Key Config Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `magnitude_gate_pct` | 0.10 | Min \|momentum\| % to trade. **Strategy invariant — don't lower below 0.10.** |
| `ladder_tier2_pct` | 0.20 | Momentum threshold to enter tier 2 sizing |
| `ladder_tier3_pct` | 0.35 | Momentum threshold to enter tier 3 (max) sizing |
| `position_tier1_usd` | 3.0 | Position size for tier 1 |
| `position_tier2_usd` | 5.0 | Position size for tier 2 |
| `position_tier3_usd` | 10.0 | Position size for tier 3 |
| `daily_budget_usd` | 30.0 | Max total USD per day across all trades |
| `per_market_cap_usd` | 10.0 | Max USD on a single market window |
| `asset` | BTC | Asset (BTC only in v1.0) |
| `window` | 5m | Window (5m only in v1.0) |
| `order_type` | GTC | GTC or FAK |

## What to Watch For

- **Gate fires ~7×/day** on BTC under normal conditions (0.10% threshold). Significantly fewer = check if markets are live and Binance is reachable. Significantly more = consider raising the gate.
- **Per-market cap** (default $10) prevents stacking multiple bets on the same slot. Leave it at or below `position_tier3_usd`.
- **Daily budget** (default $30) is the safety cap. At 7 trades/day × avg $5 = $35, the default may cut the last ~1 trade. Increase if you want full daily exposure.

## Invariants — don't raise without re-backtesting

The +5.04% / 89.4% backtest result is conditional on the parameter values it was run against. Three values are **invariants** — changing them invalidates the backtest and the skill becomes an unvalidated experiment:

- **`magnitude_gate_pct ≥ 0.10`** — lowering admits noise; backtest win rate degrades fast below 0.10%.
- **`position_tier3_usd ≤ 10`** — the conviction ladder ($3/$5/$10) is sized for the observed win-rate distribution. Raising T3 without re-running the backtest scales bet size beyond what the empirical edge supports.
- **`daily_budget_usd ≤ 50`** — the daily budget caps total daily exposure. Raising it past ~$50 admits parameter combinations the backtest didn't cover.

Lowering any of these is always safe. Raising them is your call but invalidates the empirical evidence cited above.

## Geo-fallback (Binance.us)

The skill fetches BTC 1m klines from `api.binance.com`. In geo-restricted regions (e.g. US-hosted Railway deployments) Binance returns HTTP 451. The skill auto-falls-back to `api.binance.us` in that case. If both endpoints are unreachable, market discovery returns nothing for that cycle — the skill exits cleanly with no orders placed.

If you're running this on a host that can reach neither endpoint, you'll need to proxy/VPN the request or run the skill from a host that can reach Binance.

## Gamma fallback liveness gap

When the Simmer SDK's primary market-discovery path is unavailable, the skill falls back to Polymarket's Gamma API. Gamma-sourced markets come through without `is_live_now` precision — the skill uses a time-window heuristic instead. This can occasionally admit a market that has time remaining on the clock but isn't yet in the live trading window. Known gap; tracked for future fix.

## Advanced: Extending the Strategy

The conviction ladder and magnitude gate are the two load-bearing components. All other parameters are tunable without invalidating the backtest. If you want to:

- **Use ETH/SOL**: change `asset` — but run your own backtest first. The 0.10% gate and 89.4% win rate are BTC-only results.
- **Use 15m markets**: change `window` — 15m backtest is on the roadmap but not yet run.
- **Tighten the ladder**: raise `ladder_tier2_pct` / `ladder_tier3_pct` for fewer but higher-conviction trades.
- **Cap exposure**: lower `position_tier3_usd` or `daily_budget_usd`.

## Caveats

- Fast markets resolve on Chainlink, not Binance. The strategy trades the correlation, not the exact price.
- Spreads on newly opened fast markets can be wide — the 10% spread cap filters most of these.
- This is an alpha skill (status: scaffold). Run paper validation before committing real funds.
