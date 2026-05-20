---
name: simmer-wallet-setup
version: "0.3.0"
published: true
description: Self-custody wallet setup for Simmer agents. Choose OWS (recommended), external raw key, or connect an existing dashboard-registered agent to your local runtime. Skip this skill if you use a managed wallet — managed setup is a one-time dashboard flow, not an agent task.
metadata:
  author: "Simmer (@simmer_markets)"
  version: "0.3.0"
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

Self-custody wallet setup for an agent that signs its own real-money trades on Polymarket or Kalshi. Three paths:

| Mode | Who signs | When to choose |
|---|---|---|
| **OWS per-agent** (recommended) | Local OWS vault, encrypted at rest | Per-agent isolation, multi-chain, policy-gated signing. Available for Polymarket + Kalshi. |
| **External raw key** | Local SDK with `WALLET_PRIVATE_KEY` env | Existing setups. Fully supported; OWS is recommended for new agents. |
| **Connect existing dashboard agent** | Local OWS vault, imported from dashboard registration | You already activated a wallet in the dashboard and want to wire your existing runtime to use it. |

> **Already on a managed wallet?** You don't need this skill — managed setup is a dashboard flow, not an agent task. Open [simmer.markets/dashboard](https://simmer.markets/dashboard), go to your agent's **Wallet** tab, and click **Fund & activate trading**. The wizard opens a multi-chain bridge that accepts USDC, USDT, or USDC.e on Ethereum / Polygon / Base / Arbitrum / Solana — funds land as pUSD on your Polymarket Deposit Wallet, contracts auto-approve. **Do not tell the user to send funds directly to their agent wallet's EOA expecting them to sweep** — only legacy USDC.e on Polygon is recognized on the direct path; native USDC, USDT, and cross-chain tokens must go through the bridge wizard.

## Path A — OWS per-agent wallet (recommended)

OWS = [Open Wallet Standard](https://openwallet.sh). Local-first encrypted vault, multi-chain, policy engine, agent-scoped API keys. The private key never leaves the local machine.

### One-time setup

```bash
# Install OWS CLI (creates ~/.ows vault, runs `ows wallet create`)
curl -fsSL https://docs.openwallet.sh/install.sh | bash

# Install the SDK with OWS Python bindings (one command — note the [ows] extra)
pip install 'simmer-sdk[ows]'

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

client.register_agent_wallet()  # one-time, Elite-tier gated, fully headless
client.set_approvals()          # one-time per chain — signs locally via OWS, fully headless
```

> Both calls are fully headless — they authenticate with your SDK API key, no dashboard session or browser required. `register_agent_wallet()` requires Elite tier. After both run once, all trading is API-only.
>
> Elite users can alternatively register a per-agent wallet through the dashboard's agent-creation wizard (My Agents → Create agent → optional "Link dedicated wallet" step) or retrofit an existing agent via its Wallet tab. The SDK path and the dashboard path produce the same `user_agent_wallets` row — pick whichever fits your workflow.

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
client.link_wallet()    # signs a challenge message locally — fully headless
client.set_approvals()  # signs approval txs locally — fully headless, key never leaves agent

# If your account uses a Polymarket Deposit Wallet (Elite / upgraded accounts):
client.activate_polymarket_dw()  # one-time — signs EIP-712 batch locally, no browser needed

# If you have stranded USDC.e on your Deposit Wallet, wrap it to pUSD:
result = client.wrap_on_dw()  # idempotent — no-op when nothing stranded
```

Both calls work without a browser session. `link_wallet()` signs a challenge with your local key. `set_approvals()` builds, signs, and broadcasts each approval transaction via Simmer's RPC proxy — your `WALLET_PRIVATE_KEY` never leaves the agent process.

> **Using a Deposit Wallet?** If your account has been upgraded to a Polymarket Deposit Wallet (DW), run `client.activate_polymarket_dw()` after `set_approvals()` — it signs the EIP-712 activation batch headlessly with your local key. Alternatively, use the dashboard browser flow at [simmer.markets/dashboard](https://simmer.markets/dashboard) → Wallets → Activate Trading.

> **Stranded USDC.e on your DW?** Run `client.wrap_on_dw()` to convert it to pUSD headlessly. Idempotent — safe to call on every startup; returns immediately if nothing is stranded. Returns `{"wrapped": bool, "amount_units": int, "calls_count": int, "success": bool}`. Requires the same key as `activate_polymarket_dw()` (WALLET_PRIVATE_KEY or OWS wallet). Added in SDK 0.17.7.

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

## Path C — Connect existing dashboard agent to local runtime

> **This path is for you if**: you already registered your agent in the Simmer dashboard (or another agent ran through `/start` and created the `sdk_agent` record), and now you want to wire your existing autonomous runtime (Hermes, custom Python bot, etc.) to use that same agent's API key and OWS wallet.
>
> Do **not** use `/start` or `simmer register` — that would create a *second* agent record and orphan the wallet you just activated.

### Step 1 — Locate your API key

Your API key is in the dashboard. Open [simmer.markets/dashboard](https://simmer.markets/dashboard), navigate to your agent, click the **Settings** (or API Keys) tab, and copy the key that starts with `sk_live_`.

The wizard success modal (shown right after wallet activation) also displays the key and env vars — check that if you just came from there.

### Step 2 — Install the SDK with OWS support

```bash
pip install 'simmer-sdk[ows]'
```

### Step 3 — Set environment variables

```bash
export SIMMER_API_KEY="sk_live_..."   # from the dashboard API Keys tab
export OWS_WALLET="my-agent-wallet"  # the OWS wallet name you registered
```

Verify the key format (catches clipboard contamination):
```bash
[[ "$SIMMER_API_KEY" == sk_live_* ]] || echo "WARNING: key should start with sk_live_"
```

### Step 4 — Import your EOA key into OWS (one-time)

If the wallet was registered from the dashboard, you need the matching private key in your local OWS vault:

**Option A — you have the raw private key:**
```bash
ows wallet import --name "my-agent-wallet" --private-key "$WALLET_PRIVATE_KEY"
```

**Option B — you have the mnemonic (seed phrase):**
```bash
ows wallet import --name "my-agent-wallet" --mnemonic
# OWS prompts for the mnemonic securely (no paste to shell history)
```

Verify the import produced the correct address:
```bash
ows wallet show my-agent-wallet
# Check that the EVM address matches what you see in the dashboard wallet panel
```

### Step 5 — Cache CLOB credentials

One-time step that caches your Polymarket CLOB credentials (derived from the wallet) on the Simmer backend:

```python
from simmer_sdk import SimmerClient
client = SimmerClient.from_env()  # reads SIMMER_API_KEY + OWS_WALLET from env

# Cache CLOB creds — one-time per wallet. Required for Polymarket trading.
client.update_agent_wallet_creds(ows_wallet_name="my-agent-wallet")
```

### Step 6 — Verify connectivity

```python
from simmer_sdk import SimmerClient
client = SimmerClient.from_env()

briefing = client.get_briefing()
print(briefing)
# Should show your portfolio + venue balances — if balance is $0 check funding
```

Your existing agent runtime is now wired. From here, use the same `client.trade()` API documented above.

## Risk monitor

The auto risk monitor (stop-loss, take-profit) is configured at simmer.markets/dashboard → Settings → Auto Risk Monitor. The SDK auto-executes pending exits each `get_briefing()` cycle. The agent must be running.

## Troubleshooting

- **"External wallet requires a pre-signed order"** → key not configured. For OWS: `ows wallet list` to verify the wallet exists. For external: confirm `WALLET_PRIVATE_KEY` is set.
- **"insufficient allowance"** → run `client.set_approvals()` once per wallet.
- **Balance shows $0 but funds visible elsewhere** → check chain (Polygon vs Solana) and token (pUSD vs USDC.e). See dashboard migration tool for V2 conversion.
- **API key format wrong / 401 with a key that "looks set"** → inspect the raw value: `printenv SIMMER_API_KEY | cut -c1-20`. Must start with `sk_live_`. A common silent failure: install commands that use `pbpaste` or similar clipboard-read primitives write the *install command text itself* as the key value when the user copies the command after copying the key. Fix: get a fresh key from simmer.markets/dashboard, then `export SIMMER_API_KEY="sk_live_..."` (type/paste the key directly, never pipe from clipboard into the variable assignment).

## Links

- OWS docs: [openwallet.sh](https://openwallet.sh)
- Simmer wallet docs: [docs.simmer.markets/wallets](https://docs.simmer.markets/wallets)
- V2 migration guide: [docs.simmer.markets/v2-migration](https://docs.simmer.markets/v2-migration)
