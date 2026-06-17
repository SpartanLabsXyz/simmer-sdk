---
name: polymarket-soccer-shock-ladder
description: Fade sharp in-play price shocks on Polymarket soccer markets with a laddered limit-buy strategy (Roan's FIFA-quant framework). Pro skill. Currently scoped to 2026 World Cup markets. Simmer's server detects shocks in real time and emits pre-sized signals; this skill places the recovery ladder and manages the exit.
category: world-cup
tags:
  - world-cup
  - soccer
metadata:
  author: Simmer (@simmer_markets)
  version: "0.1.5"
  displayName: World Cup Shock Ladder
  difficulty: intermediate
  simmer:
    credit:
      name: "@RohOnChain"
      url: "https://x.com/RohOnChain"
      label: via
---
# World Cup Shock Ladder

Fade sharp price shocks on Polymarket World Cup markets. When a market's price
drops hard during a match, this skill places a laddered set of limit buys below
the pre-shock price, betting on a partial recovery, then exits each fill at a fixed
target.

The strategy follows @RohOnChain's published "Trade FIFA Like A Quant" framework.
The heavy lifting (detecting the shock, classifying it, sizing the rungs from
historical depth distributions) runs on Simmer's server in real time. This skill is
the thin execution layer: it reads the pre-sized signal and works the orders.

> 🚨 **Framework, not a guaranteed edge.** Read [DISCLAIMER.md](./DISCLAIMER.md)
> before going live. Runs in **dry-run by default**. Edges depend on spreads, fees,
> latency, and whether shocks actually recover. Start in dry-run, then go live small.

> **Requires Simmer Pro.** Shock signals are delivered over the Pro reactor stream.
> Upgrade at simmer.markets/dashboard if you hit a 402 on connect.

> **Polymarket only.** The ladder is a CLOB limit-order strategy. `$SIM` (the LMSR
> venue) has no order book to rest bids on, so `--venue sim` is a plumbing smoke test
> only, not a real run.

## How it works

A "shock" is a fast price drop on one outcome during an in-play match. Simmer's
server watches every WC market's live order flow, detects the drop, and figures out
how deep shocks like this one usually go (from its own accumulated history, bucketed
by favoritism, order-book depth, match minute, and goal state). It emits a signal
with the pre-shock price and the depth percentiles.

This skill turns that signal into Roan's ladder:

1. Four limit BUY orders on the shocked outcome, priced at `pre_price - depth` for
   the 50th, 75th, 90th, and 95th depth percentiles. Deeper rungs only fill if the
   price keeps falling.
2. Sizes weighted 10 / 20 / 30 / 40 percent of your per-shock stake (deeper rungs
   carry more, since bigger drops have more room to recover).
3. As rungs fill, it places a SELL for each at the fill price plus a fixed recovery
   target (default 4 cents).
4. Unfilled rungs are cancelled after a short TTL (default 60 seconds).

By default the skill only acts on **moderate-favorite** markets (pre-shock price
0.75 to 0.85), Roan's most profitable band. You can widen or change that.

## Setup

1. **Install the Simmer SDK** (0.17.0 or newer):
   ```bash
   pip install -U 'simmer-sdk>=0.17.0'
   ```

2. **Set your Simmer API key** (Pro plan required):
   ```bash
   export SIMMER_API_KEY=...   # simmer.markets/dashboard → SDK tab
   ```

3. **Set your Polymarket wallet key** (only for `--live`; not needed in dry-run):
   ```bash
   export WALLET_PRIVATE_KEY=0x...
   ```
   The SDK signs orders locally. External and OWS wallets work with no extra setup.

4. **Arm for shock signals** (self-serve, Pro required). Shock-ladder delivery is
   opt-in. Turn it on for your account:
   ```bash
   curl -X PATCH https://api.simmer.markets/api/sdk/shock-ladder/config \
     -H "Authorization: Bearer $SIMMER_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"enabled": true}'
   ```
   You get `{"shock_ladder_enabled": true}` back. The server starts fanning World Cup
   shocks to your reactor pending feed within ~5 minutes. Check your state anytime with
   a `GET` to the same URL, and disarm with `{"enabled": false}`.

## Running it

Dry-run is the default. It shows the exact ladder and exits it *would* place,
without trading. Review that before going live.

```bash
# Dry-run, single poll (safe; good for a cron)
python shock_ladder_trader.py --once

# Dry-run, continuous (poll every 2s)
python shock_ladder_trader.py

# LIVE, single poll
python shock_ladder_trader.py --once --live

# LIVE, continuous
python shock_ladder_trader.py --live
```

**Recommended: cron with `--once`.** Shock signals are time-sensitive; a 1-minute
cron gives reliable coverage even across restarts. A single `--once` run places a
ladder and then manages its fills and exits over the TTL window before exiting, so
one run fully handles one shock.

```bash
# crontab
*/1 * * * * cd /path/to/skill && python3 shock_ladder_trader.py --once --live

# OpenClaw cron
openclaw cron add --name "shock-ladder" --cron "*/1 * * * *" --tz UTC --session isolated \
  --message "Run: cd /path/to/skill && python3 shock_ladder_trader.py --once --live"
```

### Lower-latency alternative: bounded-continuous polling

The `--once` cron above is the simplest, most restart-proof setup, but it adds
0 to 60 seconds of entry latency: a shock that fires at second 5 of a minute
waits until the next cron tick to get a ladder placed. For a shock-*fade*
strategy (resting limit bids below the pre-shock price, betting on a recovery
that plays out over minutes), faster entry improves fill quality — you rest your
rungs closer to the bottom of the drop rather than after the book has already
started recovering.

The continuous mode (omit `--once`) polls every `--interval` seconds (2s by
default), which cuts entry latency to roughly 2 seconds. On its own, though, a
long-lived process loses the restart-resilience that makes the cron pattern
nice: if it hangs or dies, nothing brings it back. The fix is to **bound each
process to under a minute and let cron restart it every minute**, so a hung
process is reaped and a fresh one takes over on the next tick. You keep ~2s
entry latency *and* restart-resilience.

```bash
# crontab — start a fresh bounded-continuous run every minute
*/1 * * * * cd /path/to/skill && ./run_bounded.sh
```

**macOS note (no GNU `timeout`).** macOS ships without the GNU `timeout`
binary, so the usual `timeout 55s python3 ...` does not work out of the box. Use
a POSIX wrapper instead: background the child, sleep for the bound, then kill it.
This is the real-world validated pattern (an operator ran it cleanly in dry-run
at 2s polling):

```bash
#!/usr/bin/env bash
# run_bounded.sh — run the continuous loop, bounded to ~55s, cron restarts it.
set -euo pipefail
cd "$(dirname "$0")"

# Dry-run by default (no --live). Add --live only once you've validated the ladder.
python3 shock_ladder_trader.py --interval 2 &
child=$!

# Bound the run so a hung process is reaped before the next cron tick.
sleep 55
kill "$child" 2>/dev/null || true
wait "$child" 2>/dev/null || true
```

(Linux/coreutils hosts can use the simpler `timeout 55s python3
shock_ladder_trader.py --interval 2` and skip the wrapper.)

**Why 2s polling is safe.** Reactor signals carry a server-side TTL of 120
seconds in production. A 2s poll therefore has roughly a 60x margin against the
TTL: a fresh process started on any cron tick will see any signal still within
its TTL, so this pattern cannot miss a shock.

**Latency vs reliability — the tradeoff:**

| Pattern | Entry latency | Restart-proof | Setup |
|---|---|---|---|
| `--once` cron (recommended) | 0 to 60s | ✓ (cron re-runs) | simplest |
| bounded-continuous (this section) | ~2s | ✓ (bound + cron reap) | one wrapper script |

> **Note:** whether to promote bounded-continuous to the default recommendation
> is pending live edge-validation. For now it is documented as a lower-latency
> alternative; the `--once` cron above remains the recommended starting point.

## Configuration

All parameters are skill-side (no server config). Defaults are conservative.

| Setting | Env var | CLI flag | Default | Notes |
|---|---|---|---|---|
| Per-shock stake | `SHOCK_LADDER_STAKE_USD` | `--stake` | 15 | Total USDC split across the 4 rungs. |
| Venue | `SHOCK_LADDER_VENUE` | `--venue` | polymarket | `sim` is a smoke test only. |
| Order TTL | (CLI only) | `--ttl` | 60 | Seconds before unfilled rungs are cancelled. |
| Exit target | (CLI only) | `--exit-cents` | 4 | Cents above the fill for the exit sell. |
| Bucket filter | (CLI only) | `--buckets` | moderate | Comma-separated favoritism bands. Empty string acts on all. |
| Poll cadence | `SHOCK_LADDER_POLL_INTERVAL_S` | `--interval` | 2 | Fill-check / loop cadence in seconds. |

Favoritism bands (set with `--buckets`): `heavy`, `moderate`, `slight`, `balanced`,
`underdog`. Roan's tuning favored `moderate`. Example, act on moderate and slight:

```bash
python shock_ladder_trader.py --once --live --buckets moderate,slight
```

## What happens per signal

1. Poll `GET /api/sdk/reactor/pending`, keep only `type: "shock_ladder"` signals
   (so it never collides with copytrading signals on the same feed).
2. Apply the bucket filter. Out-of-band, malformed, or unsizable signals are
   skipped and removed (never retried).
3. Compute the 4 rung prices and sizes from `pre_price` and the depth percentiles.
4. Place the rungs as GTC limit buys (or log them in dry-run).
5. Poll fills; for each filled rung, place the exit sell at fill + target.
6. Cancel any rung still unfilled at the TTL.
7. Delete the signal so it is not reprocessed.

## Notes and limits

- **Dry-run first, always.** Confirm the laddered prices and sizes look right for a
  few real shocks before `--live`. Keep `--stake` small to start.
- **Partial fills.** The v1 fill check treats a rung that has left the order book as
  filled and sizes its exit from the rung's intended shares. Exact partial-fill
  reconciliation is a planned refinement. Keep size small until you have watched it
  on live shocks.
- **In-testing.** This is a Community / in-testing skill for the 2026 World Cup. Its
  bucket sizing sharpens as Simmer accumulates more observed shocks during the
  tournament.

## Troubleshooting

**"0 pending shock signals" every run**
- Normal between shocks. Shocks are rare and only fire during live matches.
- Confirm you are armed: a `GET https://api.simmer.markets/api/sdk/shock-ladder/config`
  (with your `SIMMER_API_KEY`) should return `{"shock_ladder_enabled": true}`. If not, re-run
  Setup step 4. Pro plan required.
- Pre-tournament there are no in-play shocks; expect signals once matches start.

**402 on connect**
- The reactor stream is Pro-gated. Upgrade at simmer.markets/dashboard.

**Orders rejected in `--live`**
- Check `WALLET_PRIVATE_KEY` is set and the wallet holds USDC on Polymarket.
- Thin books right after a shock can reject; the resting limit design tolerates this
  better than a market order, but very thin markets may still not fill.

**"venue=sim" warning**
- Expected. The ladder needs a CLOB; $SIM cannot run it. Use `--venue polymarket`.
