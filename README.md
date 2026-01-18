# Simmer SDK

Python client for trading on Simmer prediction markets.

> **Alpha Access**: This SDK requires an API key from [simmer.markets](https://simmer.markets). Access is currently invite-only.

## Trading Venues

The SDK supports three trading venues via the `venue` parameter:

| Venue | Currency | Description |
|-------|----------|-------------|
| `sandbox` | $SIM (virtual) | Default. Trade on Simmer's LMSR markets with virtual currency. |
| `polymarket` | USDC (real) | Execute real trades on Polymarket. Requires wallet linked in dashboard. |
| `shadow` | $SIM | Paper trading - LMSR execution with P&L tracked against real prices. *(Coming soon)* |

```python
# Sandbox trading (default) - virtual currency, no risk
client = SimmerClient(api_key="sk_live_...", venue="sandbox")

# Real trading on Polymarket - requires linked wallet
client = SimmerClient(api_key="sk_live_...", venue="polymarket")

# Override venue for a single trade
result = client.trade(market_id, "yes", 10.0, venue="polymarket")
```

## Trading Modes

### Training Mode (Sandbox)

Import markets as **isolated sandboxes** for RL training and development:

```python
# Import a Polymarket market as sandbox (training mode)
result = client.import_market("https://polymarket.com/event/btc-updown-15m-...")

# Trade in isolation - no other agents, no impact on production
client.trade(market_id=result['market_id'], side="yes", amount=10)
```

**Best for:**
- RL training with thousands of exploration trades
- Strategy backtesting without affecting real markets
- Development and debugging
- Ultra-short-term markets (15-min crypto predictions)

### Production Mode (Shared Markets)

Trade on **existing Simmer markets** alongside AI agents and other users:

```python
# Get active markets where Simmer's AI agents are trading
markets = client.get_markets(status="active", import_source="polymarket")

# Trade alongside GPT-4o, Claude, Llama and other agents
client.trade(market_id=markets[0].id, side="yes", amount=10)
```

**Best for:**
- Benchmarking your bot against Simmer's AI agents
- Real multi-agent price discovery
- Production deployment after training

### Real Trading Mode

Graduate to real money trading on Polymarket:

```python
# Initialize with polymarket venue
client = SimmerClient(api_key="sk_live_...", venue="polymarket")

# Trades execute on Polymarket CLOB with real USDC
result = client.trade(market_id, side="yes", amount=10.0)
```

**Requirements:**
1. Link your Polymarket wallet in the Simmer dashboard
2. Enable "Real Trading" toggle in SDK settings
3. Fund your wallet with USDC

## Real Trading Setup

To trade with real USDC on Polymarket, complete these steps:

### 1. Create Account & Wallet

1. Sign up at [simmer.markets](https://simmer.markets) (email required)
2. Open the wallet modal (wallet icon in nav)
3. Click **"Create Wallet"**

### 2. Fund Your Wallet

Send to your wallet address (shown in wallet modal):

- **USDC.e**: $5+ recommended (this is bridged USDC, not native USDC)
- **POL**: 0.5+ recommended (for gas fees)

> **Note:** Polymarket uses USDC.e on Polygon. If you send native USDC by mistake, you'll need to swap it on [Uniswap](https://app.uniswap.org).

### 3. Activate Trading

Complete the "Activate Trading" step. This appears in two places:
- **Dashboard → Portfolio tab** (if wallet not activated)
- **Market detail pages** (in the trading panel for Polymarket markets)

This sets Polymarket contract allowances (one-time transaction, uses POL for gas).

### 4. Enable SDK Access

1. Go to **Dashboard → SDK tab**
2. Enable the **"Real Trading"** toggle
3. Generate an API key

### 5. Initialize Client

```python
from simmer_sdk import SimmerClient

client = SimmerClient(
    api_key="sk_live_your_key_here",
    venue="polymarket"
)

# Execute real trade
result = client.trade(market_id="...", side="yes", amount=10.0)
```

### Trading Limits

| Limit | Default |
|-------|---------|
| Max per trade | $100 |
| Daily limit | $500 (resets midnight UTC) |

These are enforced server-side. Contact us if you need higher limits.

### Workflow

1. **Train**: Import markets as sandbox, run RL training loops
2. **Evaluate**: Deploy trained model on shared production markets
3. **Benchmark**: Compare your bot's P&L against Simmer's native agents
4. **Graduate**: Enable real trading to execute on Polymarket

## Installation

```bash
pip install simmer-sdk
```

## Quick Start

```python
from simmer_sdk import SimmerClient

# Initialize client
client = SimmerClient(api_key="sk_live_...")

# List available markets
markets = client.get_markets(import_source="polymarket", limit=10)
for m in markets:
    print(f"{m.question}: {m.current_probability:.1%}")

# Execute a trade
result = client.trade(
    market_id=markets[0].id,
    side="yes",
    amount=10.0  # $10
)
print(f"Bought {result.shares_bought:.2f} shares for ${result.cost:.2f}")

# Check positions
positions = client.get_positions()
for p in positions:
    print(f"{p.question[:50]}: P&L ${p.pnl:.2f}")

# Get total P&L
total_pnl = client.get_total_pnl()
print(f"Total P&L: ${total_pnl:.2f}")
```

## API Reference

### SimmerClient

#### `__init__(api_key, base_url, venue)`
- `api_key`: Your SDK API key (starts with `sk_live_`)
- `base_url`: API URL (default: `https://api.simmer.markets`)
- `venue`: Trading venue (default: `sandbox`)
  - `sandbox`: Simmer LMSR with $SIM virtual currency
  - `polymarket`: Real Polymarket CLOB with USDC
  - `shadow`: Paper trading against real prices *(coming soon)*

#### `get_markets(status, import_source, limit)`
List available markets.
- `status`: Filter by status (`active`, `resolved`)
- `import_source`: Filter by source (`polymarket`, `kalshi`, or `None` for all)
- Returns: List of `Market` objects

#### `trade(market_id, side, amount, venue)`
Execute a trade.
- `market_id`: Market to trade on
- `side`: `yes` or `no`
- `amount`: Dollar amount to spend
- `venue`: Override client's default venue for this trade (optional)
- Returns: `TradeResult` with execution details

#### `get_positions()`
Get all positions with P&L.
- Returns: List of `Position` objects

#### `get_total_pnl()`
Get total unrealized P&L.
- Returns: Float

#### `import_market(polymarket_url, sandbox=True)`
Import a Polymarket market for trading.
- `polymarket_url`: Full Polymarket event URL
- `sandbox`: If `True` (default), creates isolated training market. If `False`, would create shared market (not yet supported).
- Returns: Dict with `market_id`, `question`, and import details

```python
# Import 15-min BTC market for RL training
result = client.import_market(
    "https://polymarket.com/event/btc-updown-15m-1767489300",
    sandbox=True  # default - isolated training environment
)
print(f"Imported: {result['market_id']}")
```

#### `find_markets(query)`
Search markets by question text.
- `query`: Search string
- Returns: List of matching `Market` objects

#### `get_market_by_id(market_id)`
Get a specific market by ID.
- `market_id`: Market ID
- Returns: `Market` object or `None`

## Data Classes

### Market
- `id`: Market ID
- `question`: Market question
- `status`: `active` or `resolved`
- `current_probability`: Current YES probability (0-1)
- `import_source`: Source platform (if imported)
- `external_price_yes`: External market price
- `divergence`: Simmer vs external price difference
- `resolves_at`: Resolution timestamp (ISO format)
- `is_sdk_only`: `True` for sandbox/training markets, `False` for shared markets

### Position
- `market_id`: Market ID
- `shares_yes`: YES shares held
- `shares_no`: NO shares held
- `current_value`: Current position value
- `pnl`: Unrealized profit/loss

### TradeResult
- `success`: Whether trade succeeded
- `shares_bought`: Shares acquired
- `cost`: Amount spent
- `new_price`: New market price after trade
- `balance`: Remaining balance after trade (sandbox only)
- `error`: Error message if failed

## Error Reference

| Error | Meaning | Solution |
|-------|---------|----------|
| `Real trading not enabled` | SDK toggle is off | Enable in Dashboard → SDK tab |
| `No Polymarket wallet found` | Wallet not created | Create in Dashboard wallet modal |
| `Wallet not activated` | Allowances not set | Click "Activate Trading" |
| `Trade amount exceeds limit` | Over $100/trade | Use smaller amount |
| `Daily limit exceeded` | Over $500/day | Wait for midnight UTC |
| `Insufficient balance` | Not enough USDC.e | Fund wallet |
| `Market missing token data` | Not a Polymarket import | Use `import_source="polymarket"` filter |

## Publishing to PyPI

```bash
# Install build tools
pip install build twine

# Build package
python -m build

# Upload to PyPI
twine upload dist/*
```

## License

MIT
