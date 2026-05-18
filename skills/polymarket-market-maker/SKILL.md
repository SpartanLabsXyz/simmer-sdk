---
name: polymarket-market-maker
description: Post two-sided GTC limit orders on Polymarket CLOB. Manages inventory skew, cancel/replace on price drift, and estimates rebate-eligible volume. Based on Akey et al. (2026): market-making reduces loss probability by 35.9 pp — the single strongest edge on Polymarket.
metadata:
  author: Simmer (@simmer_markets)
  version: "1.0.0"
  displayName: Polymarket Market Maker
  difficulty: advanced
  venue: polymarket
  requires_wallet: true
  tier: developer
---

# Polymarket Market Maker

Post two-sided GTC limit orders (bid + ask) on Polymarket CLOB to earn the bid-ask spread as a liquidity provider. This is a **developer-only SDK primitive** — not a retail "set and forget" skill. You will need to monitor inventory and tune spread/skew parameters to your specific markets.

> **Why this matters.** Akey, Grégoire, Harvie & Martineau (2026) studied 1.4M Polymarket users and $20B in volume. Their headline finding: moving from pure taker to pure market-maker reduces the probability of losing money by **35.9 percentage points** — more than 10× larger than any other behavioral predictor (overtrading: 5pp, category concentration: 13.6pp, extreme-price trading: 3pp). This is the single highest-edge behavior on Polymarket.

## How it works

```
Each run (cron or manual):

  For each configured market:
    1. Fetch current mid price (external_price_yes)
    2. Compute bid/ask:
         bid = mid - spread/2  (round to 0.01 tick)
         ask = mid + spread/2  (round to 0.01 tick)
    3. Check inventory: if net_YES_skew > max_skew → pause bidding
                        if net_NO_skew  > max_skew → pause asking
    4. Check drift: if existing order price differs from new quote by > drift_threshold
                    → cancel and repost
    5. Post GTC orders:
         BID: client.trade("yes", amount=Q, price=bid, order_type="GTC")
         ASK: client.trade("no",  amount=Q, price=1-ask, order_type="GTC")
              ^ Synthetic sell: post NO buy at 1-ask_price instead of YES sell
                → earns maker rebates instead of paying taker fees on exits
    6. Log fee-equivalent volume for rebate estimation
```

## Quote structure (synthetic sell on asks)

Polymarket YES and NO tokens are complementary (YES + NO = $1). Instead of posting a sell order on YES (which pays taker fees), this skill posts a buy order on NO at the complementary price:

| Intent | Order posted | Price |
|--------|-------------|-------|
| Buy YES | BUY YES GTC | mid - spread/2 |
| Sell YES (ask) | **BUY NO GTC** | 1 - (mid + spread/2) |

This earns maker rebates on both sides (Polymarket Maker Rebates Program). At a taker fee rate of 2%, the rebate formula is:
```
fee_equivalent = notional_USDC × taker_fee_rate × price × (1-price)
```
The `--status` and `--cancel-all` commands show cumulative fee-equivalent estimates.

## Setup

1. **Install SDK**
   ```bash
   pip install -U 'simmer-sdk>=0.13.0'
   ```

2. **Set API key**
   ```bash
   export SIMMER_API_KEY=sk_live_...
   ```

3. **Import markets to Simmer** (markets must be imported before the skill can quote them)
   ```python
   from simmer_sdk import SimmerClient
   client = SimmerClient(api_key=os.environ["SIMMER_API_KEY"], venue="polymarket")
   result = client.import_market("https://polymarket.com/event/will-btc-hit-100k-by-eoy-2026")
   print(result["market_id"])  # use this ID in MM_MARKETS
   ```

4. **Configure markets**
   ```bash
   export MM_MARKETS=abc123def456,xyz789...   # comma-separated Simmer market IDs
   export MM_SPREAD_PCT=0.04                  # 4% spread (2¢ each side at 50¢ mid)
   export MM_QUOTE_USDC=10                    # $10 USDC per side per market
   ```

5. **Dry run** (default — no orders placed)
   ```bash
   python market_maker.py
   ```

6. **Live trading**
   ```bash
   python market_maker.py --live
   ```

## Parameters

| Env Var | Default | Description |
|---------|---------|-------------|
| `MM_MARKETS` | — | **Required.** Comma-separated Simmer market IDs |
| `MM_SPREAD_PCT` | `0.04` | Spread as fraction of price (0.04 = 4 cents at 50¢); **minimum 0.03** to avoid post-rounding quote collapse |
| `MM_QUOTE_USDC` | `10.0` | USDC per side per market |
| `MM_MAX_SKEW_PCT` | `0.30` | Pause one side when inventory skew exceeds this fraction |
| `MM_DRIFT_THRESHOLD` | `0.02` | Cancel/replace when quote drifts >2¢ from current mid |
| `MM_TAKER_FEE_RATE` | `0.02` | Taker fee rate for rebate estimation (sports: 0.03) |
| `MM_MAX_MARKETS` | `5` | Cap number of markets per run |
| `MM_STATE_FILE` | `~/.simmer/market_maker_state.json` | State persistence (order IDs + rebate log) |

## CLI commands

```bash
python market_maker.py              # Dry run
python market_maker.py --live       # Live trading
python market_maker.py --status     # Show active quotes + inventory
python market_maker.py --cancel-all # Cancel all open market-maker GTC orders
python market_maker.py --config     # Print active config
```

## Cron / automaton mode

Run on a cron cadence (e.g., every 5 minutes) to keep quotes fresh. The skill emits `{"automaton": {...}}` when `AUTOMATON_MANAGED=1` is set.

```bash
# Run every 5 minutes (OpenClaw / cron)
*/5 * * * * AUTOMATON_MANAGED=1 MM_MARKETS=abc123 MM_SPREAD_PCT=0.04 \
  python market_maker.py --live
```

Automaton output fields:
- `markets` — number of markets processed
- `bids_placed` / `asks_placed` — GTC orders submitted this run
- `orders_cancelled` — stale quotes cancelled
- `fee_equiv_usd` — estimated rebate-eligible volume this run
- `cumulative_fee_equiv_usd` — total across all runs

## Market selection guidance

Good candidates for market-making:
- Active binary Polymarket markets with reasonable volume (>$5k/day)
- Prices between 5¢ and 95¢ (near-binary markets have poor risk/reward)
- Sports, crypto, and major political markets eligible for Maker Rebates Program

Poor candidates:
- Near-resolved markets (price < 2¢ or > 98¢) — high adverse selection, max loss is max
- Very low liquidity markets — spreads wide but fills rare; capital sits idle
- neg_risk (multi-outcome) markets — price arithmetic differs; this skill uses binary-only math

## Inventory management

The skill tracks net inventory per market:
```
net_skew = shares_YES - shares_NO
```

When `|net_skew| > max_skew_pct × (quote_usdc / price)`, the over-exposed side stops posting new quotes. This limits directional exposure but does not eliminate it — monitor inventory via `--status`.

## Limitations (v1)

- **Binary markets only.** neg_risk markets have non-complementary token prices; the `1-ask_yes` formula is incorrect for them. The skill will still quote them but the ask leg may misprice.
- **No position close automation.** The skill posts quotes but doesn't close inventory on resolution. Use `client.auto_redeem()` in a separate process or add `--cancel-all` before market resolution.
- **State file is local.** If running across multiple machines, the state file can desync. Each instance manages its own orders independently.

## References

- Research: `simmer-labs/shared-knowledge/research/2026-04-16-polymarket-winners-losers-study.md`
- Synthetic sell: `simmer-labs/shared-knowledge/research/2026-04-14-polymarket-quant-bot-copy-trading.md`
- Risk disclaimer: [DISCLAIMER.md](./DISCLAIMER.md)
