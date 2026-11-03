---
name: polymarket-wallet-xray
description: X-ray any Polymarket wallet for skill level, entry quality, bot detection, arbitrage research, trader-profile comparison, and edge analysis. Queries Polymarket's public APIs with no authentication; use for research, not blind copytrading, and never assume past returns predict future results.
metadata:
  author: Simmer (@simmer_markets)
  version: "1.1.10"
  displayName: Polymarket Wallet X-Ray
  envVars:
    - name: SIMMER_API_KEY
      required: false
      description: Optional, only for scripts/status.py to read your own Simmer account status. Public wallet analysis needs no key.
  difficulty: beginner
  attribution: Inspired by @thejayden's "Autopsy of a Polymarket Whale" analysis.
---
# Polymarket Wallet X-Ray

Analyze **any** Polymarket wallet's trading patterns, skill level, and edge detection.

**No authentication needed.** Queries Polymarket's public Data API v2 directly.

**Inspired by:** [The Autopsy: How to Read the Mind of a Polymarket Whale](https://x.com/thejayden/status/2020891572389224878) by [@thejayden](https://x.com/thejayden)

> **Read-only research.** Public analysis reads wallet activity; the optional status helper reads your own Simmer account. Read [DISCLAIMER.md](./DISCLAIMER.md) before interpreting results.

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

## Setup Flow

Public wallet analysis requires only Python's standard library. No package install, SDK, API key, or wallet connection is needed. Run `wallet_xray.py` with a public Polymarket wallet address.

Only if the user explicitly asks for their own Simmer account status, use `scripts/status.py`. `SIMMER_API_KEY` is optional for this skill and used only by that helper. Have the user configure it in their environment outside chat, using the SDK tab at https://simmer.markets/dashboard. Never ask them to paste a key into chat or print it.

The status helper sends the key as a Bearer header only to `https://api.simmer.markets` for read-only portfolio and position queries. It exits before making a request when the key is unset. Neither script places orders or changes account settings.

## Quick Commands

All shortened `0x...` addresses below are placeholders. Replace them with complete public wallet addresses before running a command.

```bash
# Analyze a single wallet
python wallet_xray.py 0x1234...abcd

# Compare two wallets head-to-head
python wallet_xray.py 0x1111... 0x2222... --compare

# Optional: read your own Simmer account status (requires SIMMER_API_KEY)
python scripts/status.py
```

**APIs Used:**
- Data API v2: `https://data-api.polymarket.com/v2/activity` — Wallet activity (cursor-paginated)
- Simmer API: `https://api.simmer.markets` — Optional authenticated account status via `scripts/status.py`

## What You Get Back

Illustrative report excerpt (values and wallet address are placeholders):

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
  "recommendation": "Good trader. Positive historical metrics; research only, not a signal to copy or size positions."
}
```

## How It Works

1. **Fetch trade history** — Download wallet activity from Data API v2, following cursors up to the requested limit
2. **Compute profitability timeline** — When were they underwater vs. profitable?
3. **Analyze entry quality** — Did they buy at optimal prices or chase?
4. **Detect trading patterns** — Bot (inhuman speed) vs. human (deliberate timing)?
5. **Check for arbitrage** — Combined YES+NO avg < $1.00? (Potential structural edge — depends on execution and fees)
6. **Assess behavior** — FOMO accumulation? Disciplined sizing? Rotating positions?
7. **Summarize historical metrics** — Which patterns merit further research? What are the data limitations?

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

### Research one wallet

```bash
python wallet_xray.py 0x1234...abcd --json
```

Review `profitability`, `entry_quality`, `behavior`, and `risk_profile` to describe historical patterns. The `recommendation` field summarizes a heuristic rating tier, not a trading or allocation recommendation. Test hypotheses against fresh data and consider fees, sample size, and missing activity.

### Compare historical profiles

```bash
python wallet_xray.py 0x1111... 0x2222... --compare --json --limit 100
```

Compare the two reports for differences in historical entries, drawdowns, and activity. Strong historical metrics do not establish future returns or justify copying or sizing positions.

## Running the Skill

**Analyze a single wallet (default):**
```bash
python wallet_xray.py 0x1234...abcd
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

**"Analysis took too long"**
- For wallets with >500 trades, analysis can take 30+ seconds
- Use `--limit 100` to analyze only recent trades for faster results

**"API rate limited"**
- You're analyzing many wallets in quick succession
- Wait a minute before trying again, or use `--limit` to speed up individual analyses

**"Connection error"**
- Check that Polymarket's Data API v2 is reachable: `curl 'https://data-api.polymarket.com/v2/activity?user=0xYOUR_WALLET&limit=1'`
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

- **Full Simmer API Reference:** [docs.simmer.markets/api/overview](https://docs.simmer.markets/api/overview)
- **Original Analysis:** [The Autopsy: How to Read the Mind of a Polymarket Whale](https://x.com/thejayden/status/2020891572389224878)
- **Dashboard:** [simmer.markets/dashboard](https://simmer.markets/dashboard?ref=sdk-skill&utm_campaign=sdk-skill)
- **Support:** [Telegram](https://t.me/+m7sN0OLM_780M2Fl)
