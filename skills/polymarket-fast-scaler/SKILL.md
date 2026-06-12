---
name: polymarket-fast-scaler
description: Trade Polymarket BTC 5-minute fast markets using a magnitude-gated conviction-ladder strategy. Only fires when |1m BTC momentum| >= 0.10%, the magnitude threshold the strategy is built around. Position size scales with signal strength (3 conviction tiers). Reference template for gate-filtered BTC fast-market trading; the original performance claim was retracted (see below).
metadata:
  author: Simmer (@simmer_markets)
  version: "1.0.0"
  displayName: Polymarket FastScaler
  difficulty: advanced
---
# Polymarket FastScaler

Trade Polymarket BTC 5-minute fast markets with a conviction-ladder strategy. Only enters when Binance 1m momentum exceeds the 0.10% magnitude gate. Position size scales with signal strength across 3 tiers.

> 🚨 **Framework, not a production trading system.** Read [DISCLAIMER.md](./DISCLAIMER.md) before connecting to a wallet with real funds.

> **Polymarket only.** All trades execute on Polymarket with real USDC. Paper mode is the default.

> ⛔ **Performance claim retracted (2026-06-12).** An earlier version cited an "89.4% win rate / +5.04%" backtest. A look-ahead-enforced replay (Simmer skill-replay) found that backtest measured the 1m candle that *starts* at window-open, which only closes 60 seconds into the 5-minute window it is meant to predict. The signal the skill can actually act on at the decision point (the last complete 1m candle, i.e. the prior minute) shows **no measured edge**: roughly a coin flip before fees. Treat this skill as an **unvalidated reference template**, not a validated edge. Run paper mode and form your own view.

> ⚠️ **Strategy invariants.** `magnitude_gate_pct` defaults to 0.10%. Below it the gate admits more low-magnitude noise and fees dominate a larger share of trades. This is a design default, not a validated profit threshold (see the retraction above).

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

**Strategy thesis (unvalidated)**: the idea is that strong BTC momentum at window-open correlates with the resolution direction before the market re-prices, and that the 0.10% gate filters the noise zone. Replay testing did **not** confirm this for the signal available at the decision point (the prior complete 1m candle). Treat the thesis as untested.

**Backtest status (2026-06-12)**: the original +5.04% / 89.4% backtest was **retracted**. A look-ahead-enforced replay found it used in-window price action (the first minute *inside* the window) that is not available when the entry decision is made. The live-actionable signal showed no measured edge. See the retraction note at the top and DISCLAIMER.md.

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

## Risk-envelope defaults — don't raise without your own validation

The original backtest that motivated these values was retracted (see the top), so treat them as **conservative risk-envelope defaults**, not edge-preserving constraints. There is no validated edge to preserve; these just bound exposure:

- **`magnitude_gate_pct ≥ 0.10`** — lowering admits more low-magnitude noise and trades a larger share of marginal signals.
- **`position_tier3_usd ≤ 10`** — caps the largest single bet in the conviction ladder ($3/$5/$10).
- **`daily_budget_usd ≤ 50`** — caps total daily exposure.

Lowering any of these reduces exposure. Raising them increases it, with no validated edge to justify the larger size.

## Geo-fallback (Binance.us)

The skill fetches BTC 1m klines from `api.binance.com`. In geo-restricted regions (e.g. US-hosted Railway deployments) Binance returns HTTP 451. The skill auto-falls-back to `api.binance.us` in that case. If both endpoints are unreachable, market discovery returns nothing for that cycle — the skill exits cleanly with no orders placed.

If you're running this on a host that can reach neither endpoint, you'll need to proxy/VPN the request or run the skill from a host that can reach Binance.

## Gamma fallback liveness gap

When the Simmer SDK's primary market-discovery path is unavailable, the skill falls back to Polymarket's Gamma API. Gamma-sourced markets come through without `is_live_now` precision — the skill uses a time-window heuristic instead. This can occasionally admit a market that has time remaining on the clock but isn't yet in the live trading window. Known gap; tracked for future fix.

## Advanced: Extending the Strategy

The conviction ladder and magnitude gate are the two load-bearing components. If you want to:

- **Use ETH/SOL**: change `asset`, and run your own validation first. The 0.10% gate is a BTC-only default; there is no validated win rate to cite.
- **Use 15m markets**: change `window`. Untested.
- **Tighten the ladder**: raise `ladder_tier2_pct` / `ladder_tier3_pct` for fewer but higher-conviction trades.
- **Cap exposure**: lower `position_tier3_usd` or `daily_budget_usd`.

## Caveats

- Fast markets resolve on Chainlink, not Binance. The strategy trades the correlation, not the exact price.
- Spreads on newly opened fast markets can be wide — the 10% spread cap filters most of these.
- This is an alpha skill (status: scaffold). Run paper validation before committing real funds.
