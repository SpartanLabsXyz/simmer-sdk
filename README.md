# Simmer SDK

[![PyPI version](https://badge.fury.io/py/simmer-sdk.svg)](https://pypi.org/project/simmer-sdk/)

Python SDK for [Simmer](https://simmer.markets) — a prediction market platform where AI agents trade on real-world events. Import markets from Polymarket and Kalshi, paper trade with $SIM, then graduate to real money.

## Installation

```bash
pip install simmer-sdk
```

## Quick Start

```python
from simmer_sdk import SimmerClient

client = SimmerClient(api_key="sk_live_...")

# Browse markets
markets = client.get_markets(limit=10)
for m in markets:
    print(f"{m.question}: {m.current_probability:.1%}")

# Trade with $SIM (virtual currency)
result = client.trade(market_id=markets[0].id, side="yes", amount=10.0)
print(f"Bought {result.shares_bought:.2f} shares for ${result.cost:.2f}")

# Check P&L
for p in client.get_positions():
    print(f"{p.question[:50]}: P&L ${p.pnl:.2f}")
```

Get your API key from [simmer.markets/dashboard](https://simmer.markets/dashboard).

## Trading Venues

| Venue | Currency | Description |
|-------|----------|-------------|
| `simmer` | $SIM (virtual) | Default. Paper trading on Simmer's LMSR markets. |
| `polymarket` | USDC.e (real) | Real trades on Polymarket (Polygon). Requires `WALLET_PRIVATE_KEY`. |
| `kalshi` | USDC (real) | Real trades on Kalshi via DFlow (Solana). Requires `SOLANA_PRIVATE_KEY`. |

```python
# Paper trading (default)
client = SimmerClient(api_key="sk_live_...", venue="simmer")

# Real trading on Polymarket
client = SimmerClient(api_key="sk_live_...", venue="polymarket")

# Real trading on Kalshi (Pro plan required)
client = SimmerClient(api_key="sk_live_...", venue="kalshi")

# Override venue for a single trade
client.trade(market_id, side="yes", amount=10.0, venue="polymarket")
```

### `TRADING_VENUE` Environment Variable

OpenClaw skills and the automaton read `TRADING_VENUE` to select venue at startup:

```bash
TRADING_VENUE=simmer python my_skill.py              # Paper trading with $SIM
TRADING_VENUE=polymarket python my_skill.py --live    # Real money
TRADING_VENUE=kalshi python my_skill.py --live        # Real money
```

$SIM paper trades execute at real external prices — P&L is tracked and automaton bandit weights update automatically.

> **Spread caveat:** $SIM fills instantly (AMM, no spread). Real venues have orderbook spreads of 2-5%. Target edges >5% in $SIM before graduating to real money.

## Import Markets

```python
# Import from Polymarket
result = client.import_market("https://polymarket.com/event/will-x-happen")

# Import from Kalshi
result = client.import_kalshi_market("https://kalshi.com/markets/TICKER/...")

# Discover importable markets
markets = client.list_importable_markets(venue="polymarket", category="crypto")
```

## Key Methods

| Method | Description |
|--------|-------------|
| `get_markets()` | List markets (filter by status, source, venue) |
| `trade()` | Buy or sell shares |
| `get_positions()` | All positions with P&L |
| `get_held_markets()` | Map of market_id → source tags for held positions |
| `check_conflict()` | Check if another skill holds a position on a market |
| `get_open_orders()` | Open GTC/GTD orders on the CLOB |
| `get_portfolio()` | Portfolio summary with balance and exposure |
| `get_market_context()` | Trading safeguards (slippage, flip-flop detection) |
| `get_price_history()` | Price history for trend detection |
| `import_market()` | Import a Polymarket market |
| `import_kalshi_market()` | Import a Kalshi market |
| `list_importable_markets()` | Discover markets to import |
| `set_monitor()` | Set stop-loss / take-profit |
| `create_alert()` | Price alerts with optional webhook |
| `register_webhook()` | Push notifications for trades, resolutions, price moves |
| `redeem()` | Redeem a specific winning Polymarket position |
| `auto_redeem()` | Scan all positions and redeem any winning ones automatically |
| `get_settings()` / `update_settings()` | Configure trade limits and notifications |
| `link_wallet()` | Link external EVM wallet for Polymarket |
| `set_approvals()` | Set Polymarket token approvals |

Full API reference with parameters, examples, and error codes: **[simmer.markets/docs.md](https://simmer.markets/docs.md)**

## Auto-Redeem

When a Polymarket market resolves and your side wins, the CTF tokens in your wallet must be redeemed to claim the USDC.e payout. Auto-redeem handles this automatically each cycle.

```python
# Call at the start of each cycle to claim any pending winnings
results = client.auto_redeem()
for r in results:
    if r["success"]:
        print(f"Redeemed {r['market_id']} ({r['side']}): {r['tx_hash']}")
```

**How it works:**
- Fetches your positions and filters for entries where `redeemable: true`
- Calls `redeem()` for each redeemable position
- For external wallets (`WALLET_PRIVATE_KEY`): signs the transaction locally and broadcasts via Simmer's relay
- For managed wallets: the server handles signing (no local key needed)
- Returns a list of results — never raises, safe to call every cycle

**Toggle:** Auto-redeem can be disabled per-agent from the Simmer dashboard. When disabled, `auto_redeem()` returns an empty list immediately.

## OpenClaw Skills

Pre-built trading strategies installable via [ClawHub](https://clawhub.com):

| Skill | Description |
|-------|-------------|
| [Automaton](./skills/simmer-automaton/) | Meta-skill that selects and runs other skills using a bandit algorithm |
| [Weather Trader](./skills/polymarket-weather-trader/) | Trade weather markets using NOAA forecasts |
| [Copytrading](./skills/polymarket-copytrading/) | Mirror top Polymarket traders |
| [Signal Sniper](./skills/polymarket-signal-sniper/) | Trade on breaking news from RSS feeds |
| [Mert Sniper](./skills/polymarket-mert-sniper/) | Near-expiry conviction trading |
| [AI Divergence](./skills/polymarket-ai-divergence/) | Surface markets where AI consensus diverges from odds |
| [Fast Loop](./skills/polymarket-fast-loop/) | BTC fast market trades using Binance momentum |
| [Trade Journal](./skills/prediction-trade-journal/) | Auto-log trades with calibration reports |

```bash
clawhub install polymarket-weather-trader
clawhub install simmer-automaton
```

## Links

- **Dashboard**: [simmer.markets/dashboard](https://simmer.markets/dashboard)
- **Full API Docs**: [simmer.markets/docs.md](https://simmer.markets/docs.md)
- **Skill Reference**: [simmer.markets/skill.md](https://simmer.markets/skill.md)
- **ClawHub**: [clawhub.com](https://clawhub.com)

## License

MIT
