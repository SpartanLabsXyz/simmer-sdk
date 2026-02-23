---
name: polymarket-wallet-autopsy
displayName: Polymarket Wallet Autopsy
description: Forensic analysis of any Polymarket wallet. Spot skill, entry quality, bot detection, and arbitrage opportunities. Inspired by @thejayden's "Autopsy of a Polymarket Whale" analysis.
metadata: {"clawdbot":{"emoji":"🔍","requires":{"env":["SIMMER_API_KEY"],"pip":["simmer-sdk","requests"]},"cron":null,"autostart":false,"automaton":{"managed":true,"entrypoint":"wallet_autopsy.py"}}}
authors:
  - Simmer (@simmer_markets)
inspired_by:
  - thejayden (@thejayden) — "Autopsy: How to Read the Mind of a Polymarket Whale"
version: "1.0.0"
published: true
---

# Polymarket Wallet Autopsy

Analyze any Polymarket wallet's trading patterns, skill level, and edge detection.

**Inspired by:** [The Autopsy: How to Read the Mind of a Polymarket Whale](https://x.com/thejayden/status/2020891572389224878) by [@thejayden](https://x.com/thejayden)

> This skill implements the forensic trading analysis framework developed by @thejayden. Read the original post to understand the philosophy behind Time Profitable, hedge checks, bot detection, and accumulation signals.

> **This is a template.** The skill returns forensic metrics — your agent decides what to do with them: copytrade skilled wallets, fade bots, find arbitrage opportunities, or track competitors. The skill handles data fetching and metric computation; your agent provides the strategy.

## When to Use This Skill

Use this skill when you need to:
- **Identify skilled traders** before copying their positions
- **Detect bots** that might frontrun or manipulate
- **Find arbitrage opportunities** (wallets with hedged positions)
- **Analyze a specific wallet's edge** (entry quality, consistency, timing)
- **Compare multiple wallets** to pick the best to follow
- **Understand trader behavior** (FOMO chasing vs. disciplined accumulation)

## Quick Commands

```bash
# Analyze a single wallet
python wallet_autopsy.py 0x1234...abcd

# Analyze wallet + only look at specific market
python wallet_autopsy.py 0x1234...abcd "Bitcoin"

# Compare two wallets head-to-head
python wallet_autopsy.py 0x1111... 0x2222... --compare

# Find wallets matching criteria (top Time Profitable in market)
python wallet_autopsy.py "Will BTC hit $100k?" --top-wallets 5 --dry-run

# Check your account status
python scripts/status.py
```

**API Reference:**
- Base URL: `https://api.simmer.markets`
- Auth: `Authorization: Bearer $SIMMER_API_KEY`
- Portfolio: `GET /api/sdk/portfolio`
- Positions: `GET /api/sdk/positions`

## What You Get Back

The skill returns comprehensive forensic metrics:

```json
{
  "wallet": "0x1234...abcd",
  "total_trades": 156,
  "total_period_hours": 42.5,
  "profitability": {
    "time_profitable_pct": 75.3,
    "win_rate_pct": 68.2,
    "avg_profit_per_win": 0.035,
    "avg_loss_per_loss": -0.018,
    "realized_pnl_usd": 2450.00
  },
  "entry_quality": {
    "avg_slippage_bps": 28,
    "quality_rating": "B+",
    "assessment": "Good entries, occasional FOMO"
  },
  "behavior": {
    "is_bot_detected": false,
    "trading_intensity": "high",
    "avg_seconds_between_trades": 45,
    "price_chasing": "moderate",
    "accumulation_signal": "growing"
  },
  "edge_detection": {
    "hedge_check_combined_avg": 0.98,
    "has_arbitrage_edge": false,
    "assessment": "No locked-in edge; relies on direction"
  },
  "risk_profile": {
    "max_drawdown_pct": 12.5,
    "volatility": "medium",
    "max_position_concentration": 0.22
  },
  "recommendation": "Good trader. Skilled entries, disciplined sizing. Safe to copytrade with 10-25% of capital."
}
```

## How It Works

1. **Fetch trade history** — Download all trades this wallet made from Polymarket via Simmer API
2. **Compute profitability timeline** — When were they underwater vs. profitable?
3. **Analyze entry quality** — Did they buy at optimal prices or chase?
4. **Detect trading patterns** — Bot (inhuman speed) vs. human (deliberate timing)?
5. **Check for arbitrage** — Combined YES+NO avg < $1.00? (Risk-free profit locked in)
6. **Assess behavior** — FOMO accumulation? Disciplined sizing? Rotating positions?
7. **Generate recommendation** — Is this wallet worth following? What's the risk?

## Understanding the Metrics

### ⏱️ **Time Profitable** (e.g., 75.3%)
Wallet was profitable (not underwater) for 75% of their trading period. This wallet endured only 25% painful drawdowns — that's discipline.

- **>80%** = Sniper. Copy them.
- **50-80%** = Solid. Good risk/reward.
- **<50%** = Risky. They panic-held losses.

### 🎯 **Entry Quality** (e.g., 28 bps average slippage)
They buy near the best available price. 28 basis points is normal for active traders. No evidence of FOMO market orders.

- **<20 bps** = Expert. Limit orders, patience.
- **20-40 bps** = Good. Balanced speed/price.
- **>50 bps** = Weak. Chasing prices.

### 🤖 **Bot Detection** (e.g., false)
Average 45 seconds between trades. This is human. A bot would be <1 second.

- **<5 sec** = Likely bot. Avoid unless you know it's a legitimate market maker.
- **5-30 sec** = Possible bot.
- **>30 sec** = Human.

### 💰 **Hedge Check** (e.g., combined avg 0.98)
If they bought YES at $0.70 and NO at $0.30, combined = $1.00. This wallet spent exactly what they should to be neutral.

If combined < $1.00, they locked in risk-free profit (arbitrage).

- **< $0.95** = They found free money. Likely institutional/pro.
- **$0.95-1.00** = Slight edge detected.
- **> $1.00** = No edge; betting on direction.

## Usage Examples

### **Example 1: Agent vetting a copytrading target**

```python
from simmer_sdk import SimmerClient
import subprocess
import json

# Run wallet autopsy
result = subprocess.run(
    ["python", "wallet_autopsy.py", "0x123...abc", "--json"],
    capture_output=True,
    text=True
)
data = json.loads(result.stdout)

if data["profitability"]["time_profitable_pct"] > 75:
    if not data["behavior"]["is_bot_detected"]:
        print("✅ Safe to copytrade. Good trader.")
    else:
        print("⚠️ Skip. Bot detected.")
else:
    print("❌ Too risky. Low Time Profitable.")
```

### **Example 2: Finding arbitrage wallets**

```python
wallets = ["0x111...", "0x222...", "0x333..."]

for wallet in wallets:
    result = subprocess.run(
        ["python", "wallet_autopsy.py", wallet, "--json"],
        capture_output=True,
        text=True
    )
    data = json.loads(result.stdout)
    if data["edge_detection"]["has_arbitrage_edge"]:
        print(f"Found arbitrage wallet: {wallet}")
        print(f"  Combined avg: {data['edge_detection']['hedge_check_combined_avg']}")
```

### **Example 3: Learning from competitors**

```python
competitors = ["0xaaa...", "0xbbb..."]

for wallet in competitors:
    result = subprocess.run(
        ["python", "wallet_autopsy.py", wallet, "Bitcoin", "--json"],
        capture_output=True,
        text=True
    )
    data = json.loads(result.stdout)
    print(f"{wallet}:")
    print(f"  Entry quality: {data['entry_quality']['quality_rating']}")
    print(f"  Behavior: {data['behavior']['price_chasing']}")
```

## Running the Skill

**Analyze a single wallet (default):**
```bash
python wallet_autopsy.py 0x1234...abcd
```

**Analyze wallet for a specific market:**
```bash
python wallet_autopsy.py 0x1234...abcd "Bitcoin"
```

**Output as JSON (for scripts):**
```bash
python wallet_autopsy.py 0x1234...abcd --json
```

**Compare two wallets:**
```bash
python wallet_autopsy.py 0x1111... 0x2222... --compare
```

**Limit analysis to recent trades (faster):**
```bash
python wallet_autopsy.py 0x1234...abcd --limit 100
```

## Troubleshooting

**"Wallet has no trades"**
- This wallet hasn't traded yet, or all trades are too old
- Try a wallet you know is active

**"Market not found"**
- The market query didn't match anything on Polymarket
- Try a more specific market name or leave it blank to analyze all markets

**"Analysis took too long"**
- For wallets with >500 trades, analysis can take 30+ seconds
- Use `--limit 100` to analyze only recent trades for faster results

**"API rate limited"**
- You're analyzing many wallets in quick succession
- Wait a minute before trying again, or use `--limit` to speed up individual analyses

**"SIMMER_API_KEY not set"**
- Get your API key from: https://simmer.markets/dashboard
- Then: `export SIMMER_API_KEY="sk_live_..."`

## Credits

This skill is based on the forensic trading analysis framework from [@thejayden's "Autopsy of a Polymarket Whale"](https://x.com/thejayden/status/2020891572389224878).

The original post shows how to:
- Spot fake gurus (high PnL, terrible entries)
- Detect bots (inhuman trading speed)
- Find arbitrage opportunities (hedged positions)
- Understand trader psychology (FOMO vs. discipline)

All metrics and analysis patterns used here are derived from that work. If you find this useful, give the original post a read and follow [@thejayden](https://x.com/thejayden).

## Links

- **Full Simmer API Reference:** [simmer.markets/docs.md](https://simmer.markets/docs.md)
- **Original Analysis:** [The Autopsy: How to Read the Mind of a Polymarket Whale](https://x.com/thejayden/status/2020891572389224878)
- **Dashboard:** [simmer.markets/dashboard](https://simmer.markets/dashboard)
- **Support:** [Telegram](https://t.me/+m7sN0OLM_780M2Fl)
