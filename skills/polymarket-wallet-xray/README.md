# Polymarket Wallet X-Ray

X-ray any Polymarket wallet — trading patterns, skill level, and edge detection.

## Files

- **SKILL.md** — User documentation, quick start, usage examples, troubleshooting
- **wallet_xray.py** — Main analysis script
- **scripts/status.py** — Optional read-only Simmer account status helper

## Quick Start

All shortened wallet addresses are placeholders. Replace them with complete public Polymarket wallet addresses.

```bash
# Analyze a wallet
python wallet_xray.py 0x1234...abcd

# Output as JSON
python wallet_xray.py 0x1234...abcd --json

# Compare two wallets
python wallet_xray.py 0xaaa... 0xbbb... --compare
```

## Installation

Public analysis uses only Python's standard library. No SDK, package install, API key, or wallet connection is needed.

Only when you explicitly request your own Simmer account status, configure `SIMMER_API_KEY` in your environment outside chat and run `python scripts/status.py`. Never paste the key into chat. This optional helper sends it as a Bearer header to `https://api.simmer.markets` for read-only portfolio and position queries. With no key it exits before networking.

## Metrics Computed

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

## Implementation Notes

Public analysis fetches wallet activity directly from `https://data-api.polymarket.com/v2/activity` without authentication. It then:

1. Tracks positions over time (YES/NO shares and cost basis)
2. Realizes P&Ls when positions are closed
3. Computes statistical metrics (mean, stdev, etc.)
4. Summarizes a historical rating tier for research only

All metrics are based on @thejayden's "Autopsy of a Polymarket Whale" framework.

## Attribution

Inspired by [@thejayden](https://x.com/thejayden)'s forensic trading analysis post:
https://x.com/thejayden/status/2020891572389224878
