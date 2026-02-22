# Simmer SDK API Reference

Condensed reference for generating skills that use `SimmerClient`.

## Installation

```bash
pip install simmer-sdk
```

## Client Setup

```python
from simmer_sdk import SimmerClient

client = SimmerClient(
    api_key="sk_live_...",   # Required: from SIMMER_API_KEY env var
    venue="polymarket",       # "simmer" (virtual $SIM), "polymarket" (real USDC), "kalshi" (real USD)
    live=True,                # False = paper mode (simulated trades at real prices)
)
```

The `venue` param sets default trading venue. Can be overridden per-trade.

## Core Methods

### Markets

```python
# List active markets
markets = client.get_markets(status="active", limit=20)
# Returns List[Market] dataclass objects

# Search by keyword
markets = client.find_markets("bitcoin")

# Get single market
market = client.get_market_by_id("uuid")
```

**Note:** `get_markets()` accepts `status`, `import_source`, and `limit` only. For keyword search, use `find_markets(query)`. The REST API supports `q=` param on `GET /api/sdk/markets` but the SDK `get_markets()` method does not pass it through.

**REST API market params** (via `client._request("GET", "/api/sdk/markets", params=...)`):
`status`, `tags`, `q`, `venue`, `sort` (`volume`, `opportunity`), `limit`, `ids`, `max_hours_to_resolution`.

### Market Fields (Market dataclass)

```python
market.id                  # UUID
market.question            # "Will BTC hit $100k?"
market.status              # "active", "resolved"
market.current_probability # YES price 0.0-1.0
market.external_price_yes  # Polymarket/Kalshi price (if imported)
market.divergence          # Simmer AI price - external price
market.volume_24h          # 24h trading volume
market.resolves_at         # Resolution timestamp
market.tags                # List of tags
market.url                 # Market URL (always use this, don't construct)
market.is_paid             # True if market charges taker fees (typically 10%)
market.polymarket_token_id # For CLOB queries
```

### Trading

```python
# Buy
result = client.trade(
    market_id="uuid",
    side="yes",              # "yes" or "no"
    amount=10.0,             # USD to spend (required for buys)
    source="sdk:my-skill",   # Tag for tracking (REQUIRED in generated skills)
    reasoning="My thesis",   # Displayed publicly, builds reputation
)

# Sell
result = client.trade(
    market_id="uuid",
    side="yes",
    action="sell",
    shares=10.5,             # Number of shares to sell (required for sells)
    source="sdk:my-skill",
)
```

**TradeResult fields:**
```python
result.success        # bool
result.trade_id       # UUID string
result.shares_bought  # float (shares acquired)
result.cost           # float (USD spent)
result.error          # string (if failed)
result.simulated      # bool (True = paper trade)
```

**Before selling:** Check `status == "active"` (resolved markets can't be sold — redeem instead). Check shares >= 5 (Polymarket minimum). Always fetch fresh positions before selling.

**Auto risk monitors:** Every buy automatically gets a stop-loss and take-profit — server-side, no skill code needed. The server checks prices each oracle cycle and exits autonomously. Defaults are configurable per-position via `POST /api/sdk/positions/{market_id}/monitor` or globally via `PATCH /api/sdk/user/settings`. Only implement manual `execute_sell()` if the skill has custom exit logic (e.g. signal reversal, threshold-based exits). Most skills can rely on the auto monitors and skip sell logic entirely.

### Positions

```python
positions = client.get_positions()  # Returns List[Position] dataclass
# Convert to dicts:
from dataclasses import asdict
pos_dicts = [asdict(p) for p in positions]
```

**Position fields:**
```python
pos.market_id, pos.question, pos.shares_yes, pos.shares_no
pos.current_price    # YES price 0-1
pos.current_value    # Current value in USD
pos.cost_basis       # Total cost paid
pos.avg_cost         # Average entry price
pos.pnl              # Profit/loss
pos.venue            # "simmer" or "polymarket"
pos.currency         # "$SIM" or "USDC"
pos.status           # "active" or "resolved"
pos.resolves_at      # Resolution timestamp
pos.sources          # List of source tags
```

### Portfolio

```python
portfolio = client.get_portfolio()
# Returns dict:
# balance_usdc, total_exposure, positions_count, pnl_total, concentration, by_source
```

### Market Context (Pre-Trade)

```python
context = client.get_market_context("uuid")
# Returns dict with:
# market: {time_to_resolution, ...}
# warnings: ["MARKET RESOLVED", ...]
# discipline: {warning_level: "none"|"mild"|"severe", flip_flop_warning: "..."}
# slippage: {estimates: [{slippage_pct: 0.05, ...}]}
# edge: {recommendation: "TRADE"|"HOLD"|"SKIP", user_edge: 0.15, suggested_threshold: 0.10}
# is_paid, fee_rate_bps, fee_note
```

Use this before placing a trade — not for scanning. ~2-3s per call.

### Market Import

```python
# Discover importable markets
results = client.list_importable_markets(
    q="bitcoin", venue="polymarket", min_volume=50000, limit=20
)

# Import a Polymarket market
result = client.import_market("https://polymarket.com/event/...")
```

Import quota: 10/day free, 50/day Pro.

### Price History

```python
history = client.get_price_history("uuid")
# List of {price_yes: float, timestamp: str, ...}
```

### Briefing (Heartbeat)

REST-only — no SDK method. Use `client._request()`:

```python
briefing = client._request("GET", "/api/sdk/briefing", params={"since": "2026-02-08T00:00:00Z"})
# Returns: portfolio, positions (active/resolved_since/expiring_soon/significant_moves),
# opportunities (new_markets/high_divergence), risk_alerts, performance
```

## Trading Venues

| Venue | Currency | Notes |
|-------|----------|-------|
| `simmer` | $SIM (virtual) | Default. AMM (instant fills, no spread). |
| `polymarket` | USDC.e (real) | Orderbook. Requires `WALLET_PRIVATE_KEY` env var. |
| `kalshi` | USD (real) | Pro plan only. Requires `SOLANA_PRIVATE_KEY` env var. |

**Important:** $SIM uses AMM (no spread). Real venues have 2-5% bid/ask spreads plus fees (`is_paid` markets charge 10% taker fee). Even apparent edges may not survive real-world spreads, fees, and latency.

## Polymarket Constraints

- Minimum order: 5 shares
- Minimum tick: $0.01
- USDC.e on Polygon (not native USDC)
- Some markets charge 10% taker fee (`is_paid: true`)

## Rate Limits

| Endpoint | Free | Pro |
|----------|------|-----|
| `/api/sdk/trade` | 60/min | 180/min |
| `/api/sdk/markets` | 60/min | 180/min |
| `/api/sdk/context` | 12/min | 36/min |
| `/api/sdk/positions` | 6/min | 18/min |
| `/api/sdk/portfolio` | 6/min | 18/min |
| Market imports | 10/day | 50/day |

## Direct Polymarket Data (No Auth)

```python
# Orderbook depth
# GET https://clob.polymarket.com/book?token_id=<polymarket_token_id>

# Midpoint price
# GET https://clob.polymarket.com/midpoint?token_id=<token_id>

# Price history
# GET https://clob.polymarket.com/prices-history?market=<token_id>&interval=1w&fidelity=60
```

Get `polymarket_token_id` from the market response. Use these for read-only data; always use Simmer for trades.
