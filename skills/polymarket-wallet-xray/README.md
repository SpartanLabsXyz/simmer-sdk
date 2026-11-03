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

- **Time Profitable** — % of time wallet was not underwater
- **Win Rate** — % of trades profitable
- **Entry Quality** — Average slippage from optimal price
- **Bot Detection** — Trading speed pattern analysis
- **Arbitrage Edge** — Combined YES+NO average < $1.00
- **Risk Profile** — Drawdowns and volatility
- **Research summary** — Which historical patterns merit further study?

## Implementation Notes

Public analysis fetches wallet activity directly from `https://data-api.polymarket.com/v2/activity` without authentication. It then:

1. Tracks positions over time (YES/NO shares and cost basis)
2. Realizes P&Ls when positions are closed
3. Computes statistical metrics (mean, stdev, etc.)
4. Summarizes a historical rating tier for research, not copying or position sizing

All metrics are based on @thejayden's "Autopsy of a Polymarket Whale" framework.

## Attribution

Inspired by [@thejayden](https://x.com/thejayden)'s forensic trading analysis post:
https://x.com/thejayden/status/2020891572389224878
