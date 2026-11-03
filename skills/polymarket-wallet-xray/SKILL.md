---
name: polymarket-wallet-xray
description: X-ray any Polymarket wallet for skill level, entry quality, bot detection, arbitrage research, trader-profile comparison, and edge analysis. Queries Polymarket's public APIs with no authentication; use for research, not blind copytrading, and never assume past returns predict future results.
metadata:
  author: Simmer (@simmer_markets)
  version: "1.1.11"
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

Illustrative report excerpt (`recommendation` omitted; values and wallet address are placeholders):

```json
{
  "wallet": "0x1234...abcd",
  "total_trades": 156,
  "total_period_hours": 42.5,
  "analysis_timestamp": "2026-11-03T00:00:00",
  "profitability": {
    "time_profitable_pct": 75.3,
    "win_rate_pct": 68.2,
    "avg_profit_per_win": 0.035,
    "avg_loss_per_loss": -0.018,
    "realized_pnl_usd": 2450.00,
    "total_trades": 156
  },
  "entry_quality": {
    "avg_price_spread_bps": 128,
    "quality_rating": "B+",
    "assessment": "Good consistency. Balanced speed/price."
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
    "has_arbitrage_edge": true,
    "assessment": "Found arbitrage edge! Combined avg $0.98 < $1.00"
  },
  "risk_profile": {
    "max_drawdown_pct": 12.5,
    "volatility": "medium",
    "max_position_concentration": 0.22
  }
}
```

## How It Works

1. **Fetch wallet activity** — Download activity from Data API v2, following cursors up to the requested limit
2. **Compute `profitability`** — Track realized P&L from sells/redeems with matching buys
3. **Compute `entry_quality`** — Measure buy-price consistency within each market and outcome
4. **Compute `behavior`** — Compare activity intervals, buy prices, and share counts
5. **Compute `edge_detection`** — Average combined outcome buy prices for markets with exactly two outcomes bought
6. **Compute `risk_profile`** — Estimate drawdown, activity-cost variation, and activity-share concentration
7. **Generate `recommendation`** — Summarize a heuristic historical rating tier for research

## Understanding the Metrics

Fields in a single-wallet JSON report (all metrics use only the fetched activity window):

| Field | Meaning |
| --- | --- |
| `wallet` | Wallet address supplied to the analysis. |
| `total_trades` | Number of fetched activity records, including non-trade activity. |
| `total_period_hours` | Hours between the earliest and latest fetched activity timestamps. |
| `analysis_timestamp` | UTC analysis timestamp in ISO format, without a timezone suffix. |
| `profitability` | Realized P&L metrics from sells/redeems with matching buys in the window. |
| `profitability.time_profitable_pct` | Percentage of realized outcomes after which cumulative realized P&L is positive, without elapsed-time weighting. |
| `profitability.win_rate_pct` | Percentage of realized outcomes with P&L greater than $0.001, including breakeven outcomes in the denominator. |
| `profitability.avg_profit_per_win` | Mean realized P&L in USD among outcomes above $0.001, or 0 if none. |
| `profitability.avg_loss_per_loss` | Mean realized P&L in USD among outcomes below -$0.001, or 0 if none. |
| `profitability.realized_pnl_usd` | Sum of realized P&L in USD from matched sells/redeems. |
| `profitability.total_trades` | Number of fetched activity records, identical to top-level `total_trades`. |
| `entry_quality` | Buy-price consistency metrics grouped by market and outcome. |
| `entry_quality.avg_price_spread_bps` | Mean of `(max buy price - min buy price) / mean buy price * 10000` across groups with at least two buys and mean price above 0.01, or 0 if none. |
| `entry_quality.quality_rating` | Spread tier: A below 50 bps, B+ below 150, B below 400, otherwise C; B for buys without eligible spreads, N/A for no buys. |
| `entry_quality.assessment` | Heuristic text describing the spread tier or lack of eligible buys. |
| `behavior` | Heuristics based on activity timing, buy prices, and share counts. |
| `behavior.is_bot_detected` | True when the mean positive interval between activity records is below 5 seconds; false when no positive intervals exist. |
| `behavior.trading_intensity` | Interval tier: very_high below 10 seconds, high below 60, medium below 300, otherwise low; very_low for fewer than two records, unknown for no positive intervals. |
| `behavior.avg_seconds_between_trades` | Mean positive interval in seconds between consecutive activity timestamps, or 0 if none. |
| `behavior.price_chasing` | Compares mean buy price in the later half with the earlier half across all markets: high above 105%, moderate above 100%, otherwise low; unknown with fewer than two buys. |
| `behavior.accumulation_signal` | Compares mean shares per activity record in the later half with the earlier half: growing above 130%, stable above 100%, otherwise decreasing; unknown with fewer than two records. |
| `edge_detection` | Heuristics for markets with buys in exactly two outcomes. |
| `edge_detection.hedge_check_combined_avg` | Mean sum of the two share-weighted outcome buy prices across eligible markets, or 1.0 if none. |
| `edge_detection.has_arbitrage_edge` | True when the unrounded combined average is below 0.99; does not account for fees or matched holdings. |
| `edge_detection.assessment` | Heuristic text describing the combined average or absence of eligible markets. |
| `risk_profile` | Estimates from activity prices, costs, and shares. |
| `risk_profile.max_drawdown_pct` | Largest drop in open-position mark-to-market P&L, divided by the greater of total buy cost or $1, expressed as a percentage capped at 100. |
| `risk_profile.volatility` | Sample standard deviation divided by mean of positive activity costs: low below 0.5, medium below 1.5, otherwise high; unknown with fewer than two positive costs. |
| `risk_profile.max_position_concentration` | Largest single activity share count divided by total activity shares, or 0 if the total is nonpositive. |
| `recommendation` | Heuristic historical rating tier and selected factors for research. |

`--compare --json` returns `wallet1` and `wallet2`, each containing a report with these fields. Invalid wallet addresses or wallets with no fetched activity return `{}`.

## Usage Examples

### Research one wallet

```bash
python wallet_xray.py 0x1234...abcd --json
```

Review `profitability`, `entry_quality`, `behavior`, and `risk_profile` to describe historical patterns. The `recommendation` field summarizes a heuristic rating tier for research only. Test hypotheses against fresh data and consider fees, sample size, and missing activity.

### Compare historical profiles

```bash
python wallet_xray.py 0x1111... 0x2222... --compare --json --limit 100
```

Compare the two reports for differences in historical entries, drawdowns, and activity. Strong historical metrics do not establish future returns or justify copying positions.

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
