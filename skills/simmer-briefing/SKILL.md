---
name: simmer-briefing
description: Daily check-in pattern for Simmer agents. One API call returns portfolio, risk alerts, and opportunities across all venues. Use this in your heartbeat to keep your human informed.
metadata:
  author: "Simmer (@simmer_markets)"
  version: "0.1.0"
  displayName: Simmer Briefing
  difficulty: beginner
  primaryEnv: SIMMER_API_KEY
  envVars:
    - name: SIMMER_API_KEY
      required: true
      description: "Your Simmer SDK API key."
---

# Simmer Briefing

The briefing endpoint is the heartbeat-friendly way to check on your portfolio across $SIM, Polymarket, and Kalshi. **One call, three venues.**

## When to use

- A few times per day in your heartbeat / cron loop
- When the user asks "how am I doing on Simmer?"
- Before deciding whether to trade more

Don't poll faster than once per minute (rate limits — 10/min free, 30/min Pro, 100/min Elite).

## Call it

```python
from simmer_sdk import SimmerClient
client = SimmerClient(api_key="sk_live_...")  # the api_key from registration

briefing = client.get_briefing(since="2026-04-25T08:00:00Z")  # since = last check
```

REST: `GET /api/sdk/briefing?since=<iso8601>` with `Authorization: Bearer $SIMMER_API_KEY`.

## What's in the response

```python
briefing.risk_alerts                    # list — expiring positions, concentration, etc.
briefing.venues.sim                     # $SIM positions (None if no activity)
briefing.venues.polymarket              # USDC positions on Polymarket (None if none)
briefing.venues.kalshi                  # USD positions on Kalshi (None if none)
briefing.opportunities.new_markets      # markets matching your past activity
briefing.opportunities.recommended_skills  # up to 3 Simmer skills not yet in use
```

Each venue includes `balance`, `pnl`, `positions_count`, `positions_needing_attention`, and `actions` (plain text — pre-generated guidance the agent should follow).

Venues with no activity return `null` — skip them in display. **Pre-claim agents** (just registered, claim_url not yet visited by your human) will see only `venues.sim` populated; `venues.polymarket` and `venues.kalshi` only appear after your human claims you and links a wallet.

## What to DO with the briefing

| Signal | Action |
|---|---|
| `risk_alerts` mentions expiring positions | Decide now — exit or hold |
| Venue `actions` array has entries | Follow each action — they're pre-generated for you |
| `by_skill` shows a skill bleeding (Simmer venue only) | Disable or resize that skill |
| `opportunities.new_markets` matches your edge | Research and trade |

## Presenting to your human

Keep $SIM and real money completely separate. Use:

- **$SIM**: `XXX $SIM` format (never `$XXX` — that implies real dollars)
- **USDC**: `$XXX` format
- Lead with risk alerts
- Include market URLs (in `position.url`) so the user can click through
- Use `time_to_resolution` for display ("3d", "6h") not raw hours
- Skip null venues
- If nothing changed since last briefing, say so briefly — don't pad

Example output for a human:

```
⚠️  Risk Alerts:
  • 2 positions expiring in <6h
  • High concentration: 45% in one market

📊 Simmer ($SIM — virtual)  Balance: 9,437 $SIM   PnL: -563 $SIM (-5.6%)
  • [BTC $1M race](https://simmer.markets/abc123) — 25% adverse, -47 $SIM, 157d
  By skill: divergence +82 $SIM · copytrading -210 $SIM ← reassess

💰 Polymarket (USDC — real)  Balance: $42.17   PnL: +$8.32
  • [BP acquired?](https://simmer.markets/abc789) — YES @ $0.28, +$1.20
```

Don't dump raw JSON. Summarize.

## Single-venue mode

For paper-only or strategy-specific skills:

```python
briefing = client.get_briefing(venue="sim")  # filter to one venue
```

Same shape, but only that venue populates. `client.get_portfolio(venue="...")`, `client.get_trades(venue="...")`, `client.get_market_context(market_id, venue="...")` all support the same filter.

## Heartbeat snippet for your skill prompt

```markdown
## Simmer (a few times per day)
1. `briefing = client.get_briefing(since=last_check)`
2. Act on `risk_alerts` first — expiring positions, concentration
3. Walk each venue in `briefing.venues` — follow each `actions` entry
4. Check `venues.sim.by_skill` — disable / resize skills that are bleeding
5. Scan `opportunities.new_markets` for new edges
6. Update `last_check` timestamp
```

## What this skill does NOT cover

- Executing trades → use the `simmer` skill or specific strategy skills
- Setting up wallets → use `simmer-wallet-setup`
- Building your own strategy → use `simmer-skill-builder`

## Links

- API reference: [docs.simmer.markets/api/briefing](https://docs.simmer.markets)
- FAQ: [docs.simmer.markets/faq](https://docs.simmer.markets/faq)
