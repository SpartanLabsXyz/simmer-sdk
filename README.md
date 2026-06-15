# Simmer SDK

[![PyPI version](https://badge.fury.io/py/simmer-sdk.svg)](https://pypi.org/project/simmer-sdk/)

Simmer is the leading prediction market interface for AI agents. Autonomous trading agents place trades on venues like Polymarket and Kalshi through a unified API and SDK — with self-custody wallets, safety rails, and smart context.

- **AI-native trading platform** — designed for autonomous agents, with full support for manual trading too. Users install trading skills and let their agents trade autonomously.
- **$SIM simulated trading** — paper-trade with virtual currency before risking real funds.
- **Multi-venue** — trade Polymarket and Kalshi through one unified API.

## Installation

```bash
pip install simmer-sdk
```

Get your API key from [simmer.markets/dashboard](https://simmer.markets/dashboard).

## Quick Start

### OpenClaw Skill Pattern (recommended)

Most Simmer users run trading skills inside [OpenClaw](https://openclaw.ai). The standard pattern uses a lazy singleton client and reads config from environment variables:

```python
import os
from simmer_sdk import SimmerClient

SKILL_SLUG = "my-skill-slug"   # Must match your ClawHub slug
TRADE_SOURCE = f"sdk:{SKILL_SLUG}"

_client = None
def get_client():
    global _client
    if _client is None:
        venue = os.environ.get("TRADING_VENUE", "sim")
        _client = SimmerClient(api_key=os.environ["SIMMER_API_KEY"], venue=venue)
    return _client

def run(live: bool = False):
    client = get_client()

    # Find markets. Unfiltered browse is windowed to the newest ~1,000 active
    # markets — filter with sort="volume", q="...", or tags="..." to reach the rest.
    markets = client.get_markets(status="active", sort="volume", limit=20)

    # Get trading context (safeguards, slippage, conflict detection)
    ctx = client.get_market_context(markets[0].id)

    # Trade — always tag source and skill_slug
    if not ctx.conflict and ctx.recommended_action != "hold":
        result = client.trade(
            market_id=markets[0].id,
            side="yes",
            amount=10.0,
            dry_run=not live,
            source=TRADE_SOURCE,
            skill_slug=SKILL_SLUG,
            reasoning="Signal detected — buying YES"
        )
        print(f"{'DRY RUN: ' if not live else ''}Bought {result.shares_bought:.2f} shares")

if __name__ == "__main__":
    import sys
    run(live="--live" in sys.argv)
```

Set environment variables:
```bash
export SIMMER_API_KEY=sk_live_...
export TRADING_VENUE=sim            # sim | polymarket | kalshi
export WALLET_PRIVATE_KEY=0x...    # Required for Polymarket self-custody
```

> **Default to dry-run.** Skills should require `--live` to execute real trades. Paper-trade with `$SIM` until your edge is consistent, then graduate to real money.

### Raw SDK

For developers building custom integrations:

```python
from simmer_sdk import SimmerClient

client = SimmerClient(api_key="sk_live_...")

# Browse markets (unfiltered browse is windowed to the newest ~1,000 active
# markets — use sort="volume", q="...", or tags="..." for discovery)
markets = client.get_markets(sort="volume", limit=10)
for m in markets:
    print(f"{m.question}: {m.current_probability:.1%}")

# Trade with $SIM (virtual currency)
result = client.trade(market_id=markets[0].id, side="yes", amount=10.0)
print(f"Bought {result.shares_bought:.2f} shares for ${result.cost:.2f}")

# Check P&L
for p in client.get_positions():
    print(f"{p.question[:50]}: P&L ${p.pnl:.2f}")
```

## Trading Venues

| Venue | Currency | Description |
|-------|----------|-------------|
| `sim` | $SIM (virtual) | Default. Paper trading on Simmer's LMSR markets. |
| `polymarket` | USDC.e (real) | Real trades on Polymarket (Polygon). Requires `WALLET_PRIVATE_KEY`. |
| `kalshi` | USDC (real) | Real trades on Kalshi. Requires Pro plan. |

```python
# Paper trading (default)
client = SimmerClient(api_key="sk_live_...", venue="sim")

# Real trading on Polymarket
client = SimmerClient(api_key="sk_live_...", venue="polymarket")

# Override venue for a single trade
client.trade(market_id, side="yes", amount=10.0, venue="polymarket")
```

`TRADING_VENUE` environment variable is read at client init — OpenClaw skills use this to select venue at startup without code changes.

> **Spread caveat:** $SIM fills instantly (AMM, no spread). Real venues have orderbook spreads of 2–5%. Target edges >5% in $SIM before graduating to real money.

### Paper trading on real venues

Pass `live=False` to simulate trades with real market prices — no wallet or USDC required. For Polymarket, fills model the CLOB bid-ask spread for realistic P&L. Resolved markets auto-settle (winning shares pay $1, losers $0).

```python
client = SimmerClient(
    api_key="sk_live_...",
    venue="polymarket",
    live=False,                # Simulate fills, no real money
    starting_balance=10_000.0  # Virtual capital (default: 10,000)
)

result = client.trade(market_id=markets[0].id, side="yes", amount=50.0,
                      reasoning="Testing strategy")
print(f"Filled {result.shares_bought:.2f} shares (simulated)")

# Portfolio summary
summary = client.get_paper_summary()
print(f"Balance: ${summary['balance']:.2f}, P&L: ${summary['total_pnl']:.2f}")
```

**Graduation path:** `sim` (instant fills, no spread) → `polymarket` + `live=False` (real prices, spread modeled) → `polymarket` live (real USDC).

## Backtesting

The three modes above are all *live-forward*. To test a strategy on **historical** data before risking capital, backtest the skill bundle:

```bash
pip install 'simmer-sdk[backtest]'

# Try it offline — bundled 10-market demo slice, no data download:
simmer backtest --demo

# Backtest your own skill over a window — the tape is fetched + cached for you:
simmer backtest ./my-skill --entrypoint run.py \
    --t0 2026-03-01 --t1 2026-03-08 --cadence 12h --out report.json

# ...or by duration, and with your own local slice (BYO):
simmer backtest ./my-skill --entrypoint run.py --window 30d
simmer backtest ./my-skill --entrypoint run.py --tape ./slice --t0 2026-03-01 --t1 2026-03-08
```

The engine replays your **unmodified** skill against a frozen, look-ahead-safe
replay server (one subprocess per tick) and reports pnl, hit rate, max drawdown,
trades, baselines (buy-and-hold-YES / random), realism gaps, and a reproducible
`config_hash`. Programmatic equivalent:

```python
from simmer_sdk.backtest import run_backtest

# tape omitted => the window slice is fetched from the tape service and cached.
report = run_backtest("./my-skill", entrypoint="run.py",
                      t0="2026-03-01", t1="2026-03-08", cadence="12h")
print(report["summary"]["pnl"], report["summary"]["hit_rate"])
```

> Backtests use trade-tape prices (no orderbook), so they model decision quality,
> not execution realism — every report lists its `realism_gaps`. The window slice
> is fetched from Simmer's tape service and cached under `~/.simmer/tapes/`; pass
> `--tape <dir>` to use your own. Data coverage currently ends ~2026-05-05.

## Key Methods

| Method | Description |
|--------|-------------|
| `get_markets()` | List markets (filter by status, source, venue, tags, keyword) |
| `trade()` | Buy or sell shares |
| `get_positions()` | All positions with P&L |
| `get_held_markets()` | Map of market_id → source tags for held positions |
| `check_conflict()` | Check if another skill holds a position on a market |
| `get_open_orders()` | Open GTC/GTD orders on the CLOB |
| `maker_rewards_status(market_id)` | Polymarket liquidity-rewards config: max spread, daily pool, eligibility |
| `get_portfolio(venue="all")` | Portfolio summary with per-venue buckets (sim/polymarket/kalshi/total) |
| `get_market_context(market_id, venue="all")` | Per-venue positions + trading safeguards |
| `get_trades(venue="all")` | Trade history merged across venues, each row tagged with venue |
| `get_price_history()` | Price history for trend detection |
| `import_market()` | Import a Polymarket market by URL |
| `import_kalshi_market()` | Import a Kalshi market by URL |
| `list_importable_markets()` | Discover markets available to import |
| `check_market_exists()` | Check if a market is already on Simmer (no quota cost) |
| `set_monitor()` | Set stop-loss / take-profit on a position |
| `cancel_order()` | Cancel a single open order by ID |
| `cancel_market_orders()` | Cancel all open orders on a market (optional side filter) |
| `cancel_all_orders()` | Cancel all open orders across all markets |
| `create_alert()` | Price alerts with optional webhook |
| `register_webhook()` | Push notifications for trades, resolutions, price moves |
| `redeem()` | Redeem a specific winning Polymarket position |
| `auto_redeem()` | Scan all positions and redeem any winning ones automatically |
| `get_paper_summary()` | Paper mode portfolio summary (balance, P&L, positions) |
| `get_settings()` / `update_settings()` | Configure trade limits and notifications |
| `link_wallet()` | Link external EVM wallet for Polymarket |
| `set_approvals()` | Set Polymarket token approvals |
| `activate_polymarket_dw(agent_id=None)` | Set Polymarket Deposit Wallet on-chain CLOB approvals — user-primary (no arg) or per-agent (`agent_id=...`). See note. |
| `troubleshoot()` | Look up any error and get a fix (no auth required) |

> **Per-agent wallets (Elite tier):** activating a per-agent (Elite dedicated) wallet takes **two** calls, approvals first: `activate_polymarket_dw(agent_id=...)` sets the deposit wallet's on-chain CLOB approvals, then `update_agent_wallet_creds(...)` caches the CLOB creds. OWS callers use `update_agent_wallet_creds(ows_wallet_name="...")`; raw-key callers with `WALLET_PRIVATE_KEY` use `update_agent_wallet_creds(agent_id="...")`. **Both approvals and cached creds are required before trading** — caching creds alone does not set on-chain allowances, so trades fail at the relayer with "insufficient allowance". See the `simmer-wallet-setup` skill for the full flow. (`set_approvals()` is the user-primary EOA path and is a no-op for per-agent deposit wallets.)

**Tip — don't pre-round prices.** simmer-sdk ≥ 0.17.1 automatically rounds the price to each Polymarket market's tick grid. Pass your raw computed price to `client.trade(..., price=p)` and the SDK handles the rest. Pre-rounding with a hardcoded tick (e.g. `round(price, 3)`) will silently produce wrong values for markets at different tick sizes.

**Error handling:** All SDK 4xx responses include a `fix` field with actionable instructions when the error matches a known pattern. You can also call `POST /api/sdk/troubleshoot` with `{"error_text": "..."}` to look up any error.

Full API reference with parameters, examples, and error codes: **[simmer.markets/docs.md](https://simmer.markets/docs.md)**

## Skill Builder Utilities

The SDK ships two helper modules for skill authors. Prefer these over rolling your own — they encode patterns from top traders and external research.

### Position sizing — `simmer_sdk.sizing`

Kelly Criterion + Expected Value sizing for binary prediction markets. Default is fractional Kelly (0.25x) with an EV gate, so trades below your edge threshold return `0.0` and the skill can simply skip them.

```python
from simmer_sdk import SimmerClient
from simmer_sdk.sizing import size_position

client = SimmerClient()
bankroll = client.get_portfolio()["available_balance"]

amount = size_position(
    p_win=0.70,         # your model's probability
    market_price=0.55,  # current YES price
    bankroll=bankroll,
    min_ev=0.03,        # skip trades with edge < 3%
)
if amount > 0:
    client.trade(market_id=..., side="BUY", outcome="YES",
                 amount=amount, reasoning="Kelly: 70% vs 55%, +15% edge")
```

| Function | Purpose |
|----------|---------|
| `size_position(p_win, market_price, bankroll, method=, kelly_multiplier=, min_ev=, max_fraction=)` | Returns dollar amount to trade. `0.0` when edge ≤ `min_ev`, Kelly is negative, or inputs are invalid. |
| `kelly_fraction(p_win, market_price)` | Raw Kelly fraction `(p - c) / (1 - c)`. |
| `expected_value(p_win, market_price)` | Edge per share (`p_win - market_price`). |
| `SIZING_CONFIG_SCHEMA` | Drop-in `CONFIG_SCHEMA` fragment exposing `SIMMER_POSITION_SIZING`, `SIMMER_KELLY_MULTIPLIER`, `SIMMER_MIN_EV` env vars. |

Methods: `"fractional_kelly"` (default, multiplier 0.25), `"kelly"` (full, aggressive), `"fixed"` (uses `kelly_multiplier` as a flat fraction). For NO bets pass `p_win=1-p_yes` and `market_price=1-yes_price`.

## Auto-Redeem

When a Polymarket market resolves and your side wins, the CTF tokens in your wallet must be redeemed to claim the USDC.e payout. Auto-redeem handles this automatically each cycle.

```python
# Call at the start of each cycle to claim any pending winnings
results = client.auto_redeem()
for r in results:
    if r["success"]:
        print(f"Redeemed {r['market_id']} ({r['side']}): {r['tx_hash']}")
```

- Fetches positions where `redeemable: true` and `redeemable_side` is set (Polymarket only)
- For self-custody wallets (`WALLET_PRIVATE_KEY`): signs and broadcasts on-chain
- For managed wallets: server handles signing, no local key needed
- Never raises — safe to call every cycle

Auto-redeem can be toggled per-agent from the Simmer dashboard.

## Skills

Pre-built trading strategies are published on [ClawHub](https://clawhub.ai) and listed in the Simmer registry. Browse and install at **[simmer.markets/skills](https://simmer.markets/skills)**.

```bash
# Install a skill via ClawHub CLI
clawhub install polymarket-weather-trader
```

Skills in this repo (`skills/`) are the official Simmer-maintained strategies. See [docs.simmer.markets/skills/building](https://docs.simmer.markets/skills/building) for the full guide to building, remixing, and publishing your own.

## Resources

| | |
|--|--|
| **Platform** | [simmer.markets](https://simmer.markets) |
| **API Reference** | [docs.simmer.markets](https://docs.simmer.markets) |
| **Onboarding Guide** | [simmer.markets/skill.md](https://simmer.markets/skill.md) |
| **Skills Registry** | [docs.simmer.markets/skills](https://docs.simmer.markets/skills/overview) |
| **ClawHub** | [clawhub.ai](https://clawhub.ai) |
| **MCP Server** | `pip install simmer-mcp` — docs + error troubleshooting as MCP resources ([PyPI](https://pypi.org/project/simmer-mcp/)) |
| **Telegram** | [t.me/+m7sN0OLM_780M2Fl](https://t.me/+m7sN0OLM_780M2Fl) |

## Contributing

SDK improvements and bug fixes are welcome. If you've hit an edge case with `SimmerClient` or have a useful addition, open a PR.

- **Skills** belong on [ClawHub](https://clawhub.ai), not this repo — see [docs.simmer.markets/skills/building](https://docs.simmer.markets/skills/building)
- **API bugs or feature requests** → open an issue first
- **AI-assisted PRs welcome** — just note it in the PR description
- Keep PRs focused on one thing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the full guide.

## License

MIT
