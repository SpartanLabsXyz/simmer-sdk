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

    # Find markets
    markets = client.get_markets(status="active", limit=20)

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

## Key Methods

| Method | Description |
|--------|-------------|
| `get_markets()` | List markets (filter by status, source, venue, tags, keyword) |
| `trade()` | Buy or sell shares |
| `get_positions()` | All positions with P&L |
| `get_held_markets()` | Map of market_id → source tags for held positions |
| `check_conflict()` | Check if another skill holds a position on a market |
| `get_open_orders()` | Open GTC/GTD orders on the CLOB |
| `get_portfolio()` | Portfolio summary with balance and exposure |
| `get_market_context()` | Trading safeguards (slippage, flip-flop detection, conflict) |
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
| `get_settings()` / `update_settings()` | Configure trade limits and notifications |
| `link_wallet()` | Link external EVM wallet for Polymarket |
| `set_approvals()` | Set Polymarket token approvals |
| `troubleshoot()` | Look up any error and get a fix (no auth required) |

**Error handling:** All SDK 4xx responses include a `fix` field with actionable instructions when the error matches a known pattern. You can also call `POST /api/sdk/troubleshoot` with `{"error_text": "..."}` to look up any error.

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

Skills in this repo (`skills/`) are the official Simmer-maintained strategies. See [simmer.markets/skillregistry.md](https://simmer.markets/skillregistry.md) for the full guide to building, remixing, and publishing your own.

## Resources

| | |
|--|--|
| **Platform** | [simmer.markets](https://simmer.markets) |
| **API Reference** | [simmer.markets/docs.md](https://simmer.markets/docs.md) |
| **Onboarding Guide** | [simmer.markets/skill.md](https://simmer.markets/skill.md) |
| **Skills Registry** | [simmer.markets/skillregistry.md](https://simmer.markets/skillregistry.md) |
| **ClawHub** | [clawhub.ai](https://clawhub.ai) |
| **MCP Server** | `pip install simmer-mcp` — docs + error troubleshooting as MCP resources ([PyPI](https://pypi.org/project/simmer-mcp/)) |
| **Telegram** | [t.me/+m7sN0OLM_780M2Fl](https://t.me/+m7sN0OLM_780M2Fl) |

## Contributing

SDK improvements and bug fixes are welcome. If you've hit an edge case with `SimmerClient` or have a useful addition, open a PR.

- **Skills** belong on [ClawHub](https://clawhub.ai), not this repo — see [skillregistry.md](https://simmer.markets/skillregistry.md)
- **API bugs or feature requests** → open an issue first
- **AI-assisted PRs welcome** — just note it in the PR description
- Keep PRs focused on one thing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the full guide.

## License

MIT
