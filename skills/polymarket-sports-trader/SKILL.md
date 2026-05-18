---
name: polymarket-sports-trader
description: 'Autonomous Polymarket sports trader. Scans NBA / NFL / soccer / MLB / tennis markets, asks an LLM for a fair-value YES probability, trades on divergence. Template-tier skill — generic LLM-driven signal, no validated edge. Use when user wants to trade sports prediction markets autonomously, set up sports-edge bot, or experiment with LLM-driven Polymarket strategies.'
metadata:
  author: Simmer (@simmer_markets)
  version: "0.1.0"
  displayName: Polymarket Sports Trader
  difficulty: advanced
  tier: template
---
# Polymarket Sports Trader

> 🚨 **Framework, not a production trading system.** Read [DISCLAIMER.md](./DISCLAIMER.md) before connecting to a wallet with real funds.

Trade Polymarket sports markets (NBA, NFL, soccer, MLB, tennis) using a generic LLM-driven divergence signal: at each scan the skill asks your chosen LLM for a fair-value YES probability, compares to the live market price, and trades when the gap exceeds a configurable threshold.

## When to Use This Skill

Use this skill when the user wants to:

- Run an autonomous sports-trading agent on Polymarket
- Scan NBA / NFL / soccer / MLB / tennis markets for LLM-derived divergence opportunities
- Wire an OpenRouter / Anthropic / OpenAI key to a Polymarket trading bot
- Experiment with LLM-vs-market calibration on prediction markets

This is a **Template-tier capability skill**: it gives your agent the ability to trade sports markets autonomously, but the default signal — "ask an LLM for a fair value" — is not backtested and makes no edge claim. Pair with conservative sizing or use as a starting point for your own signal.

## How the Strategy Works

Each scan cycle the skill:

1. **Discovers sports markets** — pulls Polymarket sports markets via the Simmer SDK (`category='sports'`), filtered by volume.
2. **Requests an LLM fair value** — sends each market's question + price + resolution time to your LLM (OpenAI-compatible API, OpenRouter by default), asks for `fair_yes` in 0–1 with a confidence rating.
3. **Computes edge** — `edge = fair_yes − market_yes_price`.
4. **Trades on divergence** — if `|edge| ≥ divergence_min` (default 8%), buys the side the model favors at your configured position size.
5. **Tags trades** with `source: "sdk:sports-trader"` so the Simmer portfolio splits P&L by skill.

### Safeguards

- Extreme prices (default: <5% or >95%) are skipped — most "edge" at those levels is noise.
- Markets with no LLM signal (timeout, parse failure, missing key) are skipped, not faked.
- `max_trades_per_run` caps how many fills a single cycle can produce.
- LLM cost is bounded by `max_markets_per_run` × per-call cost.

### Edge thesis (honest version)

LLMs on public market context generally don't beat tight in-game odds. The plausible edges this template can exploit are:

- **Pre-game mispricing** between the announcement window and tip-off, when news (injury, line movement, lineup) leaks unevenly.
- **Long-tail player-prop calibration**, where the public crowd is thinner and Bayesian priors matter.

Validated tier (with backtest evidence) is on the roadmap — track it in the Simmer skill catalog.

## Setup Flow

When user asks to install or configure this skill:

1. **Install the Simmer SDK**
   ```bash
   pip install simmer-sdk
   ```

2. **Ask for Simmer API key**
   - From [simmer.markets/dashboard](https://simmer.markets/dashboard) → SDK tab
   - Set as `SIMMER_API_KEY`

3. **Ask for wallet private key** (external-wallet only)
   - Polymarket wallet private key (the wallet that holds pUSD / USDC.e)
   - Set as `WALLET_PRIVATE_KEY`
   - The SDK signs orders automatically when this is set; no manual signing code needed.
   - Managed wallets do NOT need this — the server signs.

4. **Ask for LLM API key**
   - OpenRouter is the default (`https://openrouter.ai/api/v1`), works with `anthropic/claude-haiku-4.5`. Cheap and adequate.
   - Anthropic direct: set `llm_base_url=https://api.anthropic.com/v1` and an `anthropic/claude-haiku-4-5` model id (varies by provider).
   - OpenAI direct: set `llm_base_url=https://api.openai.com/v1` and `gpt-4.1-mini` (or similar).
   - Set the key as `LLM_API_KEY`. `OPENROUTER_API_KEY` and `OPENAI_API_KEY` are also recognized.

5. **Confirm tunables** (or accept defaults)
   - Max position USD (default $5)
   - Divergence threshold (default 8 percentage points)
   - Max trades per run (default 4)
   - Auto-import (default false — when true, the skill imports top sports markets that aren't on Simmer yet, consuming your daily import quota)

6. **Set up cron** (disabled by default — user must explicitly schedule)

## Configuration

| Setting              | Env Variable                      | Config Key             | Default                                  | Description |
|----------------------|-----------------------------------|------------------------|------------------------------------------|-------------|
| Min market volume    | `SIMMER_SPORTS_MIN_VOLUME`        | `min_volume_usd`       | 25000                                    | Skip sports markets with 24h volume below this. |
| Max markets/run      | `SIMMER_SPORTS_MAX_MARKETS`       | `max_markets_per_run`  | 8                                        | Caps LLM calls per cycle. |
| Divergence threshold | `SIMMER_SPORTS_DIVERGENCE_MIN`    | `divergence_min`       | 0.08                                     | Required `|fair − market|` to trigger a trade. |
| Max position USD     | `SIMMER_SPORTS_MAX_POSITION`      | `max_position_usd`     | 5.00                                     | USD per trade. |
| Min position USD     | `SIMMER_SPORTS_MIN_POSITION`      | `min_position_usd`     | 2.00                                     | Floor when using smart sizing. |
| Sizing pct           | `SIMMER_SPORTS_SIZING_PCT`        | `sizing_pct`           | 0.05                                     | % of USDC balance when `--smart-sizing`. |
| Max trades/run       | `SIMMER_SPORTS_MAX_TRADES`        | `max_trades_per_run`   | 4                                        | Hard cap on fills per cycle. |
| Extreme price floor  | `SIMMER_SPORTS_PRICE_FLOOR`       | `extreme_price_floor`  | 0.05                                     | Skip if YES price below this. |
| Extreme price ceil   | `SIMMER_SPORTS_PRICE_CEIL`        | `extreme_price_ceil`   | 0.95                                     | Skip if YES price above this. |
| Slippage cap         | `SIMMER_SPORTS_SLIPPAGE_MAX`      | `slippage_max_pct`     | 0.05                                     | Reserved for future fill-quality gate. |
| Auto-import          | `SIMMER_SPORTS_AUTO_IMPORT`       | `auto_import`          | false                                    | If true, import sports markets not yet on Simmer. |
| Order type           | `SIMMER_SPORTS_ORDER_TYPE`        | `order_type`           | GTC                                      | GTC (good-til-cancelled) or FAK (fill-and-kill). |
| LLM base URL         | `SIMMER_SPORTS_LLM_BASE_URL`      | `llm_base_url`         | `https://openrouter.ai/api/v1`           | OpenAI-compatible chat endpoint. |
| LLM model            | `SIMMER_SPORTS_LLM_MODEL`         | `llm_model`            | `anthropic/claude-haiku-4.5`             | Model id (provider-specific). |
| LLM timeout (s)      | `SIMMER_SPORTS_LLM_TIMEOUT`       | `llm_timeout_secs`     | 30                                       | Per-call HTTP timeout. |

Config priority: `config.json` > env vars > defaults.

## Running the Skill

```bash
# Dry run — scan, show signals, no trades
python sports_trader.py

# Execute real trades
python sports_trader.py --live

# Smart sizing (% of balance, capped at max_position_usd)
python sports_trader.py --live --smart-sizing

# Show open sports positions
python sports_trader.py --positions

# Show config
python sports_trader.py --config

# Update config
python sports_trader.py --set divergence_min=0.10
python sports_trader.py --set auto_import=true

# Quiet mode (only print trades + errors — good for cron)
python sports_trader.py --live --quiet
```

## Source Tagging

All trades are tagged `source: "sdk:sports-trader"`. This means:

- Portfolio splits P&L by skill so you can see whether sports trading is +EV for you specifically.
- Other skills won't sell your sports positions.

## Troubleshooting

**"No sports markets to evaluate"**
- Simmer hasn't imported any active sports markets yet. Run `python sports_trader.py --set auto_import=true` to let the skill import what's needed (consumes daily import quota — 10/day free, 50/day Pro).

**"llm signal unavailable" on every market**
- `LLM_API_KEY` is not set, or the provider rejected the request. Verify the key works with a direct `curl` to your `llm_base_url`. Many OpenRouter models require an account funding step before they accept traffic.

**"External wallet requires a pre-signed order"**
- `WALLET_PRIVATE_KEY` is not set. The SDK signs orders automatically when this env var is present — no manual signing code needed.
- Fix: `export WALLET_PRIVATE_KEY=0x<your-polymarket-wallet-private-key>`.

**"Balance shows $0 but I have funds on Polygon"**
- Polymarket V2 uses **pUSD** (1:1 backed by USDC.e). If your wallet holds USDC.e or native USDC, migrate at [simmer.markets/dashboard](https://simmer.markets/dashboard) (one click, ~30 s). Full guide: [docs.simmer.markets/v2-migration](https://docs.simmer.markets/v2-migration).

**"API key invalid"**
- Get a new Simmer key from [simmer.markets/dashboard](https://simmer.markets/dashboard) → SDK tab.
