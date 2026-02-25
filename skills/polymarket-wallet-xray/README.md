# Polymarket Wallet X-Ray

X-ray any Polymarket wallet — trading patterns, skill level, and edge detection.

## Files

- **SKILL.md** — User documentation, quick start, usage examples, troubleshooting
- **wallet_xray.py** — Main analysis script
- **scripts/status.py** — Portfolio status helper

## Quick Start

```bash
# Analyze a wallet
python wallet_xray.py 0x1234...abcd

# Analyze wallet for specific market
python wallet_xray.py 0x1234...abcd "Bitcoin"

# Output as JSON
python wallet_xray.py 0x1234...abcd --json

# Compare two wallets
python wallet_xray.py 0xaaa... 0xbbb... --compare
```

## Installation

```bash
pip install simmer-sdk requests
export SIMMER_API_KEY="sk_live_..."
```

## Metrics Computed

- **Time Profitable** — % of time wallet was not underwater
- **Win Rate** — % of trades profitable
- **Entry Quality** — Average slippage from optimal price
- **Bot Detection** — Trading speed pattern analysis
- **Arbitrage Edge** — Combined YES+NO average < $1.00
- **Risk Profile** — Drawdowns and volatility
- **Recommendation** — Should you copytrade this wallet?

## Implementation Notes

The skill fetches trades from the Simmer API, which aggregates Polymarket data. It then:

1. Tracks positions over time (YES/NO shares and cost basis)
2. Realizes P&Ls when positions are closed
3. Computes statistical metrics (mean, stdev, etc.)
4. Generates a recommendation score

All metrics are based on @thejayden's "Autopsy of a Polymarket Whale" framework.

## Attribution

Inspired by [@thejayden](https://x.com/thejayden)'s forensic trading analysis post:
https://x.com/thejayden/status/2020891572389224878
