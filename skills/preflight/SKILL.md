---
name: simmer-preflight
version: "0.3.1"
published: true
description: Pre-trade readiness check for autonomous agents. One call returns wallet identity, venue status, spendable balance, open exposure, and a structured ok_to_trade verdict. Run before every real-money trade to prevent cap overruns and catch config issues before they become P&L issues.
metadata:
  author: "Simmer (@simmer_markets)"
  version: "0.3.1"
  displayName: Simmer Preflight
  difficulty: beginner
  primaryEnv: SIMMER_API_KEY
  envVars:
    - name: SIMMER_API_KEY
      required: true
      description: "Your Simmer SDK API key."
---

# Simmer Preflight

Run `client.preflight()` before every real-money trade. One call returns:

- **Who you are**: agent ID, tier, venue resolved from your API key context
- **Which wallet will sign**: execution wallet, deposit wallet, signer mode (OWS / external key / managed)
- **Spendable balance**: venue-specific USDC or $SIM balance from the briefing
- **Open exposure**: sum of `current_value` across your existing positions (real venues only for USD cap)
- **Cap check**: whether `planned_amount + open_exposure_total` exceeds your `exposure_cap_usd`
- **Risk alerts**: any briefing risk signals (concentration, expiry, reliability)
- **ok_to_trade**: a single boolean — True if no blockers

## When to use

Call preflight before every automated trade, especially:

- Before the first real-money order of a run
- Before scaling into a position beyond your initial size
- When your heartbeat wakes up after a long sleep (state may have changed)

Do not call it on every polling tick — it makes 3 API calls. Once per trade intent is correct.

## Call it

```python
from simmer_sdk import SimmerClient

client = SimmerClient.from_env(venue="polymarket")

result = client.preflight(
    venue="polymarket",       # venue to check (defaults to client.venue)
    planned_amount=5.0,       # USDC you plan to spend on this trade
    exposure_cap_usd=100.0,   # your total cross-venue exposure limit
)

if not result.ok_to_trade:
    print(f"Preflight blocked: {result.blockers}")
    # e.g. ["EXPOSURE_CAP_EXCEEDED"]
    return

# Log for audit
ledger.record({
    "preflight_id": result.client_preflight_id,
    "ok": result.ok_to_trade,
    "exposure_before": result.open_exposure_total,
    "planned": result.planned_amount,
})
```

## Result fields

| Field | Type | Description |
|---|---|---|
| `client_preflight_id` | str | UUID for your trade ledger — log before every order |
| `agent_id` | str \| None | Agent UUID from `/api/sdk/agents/me` |
| `tier` | str \| None | Account tier: "free" / "pro" / "elite" |
| `resolved_venue` | str | Normalised venue: "sim" / "polymarket" / "kalshi" |
| `execution_wallet` | str \| None | EOA that signs trades (per-agent OWS wallet for Elite per-agent callers) |
| `deposit_wallet` | str \| None | Deposit wallet address (DW cohort only; None for Cohort A) |
| `signer_status` | str | "ows" / "external_key" / "managed" |
| `spendable_balance` | float \| None | Venue balance: pUSD/USDC (real) or $SIM (sim) |
| `gas_balance` | float \| None | POL / SOL for gas — None in v0 (deferred to v1) |
| `open_exposure_total` | float | Sum of `current_value` across open positions (real venues only when cap is USD) |
| `exposure_cap_usd` | float | The cap you passed in |
| `planned_amount` | float | The planned trade size you passed in |
| `would_exceed_cap` | bool | True if `open_exposure_total + planned_amount > exposure_cap_usd` |
| `pending_alerts` | list[dict] | Risk alerts from briefing (normalised to `{message: str}`) |
| `ok_to_trade` | bool | **True if no blockers** |
| `blockers` | list[str] | Blocker codes (see below) |
| `warnings` | list[str] | Non-blocking advisories (fetch failures, skipped checks) |

## Blocker codes (v0)

| Code | Meaning | Fix |
|---|---|---|
| `EXPOSURE_CAP_EXCEEDED` | `open_exposure_total + planned_amount > exposure_cap_usd` | Wait for existing positions to resolve, or raise your cap |
| `WALLET_UNVERIFIED` | Real venue requested but `real_trading_enabled` is False, or no wallet configured | Claim agent + link wallet in dashboard |
| `VENUE_UNSUPPORTED` | Venue string not recognised | Use "sim", "polymarket", or "kalshi" |
| `INSUFFICIENT_GAS` | Gas signal detected in risk_alerts (proxy only in v0) | Fund wallet with POL / SOL |
| `EXPOSURE_UNKNOWN` | Real venue + active cap, but positions fetch failed — fail-closed | Check connectivity or set `exposure_cap_usd=0` to disable cap temporarily |

Blockers are additive — all blocking conditions are reported, not just the first.

## Per-agent caller (Elite OWS)

For per-agent API keys, `execution_wallet` returns the per-agent OWS EOA — not the parent user's wallet. This is the SIM-2130 identity guarantee: the preflight returns the wallet that will actually sign the trade.

```python
result = client.preflight(venue="polymarket", planned_amount=5, exposure_cap_usd=100)
print(result.execution_wallet)   # 0xYourAgentOWS... (not parent user's wallet)
print(result.deposit_wallet)     # 0xYourAgentDW... (per-agent DW if activated)
```

## Exposure calculation

`open_exposure_total` sums `current_value` from `/api/sdk/positions`:

- For **real venues** (polymarket, kalshi): sums non-sim positions in USDC.
- For **sim venue**: sums sim positions in $SIM.

$SIM positions are never included in a USD cap check — they use virtual currency.

## warnings[] vs blockers[]

`blockers` stop trading. `warnings` are informational — common examples:

- `briefing_fetch_failed: <error>` — balance / alerts unknown; check connectivity
- `positions_fetch_failed: <error>` — exposure may be understated; check rate limits
- `identity_fetch_failed: <error>` — agent ID / tier unknown; API key may be invalid

If you see warnings, log them. Fetch failures on individual endpoints are gracefully handled — preflight returns best-effort data rather than throwing.

## Pre-trade pattern (cookbook)

```python
import os
from simmer_sdk import SimmerClient

EXPOSURE_CAP = float(os.environ.get("EXPOSURE_CAP_USD", "100"))

client = SimmerClient.from_env(venue="polymarket")

def safe_trade(market_id: str, side: str, amount: float):
    pf = client.preflight(
        venue="polymarket",
        planned_amount=amount,
        exposure_cap_usd=EXPOSURE_CAP,
    )
    if not pf.ok_to_trade:
        print(f"Preflight blocked ({pf.blockers}), skipping trade")
        return None
    if pf.warnings:
        print(f"Preflight warnings: {pf.warnings}")

    # Log preflight before order submission
    print(f"Preflight OK — id={pf.client_preflight_id} "
          f"exposure={pf.open_exposure_total:.2f}+{amount:.2f}/{EXPOSURE_CAP}")

    return client.trade(market_id=market_id, side=side, amount=amount)
```

## Cadence

Call once per trade intent — not on every poll cycle. The preflight makes 3 API calls (`/agents/me`, `/briefing`, `/positions`), each counting toward your rate limits.

## Scope

Preflight is read-only. It never signs, trades, redeems, or mutates settings. It is safe to call in any context including paper-trading mode (positions will reflect simulated holdings).

## v0 limitations

- `gas_balance` is always `None` — on-chain RPC not available in the SDK client. Use the dashboard to verify POL / SOL balance.
- `INSUFFICIENT_GAS` is only detected if the briefing risk_alerts mention gas explicitly — not from an on-chain query.
- Server-side `preflight_id` (stable, storable) deferred to v1.
- MCP tool exposure deferred to v1.

## Links

- API reference: [docs.simmer.markets/api/preflight](https://docs.simmer.markets)
- Wallet cohort guide: [docs.simmer.markets/wallets](https://docs.simmer.markets)
