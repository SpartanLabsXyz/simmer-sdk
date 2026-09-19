---
name: polymarket-nothing-ever-happens
description: Buy NO on standalone non-sports yes/no Polymarket markets priced below a configurable cap. Based on the "nothing-ever-happens" thesis — binary markets often resolve NO, and cheap NO shares offer asymmetric value. Scans for candidates via Gamma API, filters out sports and grouped markets, checks fees, and executes.
metadata:
  author: Simmer (@simmer_markets)
  version: "1.1.3"
  displayName: Polymarket Nothing-Ever-Happens
  difficulty: beginner
---
# Polymarket Nothing-Ever-Happens Trader

Buy NO on standalone yes/no Polymarket markets priced below a configurable cap.

> 🚨 **Framework, not a production trading system.** Read [DISCLAIMER.md](./DISCLAIMER.md) before connecting to a wallet with real funds.

## What's New in v1.1.3

- **Club/league sports tokens (SIM-5518).** Replay text now drops Celta/Bayern-class matchups and league-only titles (`bundesliga`, `premier league`, …). Political `X vs Y` stays eligible. Live is still tag/category.
- **Replay already-holding stays at `max_bet`.** `get_positions()` omits the venue filter under replay (replay 422s on `venue`, same class as weather SIM-5484). A held `market_id` is not re-bought on later ticks.
- **`price_cap` docs match code.** Default is `0.10`. `CONFIG_SCHEMA` is the source of truth.

## What's New in v1.1.2

- **Standalone is tape-authoritative (CTO pass 2).** The replay listing is requested at `limit=1000` (the whole tape slice), so sibling counting covers every market on the tape, and rows with no `event_id` are dropped rather than admitted. The remaining FIX caveat is only that the tape itself may omit a sibling Polymarket had.

## What's New in v1.1.1

- **CTO review P1.** Live `ensure_can_trade` with a missing or non-numeric `max_safe_size` now refuses to trade (no uncapped `MAX_BET_USD`). Replay listings expose `event_id`; the skill drops events that have more than one row on the page (standalone = live Gamma `len(markets)==1`).
- **FIX caveat.** If the tape omits `event_id`, or the listing page only shows one grouped leg, that row still enters. Tape `volume` stands in for both liquidity and 24h volume.

## What's New in v1.1.0

- **Replay plane (SIM-5443).** Under `simmer backtest` (`SIMMER_REPLAY=1`) discovery, import, and the clock come from the existing replay harness — same `SIMMER_REPLAY` / `SIMMER_REPLAY_NOW` pattern as BTC up-down and weather. Live Gamma stays dark. Import accepts replay `status=active`. Sports still drop when tape tags are empty (question/slug tokens). Daily-spend uses the frozen tick, not wall clock.

> **This is a template.** The default logic buys NO on any non-sports standalone market where NO costs ≤10¢. Remix it with custom filters (minimum volume thresholds, specific categories, date ranges) or pair it with a signal to skip markets where YES might actually happen. The skill handles plumbing (discovery, import, fee checks, execution). You define which markets to trade.

> **Based on:** [sterlingcrispin/nothing-ever-happens](https://github.com/sterlingcrispin/nothing-ever-happens)

## The Thesis

On most standalone binary prediction markets, the event resolves NO — nothing dramatic happens. Markets systematically overprice dramatic YES outcomes. When NO is trading at 3¢–5¢, you're getting 20–33x payout if you're right, and the base rate of "nothing happens" is often much higher than the implied 3–5%.

> ⚠️ **Automated stop-losses cannot protect against gap-resolution.** Many NO-fade targets, weather temperature buckets especially, do not decay toward their losing outcome. They gap: the price sits near your entry, then jumps straight to about 0 at resolution. A percentage stop has no intermediate price to trigger on and no liquidity to exit into once it is near zero, so it cannot cap your downside on these markets. Size every position for the full loss, not for the stop. See [DISCLAIMER.md](./DISCLAIMER.md).

## What It Does

1. **Scans** Polymarket events via Gamma API for standalone yes/no markets
2. **Filters** out sports, grouped events, low-liquidity markets
3. **Selects** markets where NO ask ≤ price cap (default 10¢)
4. **Imports** each candidate into Simmer
5. **Checks** fees (only trades zero-fee markets) and safeguards
6. **Buys NO** via Simmer SDK, sized by `max_bet_usd`

## Setup Flow

When user asks to install or configure this skill:

1. **Install the Simmer SDK**
   ```bash
   pip install simmer-sdk
   ```

2. **Ask for Simmer API key**
   - They can get it from simmer.markets/dashboard → SDK tab
   - Store in environment as `SIMMER_API_KEY`

3. **Ask for wallet private key** (required for live trading)
   - This is the private key for their Polymarket wallet (the wallet that holds USDC)
   - Store in environment as `WALLET_PRIVATE_KEY`
   - The SDK uses this to sign orders client-side automatically — no manual signing needed
   - Not needed for $SIM paper trading on the Simmer venue

## Quick Commands

```bash
# Scan for candidates (no trades)
python nothing_ever_happens.py --scan

# Dry run — show what would trade
python nothing_ever_happens.py

# Execute real trades
python nothing_ever_happens.py --live

# Quiet mode (for cron — only prints on trades/errors)
python nothing_ever_happens.py --live --quiet

# Show config
python nothing_ever_happens.py --config

# Update config
python nothing_ever_happens.py --set price_cap=0.03
```

## Configuration

| Key | Env Var | Default | Description |
|-----|---------|---------|-------------|
| — | `TRADING_VENUE` | polymarket | Venue to trade on. Set `sim` for $SIM paper trading on the Simmer venue (no wallet, no USDC). |
| `price_cap` | `SIMMER_NEH_PRICE_CAP` | 0.10 | Max NO price to buy (0.10 = 10¢). Code (`CONFIG_SCHEMA`) is source of truth. |
| `max_bet_usd` | `SIMMER_NEH_MAX_BET_USD` | 5.0 | USDC per trade |
| `max_trades_per_run` | `SIMMER_NEH_MAX_TRADES_PER_RUN` | 3 | Max trades per execution |
| `daily_budget` | `SIMMER_NEH_DAILY_BUDGET_USD` | 15.0 | Daily spend limit |
| `min_liquidity` | `SIMMER_NEH_MIN_LIQUIDITY` | 500.0 | Min market liquidity (USDC) |
| `min_volume_24h` | `SIMMER_NEH_MIN_VOLUME_24H` | 100.0 | Min 24h volume (USDC) |
| `candidate_pages` | `SIMMER_NEH_CANDIDATE_PAGES` | 3 | Gamma API pages to scan |

Update via CLI: `python nothing_ever_happens.py --set price_cap=0.03`

## How It Works

### Market Discovery

The skill fetches active Polymarket events via the Gamma API, sorted by 24h volume. Under `simmer backtest` (`SIMMER_REPLAY=1`) it lists from the Simmer tape instead — live Gamma would be look-ahead. It only considers **standalone events** — events with exactly one market. Grouped events (e.g. "Who wins Iowa?" alongside "Who wins Florida?" in a presidential election group) are excluded because:
- They're often higher-profile and more efficiently priced
- The original strategy targets isolated, under-the-radar markets

### Filters Applied

1. **Standalone** — event has exactly 1 market
2. **Binary yes/no** — outcomes are exactly `["Yes", "No"]`
3. **Non-sports** — category and tags must not be sports-related. Replay also drops club/league tokens in question/slug (Celta/Bayern class) because tape tags are empty.
4. **Price cap** — NO price ≤ configured cap (default 10¢)
5. **Liquidity** — event liquidity ≥ 500 USDC
6. **Volume** — event 24h volume ≥ 100 USDC

### After Candidate Selection

For each candidate, the skill:
1. **Imports** the market into Simmer (needed to get a Simmer market ID for trading)
2. **Skips** markets where you already hold a position
3. **Checks fees** — only trades zero-fee markets (fee drag destroys edge on cheap NO)
4. **Checks safeguards** — skips markets with severe flip-flop warnings
5. **Executes** `client.trade(side="no", amount=max_bet_usd)`

### Risk Profile

- You pay the ask price for NO shares (default ≤10¢ each)
- If YES resolves: you lose your bet. If NO resolves: you collect $1/share
- Expected value depends on how often YES actually resolves in these markets
- The thesis: base rate of NO is much higher than 10%, so a 10x payout at the default cap is favorable
- **This is not guaranteed profit.** The edge depends on market selection quality.

## API Endpoints Used

- Gamma API `/events` — Discover active Polymarket events
- `POST /api/sdk/import` — Import market to Simmer (via `client.import_market()`)
- `GET /api/sdk/context/{market_id}` — Fee rate and safeguards
- `POST /api/sdk/trade` — Trade execution
- `GET /api/sdk/positions` — Current positions (avoid doubling up)

## Troubleshooting

### Replay KEEP / KILL (SIM-5443)

Reuse `simmer backtest` / `SIMMER_REPLAY=1`. Do not invent a second harness. Pinned path check:

```bash
python -m pytest skills/polymarket-nothing-ever-happens/tests/test_replay_plane.py -q
```

| Verdict | What it means |
|---------|----------------|
| **FIX** | Path is broken or the tape cannot evaluate the skill. Do not add capital. Repair: live Gamma under replay (look-ahead); `Unexpected import status: active`; wall-clock daily-spend; sports leak from empty tape tags (league/club tokens now drop; unnamed clubs with no listed token can still leak); listing 422 from `tags`/`status`; positions 422 from `venue` (already-holding must omit venue under replay). Replay standalone uses `event_id` over the whole tape slice (`limit=1000`); rows with no `event_id` are dropped. Only a tape that omits a sibling can still admit a grouped leg. Tape `volume` stands in for liquidity and 24h volume. |
| **KILL** | Pinned pytest fails, **or** a full-tape run has evals > 0 and the skill still cannot reach `trade()` on a tape cheap-NO, **or** P&L after costs is clearly ≤ 0 on honest (not live-Gamma) prices. No second-lane capital. |
| **KEEP** | Full-tape `simmer backtest` with evals > 0 **and** trades > 0 on tape prices and `SIMMER_REPLAY_NOW`. Pinned unit tests are a path check only — not KEEP. |

**"No candidates below price cap"**
→ All standalone non-sports markets have NO > cap. Lower `price_cap` (e.g. `--set price_cap=0.08`) or wait — cheap NO opportunities appear sporadically.

**"Daily budget exhausted"**
→ Hit daily limit. Adjust with `--set daily_budget=30`.

**"Import failed"**
→ The market may have already resolved, or the slug is incorrect. The skill skips and continues.

**All candidates have fees**
→ The fee filter is intentional — on a 5¢ NO position (p=0.05) the crypto taker fee runs ~6.6% of cost, eliminating most of the edge on cheap NO. By design. Zero-fee categories (e.g. Geopolitics) bypass this filter.

**"gamma_api.py not found"**
→ Copy `gamma_api.py` from the `polymarket-ai-divergence` skill into this skill's directory, or install both skills together.
