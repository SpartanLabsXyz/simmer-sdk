---
name: simmer-wallet-setup
version: "0.4.0"
published: true
description: Self-custody wallet setup for Simmer agents. Bring your own key, import a funded Polymarket wallet, or connect an existing dashboard-registered agent to your local runtime. Skip this skill if you use a managed wallet — managed setup is a one-time dashboard flow, not an agent task.
metadata:
  author: "Simmer (@simmer_markets)"
  version: "0.4.0"
  displayName: Simmer Wallet Setup
  difficulty: beginner
  primaryEnv: SIMMER_API_KEY
  envVars:
    - name: SIMMER_API_KEY
      required: true
      description: "Your Simmer SDK API key (from agent registration)."
    - name: WALLET_PRIVATE_KEY
      required: false
      description: "Polygon EVM private key. The standard self-custody signer."
    - name: OWS_WALLET
      required: false
      description: "OWS wallet name. Optional key store — set instead of WALLET_PRIVATE_KEY if the key lives in an OWS vault."
---

# Simmer Wallet Setup

Self-custody wallet setup for an agent that signs its own real-money trades on Polymarket or Kalshi. Three paths:

| Mode | Who signs | When to choose |
|---|---|---|
| **[External key](#path-a--external-key)** (default) | Local SDK with `WALLET_PRIVATE_KEY` env — or an OWS key store via `OWS_WALLET` | Any self-custody setup: your own wallet, your key, your machine. |
| **[Import a Polymarket wallet](#path-b--import-a-polymarket-wallet)** | Same local signing — key exported from polymarket.com | Fund with card/exchange via Polymarket's onramps; the wallet arrives at Simmer already funded and approved. |
| **[Connect existing agent](#connect-existing-agent)** | Same local signing, wallet already registered in the dashboard | You already activated a dedicated per-agent wallet in the dashboard and want to wire your runtime to it. |

> **Already on a managed wallet?** You don't need this skill — managed setup is a dashboard flow, not an agent task. Open [simmer.markets/dashboard](https://simmer.markets/dashboard?ref=sdk-skill&utm_campaign=sdk-skill), go to your agent's **Wallet** tab, and click **Fund & activate trading**. The wizard opens a multi-chain bridge that accepts USDC, USDT, or USDC.e on Ethereum / Polygon / Base / Arbitrum / Solana — funds land as pUSD on your Polymarket Deposit Wallet, contracts auto-approve. **Do not tell the user to send funds directly to their agent wallet's EOA expecting them to sweep** — only legacy USDC.e on Polygon is recognized on the direct path; native USDC, USDT, and cross-chain tokens must go through the bridge wizard.

## Key safety (read this first)

The private key goes in exactly one place: the agent host's environment, or a local key store.

- Use `read -s` to set it — never paste a key into a pipe, a chat, a ticket, a shared config file, or a second browser extension "just to sign" (per SIM-2118):
  ```bash
  read -s -p 'WALLET_PRIVATE_KEY: ' K && export WALLET_PRIVATE_KEY=$K
  ```
- **Prefer a key store over a bare env var.** [OWS](https://openwallet.sh) (Open Wallet Standard) keeps the key encrypted at rest (AES-256-GCM); the SDK picks it up via `OWS_WALLET` and signs through the vault. Its policy engine can scope the agent's signing token to Polymarket orders only.
  ```bash
  curl -fsSL https://docs.openwallet.sh/install.sh | bash   # one-time OWS install
  pip install 'simmer-sdk[ows]'
  ows wallet import --name "my-agent-wallet" --private-key "$WALLET_PRIVATE_KEY"
  unset WALLET_PRIVATE_KEY
  export OWS_WALLET=my-agent-wallet   # SDK signs via the vault from here
  ```
- If a key ever touches a chat or shared surface, treat it as compromised: withdraw to a fresh wallet and start over.

## Path A — External key

Set the key (see key safety above), then construct the client:

```python
client = SimmerClient(api_key="sk_live_...")
# signer auto-detected: WALLET_PRIVATE_KEY or OWS_WALLET env var
client.link_wallet()    # signs a challenge message locally — fully headless
client.set_approvals()  # signs approval txs locally — fully headless, key never leaves agent

# If your account uses a Polymarket Deposit Wallet (upgraded accounts):
client.activate_polymarket_dw()  # one-time — signs EIP-712 batch locally, no browser needed

# If you have stranded USDC.e on your Deposit Wallet, wrap it to pUSD:
result = client.wrap_on_dw()  # idempotent — no-op when nothing stranded
```

All calls work without a browser session. `link_wallet()` signs a challenge with your local key. `set_approvals()` builds, signs, and broadcasts each approval transaction via Simmer's RPC proxy — your key never leaves the agent process.

> **Using a Deposit Wallet?** If your account has been upgraded to a Polymarket Deposit Wallet (DW), run `client.activate_polymarket_dw()` after `set_approvals()` — it signs the EIP-712 activation batch headlessly with your local key. Alternatively, use the dashboard browser flow at [simmer.markets/dashboard](https://simmer.markets/dashboard?ref=sdk-skill&utm_campaign=sdk-skill) → Wallets → Activate Trading.

> **Stranded USDC.e on your DW?** Run `client.wrap_on_dw()` to convert it to pUSD headlessly. Idempotent — safe to call on every startup; returns immediately if nothing is stranded. Returns `{"wrapped": bool, "amount_units": int, "calls_count": int, "success": bool}`. Requires the same signer as `activate_polymarket_dw()`. Added in SDK 0.17.7.

### For Kalshi (Solana)

Set `SOLANA_PRIVATE_KEY` (base58) in the env, fund with SOL + USDC, complete KYC at dflow.net/proof. If the key lives in an OWS vault, the same wallet's derived Solana account signs — no `SOLANA_PRIVATE_KEY` needed:

```python
client = SimmerClient.from_env(venue="kalshi")
client.trade(market_id, "yes", 5.0, reasoning="...")
```

## Path B — Import a Polymarket wallet

For a wallet born on polymarket.com: create the account there, fund it with their onramps (card, exchange transfer), export the private key (polymarket.com → Settings → Export Private Key), and set it as `WALLET_PRIVATE_KEY` on the agent host (key safety above applies in full — this is a raw key).

```python
client = SimmerClient.from_env()
result = client.import_polymarket_wallet()   # SDK 0.24.4+
# links with ownership proof, adopts the Polymarket-deployed deposit wallet
# (no redeploy), tops up approvals gaslessly, derives creds, reports balance
```

The wallet arrives already funded and approved — Polymarket deployed the deposit wallet and pre-set the core exchange allowance. Note the shared surface: manual trades on polymarket.com use the same wallet as the agent and can collide with its positions.

Docs: [docs.simmer.markets/polymarket-import](https://docs.simmer.markets/polymarket-import).

## Dedicated per-agent wallets (Elite)

Elite accounts can give each agent its own wallet (own EOA + own deposit wallet, clean per-agent P&L). **Register the wallet from the dashboard** (My Agents → your agent → Add wallet), then activate on the host where the key lives:

```python
# WALLET_PRIVATE_KEY must be set to the agent EOA private key.
client.activate_polymarket_dw(agent_id="<agent_id>")     # sets on-chain approvals — OWS/raw-key signed, gasless relay
client.update_agent_wallet_creds(agent_id="<agent_id>")  # derives + caches CLOB creds
```

Both calls are required (approvals first) and idempotent. Don't use `set_approvals()` here — that's the user-primary EOA path and a no-op for per-agent deposit wallets. Get `<agent_id>` from `client.get_agent_wallets()`.

> **Existing OWS per-agent wallets:** new OWS per-agent registrations are closed (`register_agent_wallet()` is deprecated and the server rejects it), but wallets already registered on that path keep working unchanged — `client.update_agent_wallet_creds(ows_wallet_name="<wallet>")` remains supported for them. OWS as a key store for the standard path (everything above) is unaffected.

## Polymarket token note

Polymarket trades against whatever collateral token Polymarket currently uses for its CLOB (currently pUSD, the V2 collateral). The dashboard at [simmer.markets/dashboard](https://simmer.markets/dashboard?ref=sdk-skill&utm_campaign=sdk-skill) shows what the wallet needs and walks through setup. Watch for the V2 era banner at the top — it's the entry point.

**First-time activation** (new users with no prior Polymarket activity): the dashboard prompts a USDC → pUSD wrap plus a one-time approval sequence (~8 signatures total). Total ~30 seconds of clicks and ~$0.20 in gas.

**Existing Polymarket users with USDC.e** from before V2: the dashboard prompts a one-click migration (~30s) — no need to re-deposit.

Either way, after setup `client.set_approvals()` should report `all_set=True`. If it doesn't, see [docs.simmer.markets/v2-migration](https://docs.simmer.markets/v2-migration).

## Connect existing agent

You already created an agent in the dashboard, registered its dedicated wallet, and have its API key. Now wire it to your locally-running agent runtime.

### Prerequisites
- API key (from dashboard → agent settings → API key, or wizard success modal)
- The wallet's EOA private key (the one you imported during dashboard activation)
- Python 3.10+ runtime where your agent runs

### Steps

1. **Install packages**
   ```bash
   pip install simmer-sdk          # add [ows] extra if using the OWS key store
   ```

2. **Set env vars in your agent runtime**
   Use `read -s` to avoid clipboard contamination (per SIM-2118):
   ```bash
   read -s -p 'SIMMER_API_KEY: ' KEY && export SIMMER_API_KEY=$KEY
   read -s -p 'WALLET_PRIVATE_KEY: ' K && export WALLET_PRIVATE_KEY=$K
   ```
   (Optionally import the key into OWS instead — see key safety above — and set `OWS_WALLET`.)

3. **Activate trading: on-chain approvals + CLOB credentials (one-time)**
   ```bash
   python -c "from simmer_sdk import SimmerClient; \
     c = SimmerClient.from_env(venue='polymarket'); \
     c.activate_polymarket_dw(agent_id='<agent_id>'); \
     c.update_agent_wallet_creds(agent_id='<agent_id>')"
   ```
   First sets the deposit wallet's on-chain CLOB approvals (locally-signed EIP-712 batch, relayed gasless), then derives + caches CLOB creds server-side. **Both** are required before any Polymarket trades — approvals alone or creds alone is not enough. Get `<agent_id>` from `client.get_agent_wallets()`. (Existing OWS-registered wallets replace the final call with `c.update_agent_wallet_creds(ows_wallet_name='<name>')`.)

4. **Verify**
   ```bash
   python -c "from simmer_sdk import SimmerClient; \
     c = SimmerClient.from_env(); \
     print(c.get_briefing())"
   ```

### Anti-patterns
- Don't paste your private key from clipboard into a pipe — use `read -s` (per SIM-2118).
- Don't skip `activate_polymarket_dw(agent_id=...)` — `update_agent_wallet_creds` alone caches creds without setting on-chain approvals, so trades fail at the relayer. Run both, approvals first.

## Risk monitor

The auto risk monitor (stop-loss, take-profit) is configured at simmer.markets/dashboard → Settings → Auto Risk Monitor. The SDK auto-executes pending exits each `get_briefing()` cycle. The agent must be running.

## Troubleshooting

- **"External wallet requires a pre-signed order"** → key not configured. Confirm `WALLET_PRIVATE_KEY` is set — or, for an OWS key store, `ows wallet list` to verify the wallet exists and `OWS_WALLET` is set.
- **"insufficient allowance"** → set approvals once per wallet: `client.activate_polymarket_dw(agent_id=...)` for a per-agent deposit wallet, `client.activate_polymarket_dw()` for a user-primary deposit wallet, or `client.set_approvals()` for a legacy raw-key EOA wallet.
- **Balance shows $0 but funds visible elsewhere** → check chain (Polygon vs Solana) and token (pUSD vs USDC.e). See dashboard migration tool for V2 conversion.
- **API key format wrong / 401 with a key that "looks set"** → inspect the raw value: `printenv SIMMER_API_KEY | cut -c1-20`. Must start with `sk_live_`. A common silent failure: install commands that use `pbpaste` or similar clipboard-read primitives write the *install command text itself* as the key value when the user copies the command after copying the key. Fix: get a fresh key from simmer.markets/dashboard, then `export SIMMER_API_KEY="sk_live_..."` (type/paste the key directly, never pipe from clipboard into the variable assignment).

## Links

- Polymarket wallet import: [docs.simmer.markets/polymarket-import](https://docs.simmer.markets/polymarket-import)
- OWS docs: [openwallet.sh](https://openwallet.sh)
- Simmer wallet docs: [docs.simmer.markets/wallets](https://docs.simmer.markets/wallets)
- V2 migration guide: [docs.simmer.markets/v2-migration](https://docs.simmer.markets/v2-migration)
