---
name: polymarket-worldcup-parlay-roller
description: Build parlay-style multi-leg positions on Polymarket World Cup markets leg by leg on the CLOB - stake leg 1, roll proceeds into leg 2 on a win, until the streak completes or a leg loses. Free skill. Combo-implied pricing shown for comparison; routes through real Polymarket combos when their taker API ships.
category: world-cup
tags:
  - world-cup
  - soccer
  - parlay
metadata:
  author: Simmer (@simmer_markets)
  version: "0.1.0"
  displayName: Worldcup Parlay Roller
  difficulty: beginner
---

# Worldcup Parlay Roller

Express a World Cup parlay today, on the CLOB. Polymarket's new combo positions
(RFQ parlays) have no public taker API yet, so this skill builds the same payoff
leg by leg: stake leg 1, sell the winner after the match, roll all proceeds into
leg 2, and repeat. One lost leg ends the streak. Max loss is the initial stake.

Read [DISCLAIMER.md](./DISCLAIMER.md) before going live. This is a framework,
not a guaranteed edge, and it runs in dry-run by default.

Free skill. Works on any Simmer account. Polymarket venue only; `$SIM` has no
World Cup markets (`--venue sim` is a plumbing smoke test).

## Setup

Agent: do this once with the user.

1. Get the thesis. The user gives it in natural language or from a post, for
   example: "Mexico win the opener, then USA beat Paraguay."
2. Resolve each pick to a concrete Simmer market: binary team-win, draw, or NO
   markets. Use `client.get_markets(search=...)` or the dashboard. Record each
   market id, side (`yes` or `no`), and kickoff time in UTC.
3. Confirm with the user, restating exact resolution conditions. Example:
   "Leg 1: Mexico WIN; a draw LOSES this leg." Draw legs and NO legs are
   supported.
4. Validate order. Legs must be in kickoff order and must not overlap.
   Simultaneous final-round group matches cannot roll proceeds.
5. Write `roller_config.json` from `roller_config.example.json`, max 5 legs.
6. Dry-run first: `python parlay_roller_trader.py --once`. Review the log, then
   go live.

## Run

```bash
python parlay_roller_trader.py --once
python parlay_roller_trader.py --once --live
python parlay_roller_trader.py --status
python parlay_roller_trader.py --abort
```

Cron guidance: 1-minute cron while a leg's match window is live or settling
(kickoff to proceeds banked); lazy or on-demand otherwise. Every state
transition is appended to the streak log.

## How It Rolls

- Entry: limit at mid + 1 cent, 120s TTL, retried while the entry window is
  open. Window closes 15 minutes before kickoff.
- Exit: after the match, sell the winner once the bid is at least 0.97. This is
  faster than waiting for redemption but usually takes a small haircut.
- Loss: post-match bid below 0.03 marks the streak BUSTED; dust is held to
  resolution.
- Anomalies: unresolved more than 6 hours past expected end, or voided market,
  pauses the skill with no automatic action.
- Take-profit: optional `bank_half_after: N` banks half the proceeds after
  winning leg N and rolls the rest.

## Combo Comparison

At setup (the first tick of a fresh streak), the skill fetches Polymarket's
public combo catalog and prints the combo-implied price next to the streak's
naive leg-product price. Correlated legs make the product an approximation. If
the catalog is unreachable, comparison is simply unavailable. When Polymarket
ships the combo taker API, v2 can route through real combos when cheaper.
