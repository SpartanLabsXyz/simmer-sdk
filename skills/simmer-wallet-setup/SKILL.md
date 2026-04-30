---
name: simmer-wallet-setup
description: Self-custody wallet setup for Simmer agents. Choose OWS (recommended — encrypted local vault, multi-chain, policy controls) or external raw key (existing setups). Skip this skill if you use a managed wallet — managed setup is a one-time dashboard flow, not an agent task.
metadata:
  author: "Simmer (@simmer_markets)"
  version: "0.1.0"
  displayName: Simmer Wallet Setup
  difficulty: beginner
  primaryEnv: SIMMER_API_KEY
  envVars:
    - name: SIMMER_API_KEY
      required: true
      description: "Your Simmer SDK API key (from agent registration)."
    - name: OWS_WALLET
      required: false
      description: "OWS wallet name. Set only if you chose Path A (OWS)."
    - name: WALLET_PRIVATE_KEY
      required: false
      description: "Polygon EVM private key. Set only if you chose Path B (external raw key)."
---

# Simmer Wallet Setup

Self-custody wallet setup for an agent that signs its own real-money trades on Polymarket or Kalshi. Two paths:

| Mode | Who signs | When to choose |
|---|---|---|
| **OWS per-agent** (recommended) | Local OWS vault, encrypted at rest | Per-agent isolation, multi-chain, policy-gated signing. Available for Polymarket + Kalshi. |
| **External raw key** | Local SDK with `WALLET_PRIVATE_KEY` env | Existing setups. Fully supported; OWS is recommended for new agents. |

> **Already on a managed wallet?** You don't need this skill. Managed setup is a one-time dashboard action — go to [simmer.markets/dashboard](https://simmer.markets/dashboard), connect a Polygon wallet, approve the contracts, and the Simmer server signs trades within those approval bounds. No agent-side setup required.

## Path A — OWS per-agent wallet (recommended)

OWS = [Open Wallet Standard](https://openwallet.sh). Local-first encrypted vault, multi-chain, policy engine, agent-scoped API keys. The private key never leaves the local machine.

### One-time setup

```bash
# Install OWS CLI
curl -fsSL https://docs.openwallet.sh/install.sh | bash
pip install open-wallet-standard

# Create a wallet for this agent
ows wallet create --name "my-agent-wallet"
# Stores at ~/.ows/wallets/, derives addresses for EVM (Polygon), Solana, etc.

# Fund it — show the EVM address to the human, they bridge USDC.e to Polygon
ows wallet show my-agent-wallet
```

### Register the wallet with Simmer

```python
from simmer_sdk import SimmerClient
client = SimmerClient(
    api_key="sk_live_...",
    ows_wallet="my-agent-wallet",   # name from `ows wallet create`
)

client.register_agent_wallet()  # one-time, Elite-tier gated
client.set_approvals()          # one-time per chain (signed via OWS)
```

> ⚠️ **Setup requires a dashboard session.** `register_agent_wallet()` and `set_approvals()` use the browser auth from [simmer.markets/dashboard](https://simmer.markets/dashboard) in addition to the API key — they don't work from a fully headless cron. Run them once after logging in. Trading is API-only after that.

(Alternative: set `OWS_WALLET=my-agent-wallet` in the environment and pass only `api_key` — the SDK auto-detects.)

### Trade — same API, OWS routes the signing

```python
result = client.trade(
    market_id, "yes", 10.0,
    venue="polymarket",
    reasoning="..."
)
# SDK builds the order, OWS signs locally, broadcast goes through Simmer
```

### For Kalshi (Solana)

OWS is multi-chain. The same wallet has a Solana account derived automatically.

```bash
ows wallet show my-agent-wallet  # shows Solana address too
# Fund with SOL + USDC, complete KYC at dflow.net/proof
```

```python
client = SimmerClient(
    api_key="sk_live_...",
    ows_wallet="my-agent-wallet",
    venue="kalshi",
)
client.trade(market_id, "yes", 5.0, reasoning="...")
```

No `SOLANA_PRIVATE_KEY` env var needed — OWS handles signing through the same vault.

### What OWS gives over raw keys

- **Encrypted at rest** (AES-256-GCM, scrypt KDF). Private key only decrypted in-process for signing, then wiped.
- **Policy engine** — chain allowlists, daily caps, contract allowlists. Optional.
- **Multi-chain** — same vault, every chain Simmer supports.
- **Per-agent isolation** — separate wallets per agent for clean P&L attribution.
- **Agent API keys** with bounded access (revocable, expiring).

## Path B — External raw key

> Fully supported path for self-custody with an existing wallet. New agents should consider OWS first — same self-custody guarantee, encrypted at rest, multi-chain, and easier to layer policy controls. Raw-key flow stays supported for users who already have it set up.

Set the key in the environment, then construct the client:

```bash
export WALLET_PRIVATE_KEY="0x..."  # Polymarket Polygon wallet
```

```python
client = SimmerClient(api_key="sk_live_...")
# private_key is auto-detected from WALLET_PRIVATE_KEY env var
client.link_wallet()
client.set_approvals()
```

> ⚠️ **`link_wallet()` and `set_approvals()` need a dashboard session** — they use the browser auth from [simmer.markets/dashboard](https://simmer.markets/dashboard), not the SDK API key. Run them once from a logged-in dashboard, not a headless script. After that, the rest of the agent runs API-only.

### Migrating to OWS when ready

No rush. When ready, import the existing key into OWS and switch over:

```bash
ows wallet import --name "my-agent-wallet" --private-key "$WALLET_PRIVATE_KEY"
unset WALLET_PRIVATE_KEY  # OWS handles signing from here
```

Then in the agent code:

```python
client = SimmerClient(api_key="sk_live_...", ows_wallet="my-agent-wallet")
# Same trade() / set_approvals() / redeem() API — OWS routes the signing
```

The same wallet address is preserved, so existing positions and approvals carry over.

## Polymarket token note

Polymarket trades against whatever collateral token Polymarket currently uses for its CLOB (currently pUSD, the V2 collateral). The dashboard at [simmer.markets/dashboard](https://simmer.markets/dashboard) shows what the wallet needs and walks through setup. Watch for the V2 era banner at the top — it's the entry point.

**First-time activation** (new users with no prior Polymarket activity): the dashboard prompts a USDC → pUSD wrap plus a one-time approval sequence (~8 signatures total). Total ~30 seconds of clicks and ~$0.20 in gas.

**Existing Polymarket users with USDC.e** from before V2: the dashboard prompts a one-click migration (~30s) — no need to re-deposit.

Either way, after setup `client.set_approvals()` should report `all_set=True`. If it doesn't, see [docs.simmer.markets/v2-migration](https://docs.simmer.markets/v2-migration).

## Risk monitor

The auto risk monitor (stop-loss, take-profit) is configured at simmer.markets/dashboard → Settings → Auto Risk Monitor. The SDK auto-executes pending exits each `get_briefing()` cycle. The agent must be running.

## Troubleshooting

- **"External wallet requires a pre-signed order"** → key not configured. For OWS: `ows wallet list` to verify the wallet exists. For external: confirm `WALLET_PRIVATE_KEY` is set.
- **"insufficient allowance"** → run `client.set_approvals()` once per wallet.
- **Balance shows $0 but funds visible elsewhere** → check chain (Polygon vs Solana) and token (pUSD vs USDC.e). See dashboard migration tool for V2 conversion.

## Links

- OWS docs: [openwallet.sh](https://openwallet.sh)
- Simmer wallet docs: [docs.simmer.markets/wallets](https://docs.simmer.markets/wallets)
- V2 migration guide: [docs.simmer.markets/v2-migration](https://docs.simmer.markets/v2-migration)
