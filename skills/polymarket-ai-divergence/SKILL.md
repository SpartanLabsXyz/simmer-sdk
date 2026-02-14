---
name: polymarket-ai-divergence
displayName: Polymarket AI Divergence
description: Surface markets where Simmer's AI price diverges from Polymarket. High divergence = potential alpha. Use when user wants to find AI vs market disagreements, scan for trading opportunities, or understand where the AI is bullish/bearish relative to external prices.
metadata: {"clawdbot":{"emoji":"🔮","requires":{"env":["SIMMER_API_KEY"]},"cron":null,"autostart":false}}
authors:
  - Simmer (@simmer_markets)
version: "1.0.3"
published: true
---

# Polymarket AI Divergence Scanner

Surface markets where Simmer's AI-driven price diverges from Polymarket.

## When to Use This Skill

Use this skill when the user wants to:
- Find trading opportunities based on AI vs market disagreement
- Scan for high-divergence plays
- See where Simmer's AI is bullish/bearish vs Polymarket
- Understand AI pricing differences

## Quick Commands

```bash
# Show all divergences (>5% default)
python ai_divergence.py

# Quick status
python scripts/status.py

# Only high-divergence (>15%)
python ai_divergence.py --min 15

# Bullish only (AI > Polymarket)
python ai_divergence.py --bullish

# Bearish only (AI < Polymarket)
python ai_divergence.py --bearish

# Top opportunities summary
python ai_divergence.py --opportunities

# JSON output
python ai_divergence.py --json
```

**API Reference:**
- Base URL: `https://api.simmer.markets`
- Auth: `Authorization: Bearer $SIMMER_API_KEY`
- Markets: `GET /api/sdk/markets`

## Configuration

| Setting | Environment Variable | Default | Description |
|---------|---------------------|---------|-------------|
| API Key | `SIMMER_API_KEY` | (required) | Your Simmer SDK key |
| API URL | `SIMMER_API_URL` | `https://api.simmer.markets` | API base URL |

## How It Works

Each market has:
- `current_probability` — Simmer's AI-influenced price
- `external_price_yes` — Polymarket's price
- `divergence` — The difference (Simmer - Polymarket)

High divergence = potential alpha if the AI is right.

## Interpreting Signals

| Divergence | Meaning | Action |
|------------|---------|--------|
| > +10% | AI more bullish | Consider BUY YES |
| < -10% | AI more bearish | Consider BUY NO |
| ±5-10% | Mild divergence | Monitor |
| < ±5% | Aligned | No signal |

## Example Output

```
🔮 AI Divergence Scanner
===========================================================================
Market                                     Simmer     Poly      Div   Signal
---------------------------------------------------------------------------
Will bitcoin hit $1m before GTA VI?        14.2%   48.5%  -34.3%   🔴 SELL
What will be the top AI model this mon     17.9%    1.0%  +16.9%    🟢 BUY

💡 Top Opportunities (>10% divergence)
===========================================================================
📌 Will bitcoin hit $1m before GTA VI?
   AI says BUY NO (AI: 14% vs Market: 48%)
   Divergence: -34.3% | Resolves: 2026-07-31
```

## Example Conversations

**"Where does the AI disagree with Polymarket?"**
→ `python ai_divergence.py`

**"Any bullish opportunities?"**
→ `python ai_divergence.py --bullish --min 10`

**"What's the AI's highest conviction play?"**
→ `python ai_divergence.py --opportunities`

## Troubleshooting

**"SIMMER_API_KEY not set"**
→ Get key from simmer.markets/dashboard → SDK tab

**"No markets match filters"**
→ Lower `--min` threshold or remove directional filters
