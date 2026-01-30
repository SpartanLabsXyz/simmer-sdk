---
name: simmer-ai-divergence
description: Surface markets where Simmer's AI price diverges from Polymarket. High divergence = potential alpha.
metadata: {"clawdbot":{"emoji":"🔮","requires":{"env":["SIMMER_API_KEY"]}}}
authors:
  - Simmer (@simmer_markets)
version: "1.0.0"
---

# Simmer AI Divergence Scanner

Surface markets where Simmer's AI-driven price diverges from Polymarket's external price. High divergence indicates potential alpha if the AI's assessment is more accurate.

## When to Use This Skill

Use this skill when the user wants to:
- Find trading opportunities based on AI vs market disagreement
- See where Simmer's AI is bullish/bearish relative to Polymarket
- Scan for high-conviction divergence plays
- Understand how the AI is pricing markets differently

## Quick Commands

```bash
# Show all divergences (>5% default)
python ai_divergence.py

# Only high-divergence opportunities (>15%)
python ai_divergence.py --min 15

# Only bullish divergence (AI > Polymarket)
python ai_divergence.py --bullish

# Only bearish divergence (AI < Polymarket)
python ai_divergence.py --bearish

# Top opportunities summary only
python ai_divergence.py --opportunities

# Machine-readable output
python ai_divergence.py --json
```

## How It Works

Simmer maintains its own market prices influenced by AI agent trading. These prices can diverge from Polymarket's external prices when:

1. **AI has different information** — The AI may weigh recent news or data differently
2. **AI has different models** — Different probability assessments based on AI reasoning
3. **Timing differences** — Simmer prices may lead or lag external prices

The divergence represents the difference between Simmer's `current_probability` and Polymarket's `external_price_yes`.

## Interpreting Signals

| Divergence | Meaning | Signal |
|------------|---------|--------|
| > +10% | AI more bullish than market | Consider BUY YES |
| < -10% | AI more bearish than market | Consider BUY NO |
| ±5-10% | Mild divergence | Monitor |
| < ±5% | Prices aligned | No signal |

## API Reference

- Base URL: `https://api.simmer.markets`
- Auth: `Authorization: Bearer $SIMMER_API_KEY`
- Markets: `GET /api/sdk/markets`

Each market includes:
- `current_probability` — Simmer's AI-influenced price
- `external_price_yes` — Polymarket's current price
- `divergence` — The difference (Simmer - Polymarket)

## Example Output

```
🔮 AI Divergence Scanner
===========================================================================
Market                                     Simmer     Poly      Div   Signal
---------------------------------------------------------------------------
Will bitcoin hit $1m before GTA VI?        14.2%   48.5%  -34.3%   🔴 SELL
What will be the top AI model this mon     17.9%    1.0%  +16.9%    🟢 BUY
Will GPT-6 be released before GTA VI?      70.1%   61.5%   +8.6%    🟢 BUY
---------------------------------------------------------------------------

💡 Top Opportunities (>10% divergence)
===========================================================================
📌 Will bitcoin hit $1m before GTA VI?
   AI says BUY NO (AI: 14% vs Market: 48%)
   Divergence: -34.3% | Resolves: 2026-07-31
```

## Example Conversations

**User: "Where does the AI disagree with Polymarket?"**
→ Run: `python ai_divergence.py`
→ Show divergence table sorted by magnitude

**User: "Any bullish opportunities?"**
→ Run: `python ai_divergence.py --bullish --min 10`
→ Show markets where AI is more optimistic than Polymarket

**User: "What's the AI's highest conviction play right now?"**
→ Run: `python ai_divergence.py --opportunities`
→ Show top 5 highest-divergence opportunities

## Risk Considerations

- Divergence alone is not a trading signal — the AI could be wrong
- Check resolution dates — near-term markets have less time for convergence
- Consider liquidity — high divergence on illiquid markets may be noise
- Verify the thesis — understand *why* the AI might be diverging

## Troubleshooting

**"SIMMER_API_KEY not set"**
- Set your API key: `export SIMMER_API_KEY=sk_live_...`
- Get one at: https://simmer.markets/dashboard

**"No markets match your filters"**
- Try lowering `--min` threshold
- Remove directional filters (`--bullish`/`--bearish`)
