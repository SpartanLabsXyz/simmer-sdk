---
name: polymarket-btc-up-down-trader
description: Trade Polymarket BTC daily and weekly UP/DOWN markets with empirically-anchored exit discipline. Enters on CEX momentum divergence; exits automatically on time cap, volume spike, or target capture. Use when the user wants to trade BTC direction markets (hours/days duration), not fast 5-minute markets.
metadata:
  author: Simmer (@simmer_markets)
  version: "1.0.0"
  displayName: Polymarket BTC Up-Down Trader
  difficulty: intermediate
---
# Polymarket BTC Up-Down Trader

Trade Polymarket's BTC daily and weekly UP/DOWN markets with built-in exit discipline. Enters on Binance momentum signals, exits automatically before resolution tail risk bites.

> **Polymarket only.** All trades execute on Polymarket with real USDC. Use `--live` for real trades; default is dry run.

> **Not for fast (5m/15m) markets.** Use `polymarket-fast-loop` for those. This skill targets daily and weekly BTC direction markets with hours-to-days of duration.

> ⚠️ **BTC UP/DOWN markets carry Polymarket's 10% fee on crypto markets.** Factor this into your minimum edge threshold.

## When to Use This Skill

Use this skill when the user wants to:
- Trade BTC daily or weekly UP/DOWN prediction markets on Polymarket
- Apply systematic exit discipline (don't hold to settlement)
- Use CEX price momentum as the entry signal
- Monitor open positions and auto-exit when targets are hit

## Setup Flow

1. **Install the Simmer SDK**
   ```bash
   pip install simmer-sdk
   ```

2. **Set your API key**
   - Get from simmer.markets/dashboard → SDK tab
   - `export SIMMER_API_KEY="your-key-here"`

3. **Set wallet private key** (required for live trading)
   - `export WALLET_PRIVATE_KEY="0x..."`
   - Not needed for dry-run or managed wallets.

4. **Configure exit discipline** (optional — defaults are well-tuned)
   ```bash
   python strategy.py --config
   ```

## Quick Start

```bash
# Dry run — see what would happen
python strategy.py

# Go live
python strategy.py --live

# Monitor-only (check exits without new entries)
python strategy.py --live --monitor

# Quiet mode for cron
python strategy.py --live --quiet
```

## How to Run on a Loop

**Linux crontab:**
```
# Every 30 minutes
*/30 * * * * cd /path/to/skill && python strategy.py --live --quiet
```

**OpenClaw native cron:**
```bash
openclaw cron add \
  --name "BTC Up-Down Trader" \
  --cron "*/30 * * * *" \
  --session isolated \
  --message "Run BTC up-down trader: cd /path/to/skill && python strategy.py --live --quiet"
```

## Configuration

All settings configurable via `config.json`, environment variables, or `--set`:

```bash
python strategy.py --set exit_before_resolution_hours=2.0
python strategy.py --set volume_spike_exit_multiplier=4.0
python strategy.py --set target_hit_capture_pct=0.75
```

### Exit Discipline Knobs

| Setting | Default | Env Var | Description |
|---------|---------|---------|-------------|
| `exit_before_resolution_hours` | `1.0` | `SIMMER_BTCUD_EXIT_BEFORE_RESOLUTION_HOURS` | Close position this many hours before scheduled resolution. Disable with `0`. |
| `volume_spike_exit_multiplier` | `3.0` | `SIMMER_BTCUD_VOLUME_SPIKE_MULTIPLIER` | Exit when 10-minute CLOB volume hits N× the rolling baseline. Disable with `0`. |
| `target_hit_capture_pct` | `0.85` | `SIMMER_BTCUD_TARGET_HIT_CAPTURE_PCT` | Exit when position has captured 85% of the theoretical max gain. Disable with `0`. |
| `volume_baseline_windows` | `6` | `SIMMER_BTCUD_VOLUME_BASELINE_WINDOWS` | Number of prior 10-minute windows used to compute volume baseline. |

**Why these defaults?** Exit behavior on short-dated prediction markets skews heavily toward early exits — the final hours before resolution compress remaining upside while tail risk (last-minute reversals, oracle variance) stays constant. The defaults are calibrated to exit early while capturing the bulk of the move. Empirical work on short-dated crypto UP/DOWN exit patterns informed the calibration; adjust based on your own backtest results.

### Entry Knobs

| Setting | Default | Env Var | Description |
|---------|---------|---------|-------------|
| `entry_threshold` | `0.05` | `SIMMER_BTCUD_ENTRY_THRESHOLD` | Min price divergence from 50¢ to enter |
| `min_momentum_pct` | `0.3` | `SIMMER_BTCUD_MOMENTUM_THRESHOLD` | Min BTC % move (30-min lookback) to trigger |
| `max_position` | `10.0` | `SIMMER_BTCUD_MAX_POSITION_USD` | Max $ per trade |
| `daily_budget` | `50.0` | `SIMMER_BTCUD_DAILY_BUDGET_USD` | Max total entry spend per UTC day |
| `lookback_minutes` | `30` | `SIMMER_BTCUD_LOOKBACK_MINUTES` | Lookback window for BTC momentum signal |
| `min_hours_to_resolution` | `4.0` | `SIMMER_BTCUD_MIN_HOURS_TO_RESOLUTION` | Skip entry if market resolves in fewer than N hours |

### Example config.json

```json
{
  "exit_before_resolution_hours": 1.0,
  "volume_spike_exit_multiplier": 3.0,
  "target_hit_capture_pct": 0.85,
  "entry_threshold": 0.05,
  "min_momentum_pct": 0.3,
  "max_position": 10.0,
  "daily_budget": 50.0
}
```

## Exit Logic

Each run cycle evaluates open positions against three triggers, in priority order:

### 1. `time_cap` — Time-based exit
Closes position when the market is within `exit_before_resolution_hours` of resolution.

```
hours_to_resolution ≤ exit_before_resolution_hours → EXIT (time_cap)
```

This is the hard floor. Holding to settlement adds tail risk (oracle variance, thin liquidity, last-minute manipulation) with little remaining upside on a near-resolved market.

### 2. `target_hit` — Profit capture exit
Closes position when it has captured `target_hit_capture_pct` of the theoretical maximum gain.

```
YES positions: (current_price - entry_price) / (1.0 - entry_price) ≥ 0.85 → EXIT (target_hit)
NO positions:  (entry_price - current_price) / entry_price ≥ 0.85 → EXIT (target_hit)
```

Example: Bought YES at $0.40. Maximum gain = 1.0 - 0.40 = $0.60/share. At $0.91, captured = (0.91 - 0.40) / 0.60 = 85% → exit.

### 3. `volume_spike` — Smart-money volume exit
Closes position when 10-minute CLOB volume exceeds `volume_spike_exit_multiplier`× the rolling baseline.

```
current_10m_volume / avg_baseline_volume ≥ 3.0 → EXIT (volume_spike)
```

A sudden surge in volume near resolution typically signals that informed traders have positioned ahead of outcome certainty. Exiting into the spike captures the liquidity premium.

## Exit Reasons

Every exit logs one of four reasons:

| Reason | Trigger |
|--------|---------|
| `time_cap` | Hours to resolution ≤ `exit_before_resolution_hours` |
| `target_hit` | Captured ≥ `target_hit_capture_pct` of max gain |
| `volume_spike` | 10m volume ≥ `volume_spike_exit_multiplier` × baseline |
| `manual` | User-initiated close via dashboard or SDK |

## CLI Reference

```bash
python strategy.py                      # Dry run (show positions + opportunities)
python strategy.py --live               # Execute real trades
python strategy.py --monitor            # Only run exit checks (no new entries)
python strategy.py --positions          # Show open positions
python strategy.py --config             # Show current config
python strategy.py --set KEY=VALUE      # Update config
python strategy.py --quiet              # Only output on trades/errors
```

## Source Tagging

All trades are tagged `source: "sdk:btcupdown"`. This keeps BTC UP/DOWN P&L separate from other strategies in your Simmer portfolio.

## Troubleshooting

**"No active BTC UP/DOWN markets found"**
- Gamma API may be slow or markets may not be trading. Try again in a few minutes.
- Check polymarket.com directly for active BTC UP/DOWN markets.

**"Could not fetch BTC momentum"**
- Binance API rate-limited. Increase `lookback_minutes` or wait.

**"Momentum X% < minimum Y%"**
- Signal not strong enough. Entry threshold working correctly. Adjust `min_momentum_pct` if too conservative.

**Volume spike trigger not firing**
- Default baseline needs 6 prior 10-minute windows of data. In low-volume markets, set `volume_baseline_windows` lower or increase `volume_spike_exit_multiplier`.

**"External wallet requires a pre-signed order"**
- `WALLET_PRIVATE_KEY` is not set. Export the private key for your Polymarket wallet.
- The SDK handles all signing automatically once the env var is set.

**"Balance shows $0 but I have funds on Polygon"**
- Polymarket V2 uses pUSD (PolyUSD). Migrate at simmer.markets/dashboard (one click, ~30s).
