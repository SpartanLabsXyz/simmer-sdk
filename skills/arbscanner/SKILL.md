---
name: simmer-arbscanner
description: Detect and execute Polymarket arbitrage opportunities via Simmer. Single-condition (YES+NO≠$1) and NegRisk (multi-outcome sum≠100%) strategies.
metadata: {"openclaw":{"emoji":"📊","requires":{"env":["SIMMER_API_KEY"]},"cron":"*/30 * * * *"}}
authors:
  - Simmer (@simmer_markets)
attribution: "Detection logic based on runesatsdev/polymarket-arbitrage-bot and IMDEA Networks research"
version: "1.0.0"
---

# Simmer Arbitrage Scanner

Detect and execute Polymarket arbitrage opportunities using Simmer's managed execution.

## When to Use This Skill

Use when you want to:
- Scan for arbitrage opportunities on Polymarket
- Detect mispriced binary markets (YES + NO ≠ $1.00)
- Find NegRisk opportunities (multi-outcome markets with sum ≠ 100%)
- Execute arb trades through Simmer's safety rails

## Strategies

### 1. Single-Condition Arbitrage
When YES + NO prices don't sum to $1.00, guaranteed profit exists.

**Example:**
```
Market: "Will it rain tomorrow?"
YES price: $0.53
NO price: $0.42
Sum: $0.95

Action: Buy both for $0.95 → Payout $1.00 → Profit $0.05 (5.3% ROI)
```

Historical: $10.58M extracted across 7,051 conditions (IMDEA research)

### 2. NegRisk Rebalancing
Multi-outcome markets (3+ options) where probabilities don't sum to 100%.

**Example:**
```
Market: "Who wins the election?"
Candidate A: 45%
Candidate B: 46%
Candidate C: 6%
Sum: 97%

Action: Buy all for $0.97 → Payout $1.00 → Profit $0.03 (3% ROI)
```

Historical: $28.99M extracted, 29× capital efficiency vs single-condition

## Setup

1. **Set your Simmer API key:**
```bash
export SIMMER_API_KEY="sk_live_..."
```

2. **Run the scanner:**
```bash
python arb_scanner.py              # Scan only
python arb_scanner.py --execute    # Scan and trade
python arb_scanner.py --dry-run    # Show what would trade
```

## Configuration

Edit thresholds in `arb_scanner.py`:

```python
MIN_PROFIT_THRESHOLD = 0.02  # Minimum 2¢ profit
MIN_ROI_THRESHOLD = 0.01     # Minimum 1% ROI
DEFAULT_TRADE_SIZE = 5.0     # $5 per leg
MAX_TRADE_SIZE = 25.0        # $25 max per opportunity
```

## Venue Selection

```bash
# Practice with virtual $SIM (default)
python arb_scanner.py --execute --venue simmer

# Real USDC on Polymarket
python arb_scanner.py --execute --venue polymarket
```

## Example Output

```
🔍 Simmer Arbitrage Scanner
============================================================
  Execution: SCAN ONLY
  Venue: simmer
  Min profit: $0.02
  Min ROI: 1%

📡 Fetching Polymarket markets...
  Found 100 active markets

🔎 Scanning for arbitrage...
  ✅ Single-condition: Will Trump win 2028 election?...
     ROI: 3.2% | Profit: $0.48 | Action: buy_both
  ✅ NegRisk (5 outcomes): Democratic VP Nominee?...
     ROI: 4.7% | Profit: $1.27 | Sum: 0.953

📊 Found 2 opportunities

🏆 Top Opportunities:
------------------------------------------------------------
1. [negrisk] Democratic VP Nominee?...
   ROI: 4.7% | Profit: $1.27 | Capital: $27.00
   Action: buy_all

2. [single_condition] Will Trump win 2028 election?...
   ROI: 3.2% | Profit: $0.48 | Capital: $15.00
   Action: buy_both
```

## Cron Setup

Run every 30 minutes to catch opportunities:

```bash
# Add to crontab
*/30 * * * * cd /path/to/skill && python arb_scanner.py --execute --venue simmer
```

Or use your agent's heartbeat system.

## Limitations

- **NegRisk execution:** Currently detection-only. Multi-leg execution requires atomic transactions.
- **Slippage:** Scanner uses best ask prices; actual execution may differ.
- **Speed:** Arb opportunities close quickly. This scanner is for learning/small trades, not HFT.

## Attribution

Detection logic based on:
- [runesatsdev/polymarket-arbitrage-bot](https://github.com/runesatsdev/polymarket-arbitrage-bot)
- IMDEA Networks research: "Unravelling the Probabilistic Forest: Arbitrage in Prediction Markets" (arXiv:2508.03474)

## Links

- **Simmer Dashboard:** https://simmer.markets/dashboard
- **SDK Docs:** https://github.com/SpartanLabsXyz/simmer-sdk
- **Support:** https://t.me/+m7sN0OLM_780M2Fl
