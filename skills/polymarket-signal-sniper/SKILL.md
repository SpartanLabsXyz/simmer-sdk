---
name: polymarket-signal-sniper
description: Monitor RSS feeds for news that may move Polymarket markets, configure custom signal sources and keywords, and get article + market pairs with safeguard warnings. Never trades; your agent decides whether and how to trade.
metadata:
  author: Simmer (@simmer_markets)
  version: "2.0.0"
  displayName: Polymarket Signal Sniper
  difficulty: intermediate
---
# Polymarket Signal Sniper

Your signals, Simmer's market safeguards, your agent's judgment.

> 🚨 **The skill never trades.** It hands your agent article + market pairs. Your agent decides whether the article bears on the market, which side, and how much. Read [DISCLAIMER.md](./DISCLAIMER.md) before your agent trades with real funds.

> **This is a template.** The default signal source is RSS feeds. Remix it with any data source (APIs, webhooks, social media, custom scrapers). The skill handles the plumbing (feed polling, market matching, safeguards, dedup). Your agent provides the judgment.

**Changed in 2.0.0:** earlier versions guessed the trade side from keyword sentiment and could place orders with `--live`. Keyword sentiment cannot tell whether a headline is about a market, so that path is gone. `--live`, `--dry-run` and `--scan-only` are still accepted so old cron lines keep working, but they have no effect.

## Setup Flow

When user asks to install or configure this skill:

1. **Install the Simmer SDK**
   ```bash
   pip install simmer-sdk
   ```

2. **Ask for Simmer API key**
   - They can get it from simmer.markets/dashboard → SDK tab
   - Store in environment as `SIMMER_API_KEY`

The skill needs no wallet key, because it never trades. If your agent will act on signals, set up trading separately with the `simmer-wallet-setup` skill.

## Quick Start (Ad-Hoc Usage)

**User provides RSS feed and market directly:**
```
User: "Watch this RSS feed for greenland news: https://news.google.com/rss/search?q=greenland"
User: "Tell me about any trump news from this feed that could move my markets"
```

→ Run with `--feed`, `--market` and `--json`:
```bash
python signal_sniper.py --feed "https://news.google.com/rss/search?q=greenland" --market "greenland-acquisition" --json
```

## Persistent Setup (Optional)

For recurring scans, configure via environment:

| Setting | Environment Variable | Default | Description |
|---------|---------------------|---------|-------------|
| RSS Feeds | `SIMMER_SNIPER_FEEDS` | (none) | Comma-separated RSS URLs |
| Markets | `SIMMER_SNIPER_MARKETS` | (auto) | Comma-separated market IDs (auto-discovers from keywords if empty) |
| Keywords | `SIMMER_SNIPER_KEYWORDS` | (none) | Comma-separated keywords to match |

## How It Works

Each cycle the script:
1. Polls configured RSS feeds
2. Filters articles by keywords (if configured)
3. Picks target markets (auto-discovers from keywords if no markets configured)
4. Calls the SDK context endpoint for each market's safeguards:
   - Position awareness (already holding?)
   - Flip-flop detection (recently changed direction?)
   - Slippage estimates (is market liquid?)
   - Time decay (resolving soon?)
   - Resolution criteria (what actually resolves this market?)
5. Pairs each new article with each market that passes the safeguards
6. Prints the pairs, or emits them as JSON with `--json`
7. Tracks processed articles to avoid duplicates

It places no trades and makes no wallet calls.

## Running the Skill

**Run a scan:**
```bash
python signal_sniper.py
```

**Get signals as JSON for your agent** (logs go to stderr, JSON to stdout):
```bash
python signal_sniper.py --json
```

**View current config:**
```bash
python signal_sniper.py --config
```

**Override for one run:**
```bash
python signal_sniper.py --feed "https://..." --keywords "trump,greenland" --market "abc123"
```

**Show processed articles:**
```bash
python signal_sniper.py --history
```

### JSON output

```json
{
  "signals": [
    {
      "article": {"title": "...", "url": "...", "summary": "...", "published": "..."},
      "market": {"id": "...", "question": "...", "resolution_criteria": "...",
                 "current_price": 0.42, "time_to_resolution": "5d 3h"},
      "warnings": ["Moderate slippage (11.0%)"]
    }
  ],
  "error": null
}
```

A pair means the article matched a keyword and the market passed the safeguards. It does not mean the article is about the market.

## Interpreting Context Warnings

The `warnings` list on each signal comes from the market's context:

| Warning | Action |
|---------|--------|
| `Market resolves in Xh - elevated risk` | Consider if signal is timely enough |
| `Mild flip-flop warning` | Proceed carefully, need strong signal |
| `High slippage` / `Moderate slippage` | Reduce position size or skip |
| `Edge ... below threshold` | The market may already reflect this |

Markets that are resolved, resolve within 2 hours, have a severe flip-flop warning or a spread above 10% are dropped before pairing.

## Analyzing Signals

For each signal, your agent should:

1. **Read the headline and summary.** What is the actual news?

2. **Check resolution_criteria.** What ACTUALLY resolves this market?
   - Example: "greenland" in a headline doesn't mean "acquisition complete"
   - The resolution might be "US formally acquires Greenland by 2027"
   - Does this article move the needle on THAT specific criterion? Most pairs will not.

3. **Ask whether it is priced in.** Compare `published` with recent price movement. News on a public RSS feed has often moved the price already.

4. **Only trade if** the article bears on the resolution criteria, it is not priced in, and no warning argues against it. If it trades, pass `source="sdk:signalsniper"` so the trade is attributed to this skill:
   ```python
   client.trade(market_id=..., side="yes", amount=10.0, source="sdk:signalsniper",
                skill_slug="polymarket-signal-sniper")
   ```

## Example Conversations

**User: "Set up news sniping for the Greenland market"**
→ Ask for RSS feeds they want to monitor
→ Configure with market ID and keywords
→ Enable cron for recurring scans, and read the JSON each run

**User: "Check this feed for trading signals"**
→ Run: `python signal_sniper.py --feed "URL" --json`
→ Assess each pair and report the ones that bear on resolution

**User: "Snipe any bitcoin news from CoinDesk"**
→ Run with CoinDesk RSS and bitcoin-related markets
→ Show relevant pairs and ask if they want to trade

**User: "What signals have we processed?"**
→ Run: `python signal_sniper.py --history`
→ Show recent articles and the markets they were paired with

## Example Flow

```
1. RSS poll finds: "Trump and Denmark reach preliminary Greenland agreement"
2. Keywords match: "greenland", "trump"
3. Market "greenland-acquisition-2027" passes safeguards → signal emitted
4. Agent reads resolution criteria: "Resolves YES if US formally acquires Greenland by 2027"
5. Agent's view: "preliminary agreement" ≠ "formally acquires"; price already up 4% today
6. Agent decides: no trade, report to user
```

## Troubleshooting

**"No feeds configured"**
- Provide feeds in message: "watch this RSS: https://..."
- Or set `SIMMER_SNIPER_FEEDS` environment variable

**"No matching articles found"**
- Check keywords are correct
- RSS feed might not have recent articles

**"Skipping: safeguards failed"**
- The market is resolved, resolving within 2 hours, illiquid, or you have been reversing on it
- Working as intended

**"Already processed"**
- This article was already seen
- Working as intended (dedup)

**"--live has no effect"**
- Since 2.0.0 the skill never trades. Your agent places any trade itself.

## Finding Good RSS Feeds

Tips for choosing signal sources:
- **Google News RSS**: `https://news.google.com/rss/search?q=YOUR_TOPIC`
- **Niche sources**: Better than mainstream (less priced in)
- **Official sources**: Government, company announcements
- **Twitter lists → RSS**: Use services like Nitter or RSS.app

The skill works best when:
- Feeds are relevant to your target markets
- You have specific keywords to filter noise
- Sources publish before mainstream coverage
