---
name: simmer-preflight
version: "0.3.6"
published: true
read_only: true
description: Pre-trade readiness check for autonomous agents. One call returns wallet identity, venue status, spendable balance, open exposure, and a structured ok_to_trade verdict. Live real-venue trades auto-run this check and refuse when ok_to_trade is False.
metadata:
  author: "Simmer (@simmer_markets)"
  version: "0.3.6"
  displayName: Simmer Preflight
  difficulty: beginner
  primaryEnv: SIMMER_API_KEY
  envVars:
    - name: SIMMER_API_KEY
      required: true
      description: "Your Simmer SDK API key."
---

# Simmer Preflight

Live real-money trades now **enforce** this check. `client.trade(..., dry_run=False)` and `client.place_combo(..., dry_run=False)` on a `live=True` client against a real venue (`polymarket` / `kalshi`) auto-run `preflight()` and refuse if `ok_to_trade` is False, surfacing the blocker codes. The auto-gate's exposure cap is opt-in (`EXPOSURE_CAP_USD` set → enforced; unset → cap disabled) and sells are exempt from cap math so an over-cap book can still reduce exposure. Wallet / venue / gas blockers still run. Paper, `venue="sim"`, and `dry_run=True` are unchanged. One-release migrate valve: `skip_preflight=True` or `SIMMER_SKIP_PREFLIGHT=1` (deprecation warning). MCP `simmer_trade` uses the same live gate; `SIMMER_MCP_ALLOW_LIVE` is still required and is not a replacement.

Call `client.preflight()` yourself when you want the full result (wallet, exposure, `client_preflight_id`) before you size or submit. One call returns:

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
| `INSUFFICIENT_GAS` | Structured risk_alert `code` is `INSUFFICIENT_GAS` (v0 proxy; no on-chain query) | Fund wallet with POL / SOL |
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
- Positions with `venue: null` / missing venue count as sim (virtual), not real USDC.

For Kalshi, `real_trading_enabled` is a client-side claim check (`WALLET_UNVERIFIED` if false). The server gates only Polymarket on this flag — unclaimed Kalshi agents are blocked in the SDK/MCP preflight, not by the Kalshi place path.

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

client = SimmerClient.from_env(venue="polymarket")

def safe_trade(market_id: str, side: str, amount: float):
    # Live auto-gate: EXPOSURE_CAP_USD is opt-in (unset = no cap, no $100 default).
    # Manual preflight() still accepts an explicit exposure_cap_usd.
    cap_raw = os.environ.get("EXPOSURE_CAP_USD")
    if cap_raw:
        pf = client.preflight(
            venue="polymarket",
            planned_amount=amount,
            exposure_cap_usd=float(cap_raw),
        )
    else:
        # No env cap: live trade() will not enforce one. This explicit
        # preflight() call still uses the method default (exposure_cap_usd=100).
        pf = client.preflight(
            venue="polymarket",
            planned_amount=amount,
        )
    if not pf.ok_to_trade:
        print(f"Preflight blocked ({pf.blockers}), skipping trade")
        return None
    if pf.warnings:
        print(f"Preflight warnings: {pf.warnings}")

    # Log preflight before order submission
    print(f"Preflight OK — id={pf.client_preflight_id} "
          f"exposure={pf.open_exposure_total:.2f}+{amount:.2f}/{pf.exposure_cap_usd}")

    return client.trade(market_id=market_id, side=side, amount=amount)
```

## Cadence

Call once per trade intent — not on every poll cycle. The preflight makes 3 API calls (`/agents/me`, `/briefing`, `/positions`), each counting toward your rate limits.

## Scope

Preflight is read-only. It never signs, trades, redeems, or mutates settings. It is safe to call in any context including paper-trading mode (positions will reflect simulated holdings).

## v0 limitations

- `gas_balance` is always `None` — on-chain RPC not available in the SDK client. Use the dashboard to verify POL / SOL balance.
- `INSUFFICIENT_GAS` is only detected from a structured risk_alert `code` of `INSUFFICIENT_GAS` — not from free-text `gas` / `pol` substrings, and not from an on-chain query.
- Server-side `preflight_id` (stable, storable) deferred to v1.
- MCP `simmer_preflight` is available; live `simmer_trade` now enforces the same `ok_to_trade` gate.

## Links

- API reference: [docs.simmer.markets/api/preflight](https://docs.simmer.markets)
- Wallet cohort guide: [docs.simmer.markets/wallets](https://docs.simmer.markets)
