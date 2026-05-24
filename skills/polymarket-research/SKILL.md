---
name: polymarket-research
description: Research any Polymarket topic — get market data, probabilities, volumes, and top holders in one snapshot. Read-only, no trading.
metadata:
  author: "Simmer (@simmer_markets)"
  version: "1.0.0"
  displayName: Polymarket Research
  difficulty: beginner
---

# Polymarket Research

Get a structured research snapshot for any Polymarket topic. Returns market probabilities, volume across time windows, and the top holders (largest positions) per outcome. Read-only — no trades, no wallet needed.

> **This is a template.** The default output is a research snapshot — remix it to feed into your own trading logic, dashboard, or analysis pipeline. The skill handles market discovery and data retrieval. Your agent decides what to do with the signal.

## Setup

```bash
pip install simmer-sdk
export SIMMER_API_KEY="sk_live_..."
```

## Quick start

```bash
python research.py "bitcoin price"
python research.py "us election" --top-holders 20
python research.py "weather" --min-volume 100000
```

## What you get

For each matching market:

| Field | Source |
|---|---|
| Question, status, resolution date | Simmer SDK |
| YES/NO probability | Simmer SDK (synced from Polymarket CLOB) |
| 24h volume | Simmer SDK |
| Top holders per outcome | Polymarket data API (free, no auth) |
| Edge analysis vs AI price | Simmer SDK context endpoint |

## Configuration

| Parameter | Env var | Default | Description |
|---|---|---|---|
| Search query | (CLI arg) | required | Topic to search for |
| Min volume | `SIMMER_RESEARCH_MIN_VOLUME` | 10000 | Skip markets below this 24h volume |
| Max results | `SIMMER_RESEARCH_MAX_RESULTS` | 5 | Markets to return |
| Top holders | `SIMMER_RESEARCH_TOP_HOLDERS` | 10 | Holders per outcome |

## Example output

```
=== Bitcoin Price Markets ===

1. Will BTC hit $150k by end of 2026?
   YES: 32.5% | NO: 67.5%
   Volume (24h): $284,102
   Resolves: 2026-12-31

   Top holders (YES):
     Gabagool22: 142,301 shares
     xuanxuan008: 89,442 shares
     marketing101: 67,221 shares

   Top holders (NO):
     0xe1d6...907: 203,112 shares
     sharky6999: 156,004 shares
```

## Troubleshooting

- **No markets found**: try broader search terms or lower `--min-volume`
- **No top holders**: market may be new or have low participation; the holders endpoint returns empty for very new markets
- **Timeout on holders**: the Polymarket data API is occasionally slow; the skill retries once with a 10s timeout
