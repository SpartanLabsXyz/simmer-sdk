# Arbitrage Scanner Skill - Build Context

## Overview

Scan Polymarket for arbitrage opportunities between correlated markets. When mutually exclusive outcomes are mispriced (sum < $1.00), detect potential arb edges. Note: actual profitability depends on CLOB spreads, fees, and execution latency — see SKILL.md for viability notes.

## Why This Skill

From user research:
> "Multi-Market Arbitrage Scanner scans 10+ correlated markets every 15 seconds. When combined YES/NO < $1.00 → Instant arb. Example: Market A YES@0.48 + Market B NO@0.49 = Guaranteed $1.00 payout for $0.97 cost"

This is a **pure math** skill — no signals, no sentiment, just pricing inefficiencies.

## Skill Pattern

**Trigger:** Periodic scan (cron) or manual
**Logic:** Find market pairs where prices don't sum to 1.00
**Action:** Buy both sides for guaranteed profit

## Arbitrage Types

### Type 1: Same-Market YES/NO
```
Market: "Will X happen?"
YES price: $0.48
NO price: $0.49
Total: $0.97

Buy 1 YES + 1 NO = $0.97
Guaranteed payout = $1.00
Profit = $0.03 (3.1%)
```

### Type 2: Mutually Exclusive Markets
```
Market A: "Trump wins 2028"  → YES @ $0.35
Market B: "Harris wins 2028" → YES @ $0.32
Market C: "DeSantis wins 2028" → YES @ $0.15
Market D: "Other wins 2028" → YES @ $0.10
Total: $0.92

If only one can win, buy all = $0.92
Guaranteed payout = $1.00
Profit = $0.08 (8.7%)
```

### Type 3: Complementary Events
```
Market A: "BTC > $100k by Dec" → YES @ $0.45
Market B: "BTC < $100k by Dec" → YES @ $0.52
Total: $0.97

These SHOULD sum to $1.00. If not, arb exists.
```

## Core Features

### 1. Market Pair Discovery
- Query Simmer/Polymarket for related markets
- Group by event (election, price target, etc.)
- Identify mutually exclusive sets

### 2. Price Monitoring
```python
def check_arb(markets: list) -> dict:
    total_cost = sum(m['yes_price'] for m in markets)
    if total_cost < 0.98:  # 2% minimum edge
        return {
            'opportunity': True,
            'cost': total_cost,
            'profit_pct': (1.0 - total_cost) / total_cost,
            'markets': markets
        }
    return {'opportunity': False}
```

### 3. Execution
- Buy all sides simultaneously (or as fast as possible)
- Account for slippage and fees
- Minimum profit threshold (e.g., 2% after fees)

### 4. Alerts
- Telegram/Discord notification when arb found
- Include profit %, markets involved, execution plan

## File Structure

```
skills/arbscanner/
├── README.md (SKILL.md content)
├── SKILL.md
├── arbscanner.py            # Main script
├── scripts/
│   └── status.py            # Quick status check
└── data/
    └── arb_history.json     # Past opportunities log
```

## CLI Interface

```bash
# Scan for opportunities now
python arbscanner.py --scan

# Continuous monitoring mode
python arbscanner.py --monitor --interval 30

# Dry run (don't execute, just alert)
python arbscanner.py --scan --dry-run

# Set minimum profit threshold
python arbscanner.py --scan --min-profit 0.02

# View past opportunities
python arbscanner.py --history

# Scan specific event group
python arbscanner.py --scan --event "2028-election"
```

## Configuration

| Setting | Env Var | Default |
|---------|---------|---------|
| Min profit % | `SIMMER_ARB_MIN_PROFIT` | 0.02 (2%) |
| Max position | `SIMMER_ARB_MAX_USD` | 100 |
| Scan interval | `SIMMER_ARB_INTERVAL` | 60 (seconds) |
| Alert only | `SIMMER_ARB_ALERT_ONLY` | false |

## Challenges

### 1. Finding Related Markets
- Polymarket doesn't explicitly link mutually exclusive markets
- Need NLP or manual mapping to identify pairs
- Start with known patterns (elections, price brackets)

### 2. Execution Speed
- Arb opportunities disappear fast
- Need to execute multiple trades quickly
- May need batch order endpoint

### 3. Slippage
- Large orders move prices
- Calculate expected slippage before executing
- Abort if slippage eats profit

### 4. Fees
- Polymarket has trading fees
- Must factor into profit calculation
- Only execute if profit > fees + slippage

## Market Grouping Strategies

### Automatic Detection
```python
# Group by common keywords
groups = {
    "btc-100k": [markets with "BTC" and "100k"],
    "election-2028": [markets with "2028" and "president"],
}
```

### Manual Configuration
```json
{
  "groups": [
    {
      "name": "2028-president",
      "type": "mutually_exclusive",
      "markets": ["market-id-1", "market-id-2", "market-id-3"]
    }
  ]
}
```

## Simmer API Needs

- `GET /api/sdk/markets?event_id=X` - markets in same event
- `GET /api/sdk/markets?q=keyword` - search markets
- Batch trade endpoint (or fast sequential)

## Cron Suggestion

```yaml
metadata: {"clawdbot":{"emoji":"🔄","requires":{"env":["SIMMER_API_KEY"]},"cron":"*/5 * * * *"}}
```
Every 5 minutes — arb opportunities are time-sensitive.

## Success Metrics

1. Identify real arb opportunities
2. Execute before opportunity closes
3. Positive P&L after fees
4. Track hit rate (opportunities found vs executed vs profitable)

## Implementation Order

1. Manual market grouping (hardcode known pairs)
2. Price sum checker
3. Alert system (dry run)
4. Execution logic
5. Automatic market discovery

## Edge Cases

- Market resolves while holding arb position
- One leg fills, other doesn't (partial arb)
- Prices move between scan and execution
- Markets have different resolution dates

---

## Ready to Build

Start with `arbscanner.py` - focus on:
1. Hardcoded market groups first (elections, price brackets)
2. Simple price sum check
3. Dry-run alerts
4. Add execution later

Reference: `/tmp/simmer-sdk/skills/weather/weather_trader.py` for scanning pattern
