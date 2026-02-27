---
name: polymarket-wallet-xray
displayName: Polymarket Wallet X-Ray
description: X-ray any Polymarket wallet — skill level, entry quality, bot detection, and edge analysis. Queries Polymarket's public APIs, no authentication needed. Inspired by @thejayden's "Autopsy of a Polymarket Whale" analysis.
metadata: {"clawdbot":{"emoji":"🔍","requires":{"env":[],"pip":[]},"cron":null,"autostart":false}}
authors:
  - Simmer (@simmer_markets)
inspired_by:
  - thejayden (@thejayden) - "Autopsy: How to Read the Mind of a Polymarket Whale"
version: "1.0.2"
difficulty: beginner
published: true
---

# Polymarket Wallet X-Ray

Analyze **any** Polymarket wallet's trading patterns, skill level, and edge detection.

**No authentication needed.** Queries Polymarket's public CLOB API directly.

**Inspired by:** [The Autopsy: How to Read the Mind of a Polymarket Whale](https://x.com/thejayden/status/2020891572389224878) by [@thejayden](https://x.com/thejayden)

> This skill implements the forensic trading analysis framework developed by @thejayden. Read the original post to understand the philosophy behind Time Profitable, hedge checks, bot detection, and accumulation signals.

> **This is an analysis tool, not a trading signal.** The skill returns forensic metrics for ANY Polymarket wallet — your agent uses them to UNDERSTAND traders, learn patterns, and make informed decisions. This is for education and research, not for blindly copying positions.

## ⚠️ Important Disclaimer

**Past performance does not guarantee future results.** A wallet's historical metrics tell you about:
- ✅ How they traded *in the past*
- ✅ Their *historical* win rate and entry quality
- ❌ NOT whether their strategy will work going forward

**Why copying is risky:**
- Market conditions change constantly
- A trader's edge might have been luck, timing, or specific to historical events
- Slippage and fees erode thin edges to zero
- Other traders copying the same strategy destroy the edge

**Use this skill to:**
- ✅ Learn what skilled traders look like (metrics, behavior)
- ✅ Identify potential anomalies (bots, arbitrageurs)
- ✅ Understand trader psychology (FOMO vs. discipline)
- ✅ Inform your own strategy decisions

**DO NOT use this skill to:**
- ❌ Automatically copytrade wallets
- ❌ Expect to replicate their returns
- ❌ Trade on these metrics without understanding why
- ❌ Risk significant capital on patterns you don't understand

## When to Use This Skill

Use this skill when you want to:
- **Learn how skilled traders operate** — What metrics separate winners from losers?
- **Understand trading psychology** — Who chases prices? Who has discipline?
- **Detect bots and anomalies** — Identify suspicious patterns for research
- **Research arbitrage activity** — Find wallets with hedged positions (educational)
- **Compare trader profiles** — What does a consistent trader look like vs. a lucky one?
- **Inform your own strategy** — Use patterns as input to YOUR decision-making, not as direct signals

**NOT for:**
- Copying trades blindly or automatically
- Assuming past returns = future returns
- Making large bets on these metrics alone

## Quick Commands

```bash
# Analyze a single wallet
python wallet_xray.py 0x1234...abcd

# Analyze wallet + only look at specific market
python wallet_xray.py 0x1234...abcd "Bitcoin"

# Compare two wallets head-to-head
python wallet_xray.py 0x1111... 0x2222... --compare

# Find wallets matching criteria (top Time Profitable in market)
python wallet_xray.py "Will BTC hit $100k?" --top-wallets 5 --dry-run

# Check your account status
python scripts/status.py
```

**APIs Used (Public, No Auth Required):**
- Gamma API: `https://gamma-api.polymarket.com` — Market search
- CLOB API: `https://clob.polymarket.com` — Trade history and orderbook

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
  "recommendation": "Good trader. Skilled entries, disciplined sizing. Good metrics for learning from. Not advice to copytrade."
}
```

## How It Works

1. **Fetch trade history** — Download all trades this wallet made from Polymarket via Simmer API
2. **Compute profitability timeline** — When were they underwater vs. profitable?
3. **Analyze entry quality** — Did they buy at optimal prices or chase?
4. **Detect trading patterns** — Bot (inhuman speed) vs. human (deliberate timing)?
5. **Check for arbitrage** — Combined YES+NO avg < $1.00? (Potential structural edge — depends on execution and fees)
6. **Assess behavior** — FOMO accumulation? Disciplined sizing? Rotating positions?
7. **Generate recommendation** — Is this wallet worth following? What's the risk?

## Understanding the Metrics

### ⏱️ **Time Profitable** (e.g., 75.3%)
Wallet was profitable (not underwater) for 75% of their trading period. This wallet endured only 25% painful drawdowns — that's discipline.

- **>80%** = Sniper-like (skilled entries, holds through drawdowns)
- **50-80%** = Solid (good discipline)
- **<50%** = Risky (likely panic-held losses)

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

If combined < $1.00, they may have entered with a structural edge (lower combined cost than $1 payout). Actual profit depends on execution, fees, and spread.

- **< $0.95** = Strong potential edge. Likely institutional/pro.
- **$0.95-1.00** = Slight edge detected.
- **> $1.00** = No edge; betting on direction.

## Usage Examples

### **Example 1: Learning from a skilled trader (Analysis)**

```python
import subprocess
import json

# Analyze a wallet known for skilled trading
result = subprocess.run(
    ["python", "wallet_xray.py", "0x123...abc", "--json"],
    capture_output=True,
    text=True
)
data = json.loads(result.stdout)

# LEARN from their profile, don't copy blindly
time_prof = data["profitability"]["time_profitable_pct"]
entry_qual = data["entry_quality"]["quality_rating"]

print(f"📊 What this trader does well:")
print(f"  • Time Profitable: {time_prof}% (disciplined)")
print(f"  • Entry Quality: {entry_qual} (patient buyer)")
print(f"  • Behavior: {data['behavior']['accumulation_signal']} (not FOMO)")

# THEN: Ask yourself
# - Why are they profitable? (skill or luck?)
# - Can I replicate their decision-making process?
# - Do I have their capital size, timing, or information?
```

### **Example 2: Research anomalies (Education)**

```python
# Analyze multiple wallets to understand patterns
wallets = ["0x111...", "0x222...", "0x333..."]

print("Comparing trader profiles:")
for wallet in wallets:
    result = subprocess.run(
        ["python", "wallet_xray.py", wallet, "--json"],
        capture_output=True,
        text=True
    )
    data = json.loads(result.stdout)

    is_bot = "🤖 BOT" if data["behavior"]["is_bot_detected"] else "👤 HUMAN"
    print(f"\n{wallet}: {is_bot}")
    print(f"  Win Rate: {data['profitability']['win_rate_pct']}%")
    print(f"  Time Profitable: {data['profitability']['time_profitable_pct']}%")

# Use this data to understand what successful trading LOOKS LIKE
# Then build your own strategy based on these insights
```

### **Example 3: Informed decision-making (NOT blind copying)**

```python
# Analyze before you decide what to do
result = subprocess.run(
    ["python", "wallet_xray.py", "0x123...abc", "--json"],
    capture_output=True,
    text=True
)
data = json.loads(result.stdout)

# Make an INFORMED decision based on analysis + YOUR OWN JUDGMENT
if data["profitability"]["time_profitable_pct"] > 75 and \
   data["entry_quality"]["quality_rating"] in ["A", "A+"]:

    print(f"✅ This wallet shows skill (high Time Profitable, good entries)")
    print(f"⚠️  But I will NOT copytrade blindly.")
    print(f"📋 Instead, I'll:")
    print(f"   1. Backtest their patterns on fresh data")
    print(f"   2. Add my own market signals")
    print(f"   3. Start with small position (1-2% of capital)")
    print(f"   4. Monitor for next 30 days")
    print(f"   5. Adjust if it stops working")
else:
    print(f"❌ This wallet doesn't show strong enough metrics.")
    print(f"   Safer to avoid or research further before deciding.")
```

## Running the Skill

**Analyze a single wallet (default):**
```bash
python wallet_xray.py 0x1234...abcd
```

**Analyze wallet for a specific market:**
```bash
python wallet_xray.py 0x1234...abcd "Bitcoin"
```

**Output as JSON (for scripts):**
```bash
python wallet_xray.py 0x1234...abcd --json
```

**Compare two wallets:**
```bash
python wallet_xray.py 0x1111... 0x2222... --compare
```

**Limit analysis to recent trades (faster):**
```bash
python wallet_xray.py 0x1234...abcd --limit 100
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

**"Connection error"**
- Check that Polymarket's CLOB API is reachable: `curl https://clob.polymarket.com/trades`
- If down, try again later or use `--limit 50` to reduce load

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
