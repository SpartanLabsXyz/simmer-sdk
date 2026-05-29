# Changelog

All notable changes to `simmer-sdk` are documented here.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.17.23] — 2026-05-26

### Fixed

- **`dw_redeem.prepare_dw_redeem` handles 422 `not_redeemable` responses.** Server now returns HTTP 422 (not 200) when on-chain payout is not finalized. The SDK's error handler preserves the `not_redeemable`, `reason`, and `detail` flags so `client.redeem()` and `auto_redeem()` skip cleanly instead of retrying.

## [Unreleased]

## [0.17.24] — 2026-05-29

### Added

- **Bulk top-of-book quote fields on `Market` and `Position` (SIM-2641).** `client.get_markets()` and `client.get_positions()` now expose `best_bid`, `best_ask`, `best_bid_size`, `best_ask_size`, `spread`, and `quote_ts` on the returned objects (Polymarket only; `None` for sim/kalshi or when the live book is unavailable). On positions these are held-side aware — pure NO-side holders get the NO outcome's `best_bid` (their exit price). Use for scanning candidates and monitoring held positions without a per-market `executable-price` call each; `quote_ts` (~30s freshness) lets you detect stale quotes.

### Fixed

- **`polymarket-mil-aircraft-tracker`: gate auto-trading on market category.** Markets are now classified as `strike_activity` (trade as before) or `invasion_tail_risk` (alert-only, no trade). Keyword matching on the question/description — invasion/invade/regime change/annex/declare war → tail-risk; strike/attack/airstrike/bomb → activity. Tail-risk markets emit a `[mil-tracker] Tail-risk market detected, alert-only: {question}` log line and are counted separately in the `tail_risk_alerts` field of the automaton output. Eliminates erroneous entry into long-horizon geopolitical markets (e.g. "Will North Korea invade South Korea before 2027?") triggered by routine ADS-B patrol activity.

### Infrastructure

- **MCP tool safety gate is now structurally enforced.** All tools registered with the MCP server must explicitly declare `mutates: boolean`. Tools with `mutates: true` are blocked unless `SIMMER_MCP_ALLOW_LIVE=true` is set — no opt-in, no live state changes. The compiler rejects any new tool that omits the field, making the failure mode that previously left `simmer_cancel_order` ungated structurally impossible.

### Fixed

- **`polymarket-weather-trader`: skip markets with nonexistent CLOB orderbooks.** The skill now detects "orderbook does not exist" errors at execution time, caches the offending market ID for the run, and skips it on any subsequent encounter in the same process. Eliminates repeated failures against stale token IDs that remain in the market catalog after a market's CLOB book is removed.

### Added

- **`simmer_sdk.regime` — realized-vol regime gate.** A venue-agnostic primitive that lets a strategy declare which regime (range-bound vs trending) it is registered for, then skip entirely when the current realized volatility says we're in the wrong regime.

  ```python
  from simmer_sdk import realized_vol_gate, size_position

  decision = realized_vol_gate(
      close_prices,                     # last N candle closes, oldest first
      lookback_candles=12,
      regime_strategy="range_bound",    # or "trending"
      vol_threshold=0.02,               # tune per asset/timeframe
  )
  if not decision.allowed:
      return  # log decision.reason and skip — do not size

  amount = size_position(p_win, market_price, bankroll)
  ```

  Returns a `RegimeDecision` with `allowed`, `realized_vol`, `regime`, `reason`, and `n_candles`. Fails closed when fewer than `lookback_candles` prices are supplied. See `examples/regime_gate_skill.py` for the canonical wiring pattern, and `REGIME_CONFIG_SCHEMA` for opt-in via `config.json` / env vars.

  **Operator note — tuning `vol_threshold`:** "trending" means *volatile*, not *directional*. Realized vol is the std-dev of per-candle price diffs. Tune the threshold against your asset's *choppy vs calm* distribution. See the tuning workflow in `examples/regime_gate_skill.py`.

- **Paste-a-post skill builder workflow (#136).** New `skills/skill-builder/` ClawHub skill lets agents create trading skills from a natural-language description (forum post, tweet, strategy writeup). Includes the agent-as-oracle pattern for delegated market resolution.

- **`client.preflight()` wired into bundled trading skills.** All 6 bundled skills (weather-trader, copytrading, mert-sniper, elon-tweets, fast-loop, signal-sniper) now call `client.preflight()` before first trade and surface blockers/warnings in skill logs.

### Fixed

- **`polymarket-weather-trader` forces GTC order type, overrides FAK.** Weather markets are structurally illiquid; FAK orders are rejected immediately with no fill. The skill now detects when `order_type=FAK` is configured (via env var or `config.json`) and overrides it to GTC at startup with a clear warning. Default users are unaffected — the default was already GTC.
- **`preflight()` now gates every trade attempt in bundled weather-trader skills.** Both `polymarket-weather-trader` and `kalshi-weather-trader` now call `client.preflight()` before each `execute_trade()` and `execute_sell()` call when running live. `kalshi-weather-trader` also gains the run-start `ensure_can_trade(min_usd=1.0)` balance gate already present in the Polymarket variant.
- **Structured payout-readiness response for neg-risk redemptions.** `client.redeem()` and `auto_redeem()` now surface a stable `not_redeemable=True` / `reason="market_not_settled"` / `detail="neg_risk_not_determined"` result when the server signals the market is resolved but on-chain payout is not finalized yet. Previously, the prepare step succeeded and the relayer rejected the signed batch silently. The `detail` field is now preserved end-to-end.

- **Preflight-gate test stubs scoped with `patch.dict`.** Three preflight-gate test files previously used `sys.modules.setdefault("simmer_sdk", MagicMock())` which could permanently replace the real SDK module when tests ran in a mixed collection order. Replaced with a `patch.dict` context manager scoped to the skill import.

- **`auto_redeem()` warns when signing key is unavailable.** Previously failed silently for managed-wallet users calling `auto_redeem()` without a local signing key. Now surfaces a visible warning explaining that managed-wallet redemptions are handled server-side.

- **Tunable env-var names aligned between `clawhub.json` and `CONFIG_SCHEMA`.** Skills that declared config tunables in both their ClawHub metadata and their Python CONFIG_SCHEMA could have name mismatches (e.g., `SIMMER_MIN_EDGE` vs `MIN_EDGE`). Aligned across all bundled skills.

- **`bucket_spread` tunable type corrected for `polymarket-elon-tweets`.** The ClawHub slider was configured as a float in `[0.01, 0.2]`, but the code interprets `bucket_spread` as an integer count of neighboring buckets. Any ClawHub-configured value like `0.05` would `int()`-truncate to `0`, leaving only the center bucket and defeating the spread strategy. The slider is now an integer in `[0, 5]` with step 1 and a clarified label, matching the code's semantics.

- **Mert-sniper documentation corrected: expiry window default is 8 minutes.** `SKILL.md` showed a 2-minute default for `SIMMER_MERT_EXPIRY_MINUTES` in multiple places; the actual code default is 8 minutes. Updated across all references in the skill documentation.

- **Briefing skill fixes:** Removed unsupported `venue=` parameter. Converted remaining attribute access to dict access for compatibility with briefing response shape changes.

- **`kalshi-weather-trader` now gates every trade with `preflight()` and skips the run on empty wallets.** Matches the balance-check behaviour already present in `polymarket-weather-trader`: `client.preflight()` blocks each individual trade when the wallet is insufficient, and `client.ensure_can_trade(min_usd=1.0)` exits the run cleanly before the market scan when there is less than $1 available.

- **Exit scan `sources=None` guard.** Skills with no configured signal sources no longer crash on the exit scan. (#130)

## simmer-mcp v3.3.0 — 2026-05-24

### Added

- **Raw trade primitives.** Three new MCP tools for agents that want direct trade control: `place_order` (limit/market with full parameter control), `cancel_order` (by order ID), and `get_order_status`. These complement the existing `trade` tool (which handles sizing, risk checks, and position management automatically) for agents that need lower-level access.

## Skills — 2026-05-24

### Added

- **simmer-mcp-setup v0.1.2** — One-shot MCP server onboarding skill. Walks agents through installing and configuring the Simmer MCP server with validated credentials.

- **connect-existing-agent branch in simmer-wallet-setup** — New 5-step flow for agents with an existing runtime (Hermes, OpenClaw, custom) connecting to Simmer for the first time.

### Changed

- **Bundled skill disclaimer rollout.** polymarket-mert-sniper, polymarket-copytrading, and simmer-x402 now include `DISCLAIMER.md` and bounding language in their opening descriptions per the skill compliance standard. Copy reframed from "snipe" to method-first language.

## simmer-mcp v3.1.0 — 2026-05-20

### Removed

- **MCP resources `simmer://docs/api-reference` and `simmer://docs/skill-reference` are gone.** These shipped 250 KB of static markdown snapshots that drifted from the canonical source on every doc update. The MCP server is now leaner (package size cut ~70%) and there's no refresh policy to forget. Agents that need the full Simmer API reference should fetch `https://docs.simmer.markets/llms-full.txt` directly — it's always current. The `list_skills`, `get_skill_docs`, `troubleshoot_error` tools are unchanged.

## simmer-mcp v3.0.2 — 2026-05-20 (SIM-2104)

### Fixed

- **`BUNDLED_VERSION` literal drifted from `package.json`.** The 3.0.0 → 3.0.1 hotfix bumped `package.json` but not the hardcoded version in `mcp-server.ts`, so 3.0.1 installs reported themselves as 3.0.0 over the MCP handshake AND printed a spurious "Update available: 3.0.0 → 3.0.1" warning at every boot. Extracted version to `src/version.ts` reading `package.json` at module load. Single source of truth. Added regression tests asserting consistency.

## simmer-mcp v3.0.1 — 2026-05-20 (SIM-2099)

### Fixed

- **`dist/mcp-server.js` shipped without the executable bit.** The Phase 4 rename inadvertently dropped the +x permission on the bin file. `npm install -g simmer-mcp` would create the symlink but PATH-based exec rejected it. Build script now runs `chmod +x dist/mcp-server.js` after `tsc`.

## simmer-mcp v3.0.0 — 2026-05-19 (SIM-2072)

### Breaking

- **Package renamed `simmer-autoresearch` → `simmer-mcp` on npm.** Update your agent config: replace `"command": "simmer-autoresearch"` with `"command": "simmer-mcp"`, and update any `npx simmer-autoresearch` invocations to `npx simmer-mcp`. All tool names, env vars, and behavior are unchanged. The old package publishes a `simmer-autoresearch@2.99.0` deprecation stub pointing here.

### Changed

- MCP server `name` field updated from `"simmer-autoresearch"` to `"simmer-mcp"`.
- Version-check fetches from `registry.npmjs.org/simmer-mcp/latest` (was `simmer-autoresearch`).

## Skills — 2026-05-19

### Added

- **Fee-aware EV gate — polymarket-mert-sniper v1.3.0 (SIM-2039).** mert-sniper now logs the Polymarket Crypto-category entry fee (`POLY_FEE_RATE_CRYPTO × p × (1-p)`, entry-only — binary redemption at expiry is free) for every candidate market and supports an opt-in `SIMMER_MERT_MIN_EDGE` gate that blocks trades whose declared edge does not clear `fee_per_share + SIMMER_MERT_FEE_BUFFER`. The gate is advisory at default `MIN_EDGE=0` (fee logged, no behavior change), matching the skill's "bring your own alpha" framing; users computing their own EV set `MIN_EDGE` to their signal's claimed alpha to activate blocking. Fee math uses the hardcoded `POLY_FEE_RATE_CRYPTO = 0.07` constant — CLOB `/fee-rate` lookup failures degrade to a log annotation rather than fail-open.

### Fixed

- **Skip sell loop on resolved markets — polymarket-weather-trader v1.21.1, kalshi-weather-trader v1.0.7, polymarket-elon-tweets v1.3.3 (SIM-2046).** All three skills could retry sells indefinitely on resolved markets while waiting for `auto_redeem()` to claim the winning shares. Root cause: the exit loop didn't check `position.status` before attempting a sell; Polymarket/Kalshi reject sells on resolved markets with "Insufficient shares to sell", and the skill retried every cycle. Fix: added `status == "resolved"` guard immediately after the minimum-shares check. `auto_redeem()` at the top of each cycle handles the actual payout. Also changed silent `except: pass` on `auto_redeem()` to log the exception at `force=True` so future failures surface in skill logs.

## [0.17.19] — 2026-05-23

### Added

- **`client.get_markets(include=[...])` kwarg + `Market.resolution_criteria` field (SIM-2318).** New `include` parameter on `get_markets()` lets callers request optional fields server-side instead of pulling them per-market. First supported include: `"resolution_criteria"` (the human-readable text describing how a market resolves), surfaced as a new optional field on the `Market` dataclass. Backward compatible — omitting `include` returns the existing shape. Useful for skills that want to inspect resolution criteria during candidate filtering without an N+1 round trip.

### Changed

- **`TradeResult` carries `retryable: bool` and `order_id: Optional[str]` from server response (SIM-1329).** Two new fields on the `TradeResult` dataclass with safe defaults (`retryable=True`, `order_id=None`) so existing callers see no behavior change. When the server knows retrying is futile (e.g., position cleared on-chain before sell hit the CLOB), it sets `retryable=False` so the skill can stop the retry loop instead of looping at a stale price. `order_id` exposes the CLOB order ID for GTC/GTD orders, enabling `cancel_order()` follow-up. Six bundled skills (copytrading, elon-tweets, fast-loop, mert-sniper, signal-sniper, weather-trader) propagate the field and switch from `❌ Trade failed` to `⛔ Trade aborted (position cleared on-chain — no retry)` log lines when the server tags the response non-retryable. Dominant failure mode for the 18 agents with ≥10 failed sells in 7 days.

## [0.17.18] — 2026-05-23

### Added

- **`client.activate_polymarket_dw(agent_id="...")` per-agent variant.** The existing user-primary helper handled OWS signing correctly (line 4768-4772) but was hardcoded to `/api/user/wallet/external/dw-approvals/*`, returning 401 for per-agent SDK API keys. New `agent_id` keyword arg routes to the per-agent endpoint pair `/api/user/agent/{id}/wallet/external/dw-approvals/{prepare,submit}` shipped in SIM-1906, server reading state from `user_agent_wallets` instead of `users` and flipping the per-agent `approvals_set` flag on relayer success. Same idempotent contract — `already_set=True` when on-chain is already complete. Default behavior (no `agent_id`) is unchanged. Unblocks per-agent Elite OWS users from completing missed approvals when Polymarket adds spender contracts post-activation; the dashboard wizard can't sign for these wallets because the OWS vault lives on the agent's host machine, not the user's browser.

## [0.17.17] — 2026-05-22

### Added

- **OWS-wallet users can now trade Polymarket deposit-wallet markets (V2 sig-type-3).** Added `build_and_sign_order_v2_dw_ows` — a V2 POLY_1271 / ERC-7739 order signer that uses OWS upstream's `sign_typed_data` for the inner ECDSA. Mirrors the existing raw-private-key V2-DW path (`_build_and_sign_order_v2_dw`) byte-for-byte except at the signing call. Empirically verified: OWS returns 65-byte signatures with v ∈ {27, 28} (Solady ECDSA in the deposit-wallet contract requires this; v=0/1 returns 0x0 and rejects). New function asserts the v range at runtime to fail-closed if a future OWS upstream version changes encoding. Per-agent Elite OWS cohort can now trade on Polymarket — Herman's blocker since the 2026-04-28 V1 retirement is cleared.

### Fixed

- **`_coerce_typed_data_uints` now recurses into nested EIP-712 structs.** The prior top-level-only sweep missed Polymarket V2 deposit-wallet orders, where the load-bearing uints (`tokenId`, `makerAmount`, `takerAmount`, `salt`, `timestamp`) live under `message.contents.*` as the inner Order struct. OWS Rust parser rejected with "uint decimal value '...' exceeds u128 range" on large `tokenId`. V1 Order envelopes are unaffected (no nested structs → recursion is a no-op → identical output).
- **Preflight removes `POLYMARKET_SIGNER_UNSUPPORTED` blocker** — the underlying constraint no longer applies now that OWS+DW V2 signing is supported. Also fixes `POLYMARKET_APPROVALS_MISSING` warning to check approvals on the **deposit wallet** address (the funder) for DW-active users, not the EOA. Without this fix, `ok_to_trade=true` could return while the DW lacked CLOB allowances → real trade failure at submission.
- **`_execute_polymarket_byow_trade` OWS branch routes through the new V2 path** when `uses_dw=true`, with a defensive SIM-1646 hard-fail if `holder_address` indicates a pre-DW EOA-held position (theoretically impossible for per-agent OWS users but defensive against migrations, manual transfers, server state drift).

### Internal

- Codex consult on the implementation spec surfaced 4 P1 + 6 P2 design findings before any code was written; all P1s addressed in spec v2, then re-consulted with verdict CLEAN. Full design record at `_dev/active/_v2-ows-dw-signing/spec.md`.

## [0.17.16] — 2026-05-22

### Fixed

- **`client.preflight()` correctly blocks `ok_to_trade` for OWS+DW+Polymarket when preflight is the first SDK call (SIM-2325).** The 0.17.15 fix read `self._uses_deposit_wallet`, which defaults `False` at `__init__` and is only populated by `_ensure_wallet_linked()` in the trade path. In Herman's production sequence (`from_env()` → `preflight()` → `trade()`), `_ensure_wallet_linked()` had not run, so the blocker silently didn't fire and `ok_to_trade` remained `True`. Fixed to use the local `deposit_wallet` variable already fetched from `/api/sdk/agents/me` within `preflight()`, which is populated on every preflight call regardless of prior trade history.
- **`client.preflight()` adds `POLYMARKET_APPROVALS_MISSING` warning** when CLOB token approvals are missing for external-wallet (OWS without DW, private-key) Polymarket traders. Previously only surfaced as a log warning immediately before the first trade attempt; now visible in the preflight envelope for pre-flight gating.

## [0.17.14] — 2026-05-21

### Added

- **`client.preflight()` pre-trade readiness check (SIM-2237).** A canonical read-only preflight that composes identity / wallet / balance / exposure from existing SDK endpoints and returns a typed `PreflightResult` with `ok_to_trade`, `blockers[]`, `warnings[]`, and `client_preflight_id` for ledger correlation. Blocker codes: `EXPOSURE_CAP_EXCEEDED`, `WALLET_UNVERIFIED`, `VENUE_UNSUPPORTED`, `INSUFFICIENT_GAS`, `EXPOSURE_UNKNOWN` (fail-closed when positions fetch fails on a real-venue capped call). No new server endpoints — pure client-side composition. v0 scope per ticket discussion; v1 may add server-issued `preflight_id` once usage reveals what `ok_to_trade` actually needs to mean.
- **`PreflightResult` exported from top-level `simmer_sdk`** (alongside `TradeResult` family from v0.17.13).
- **Standalone `skills/preflight/` skill** for ClawHub — wraps `client.preflight()` with a CLI (supports `--json` + `AUTOMATON_MANAGED=1` for non-interactive ledger callers). 34 unit tests covering all blocker paths + SIM-2130 per-agent identity regression.

## [0.17.13] — 2026-05-21

### Fixed

- **`TradeResult`, `RealTradeResult`, `PolymarketOrderParams` now importable from top-level `simmer_sdk`.** Previously you had to `from simmer_sdk.client import TradeResult` while `SimmerClient` worked from the top-level — inconsistent. Surfaced by Herman dogfood ledger immediately after 0.17.12 upgrade. The fix is a 3-line re-export in `__init__.py` + entries in `__all__`; both import paths resolve to the same class.

## [0.17.12] — 2026-05-21

### Fixed

- **`TradeResult.shares_sold` now populates for sells (SIM-2238).** The Mintlify SDK reference already documented `result.shares_sold`, but the dataclass was missing the field and the server-response parser ignored the server's `shares_sold` value. Symptom from Herman dogfood: a SIM sell that filled 18.16 shares came back with `shares_bought=0, shares_sold=0, shares_requested=0` — no clean field to read filled shares from. Fixed in three response-build sites (live trade parser, paper-trading happy path, Kalshi BYOW happy path). `shares_bought` semantics unchanged; additive only.

### Added

- **`TradeResult.shares_filled` property** — direction-agnostic filled shares (returns `shares_bought` for buys, `shares_sold` for sells). Convenience for agents that don't want to branch on action.
- **`TradeResult.fully_filled` now works for sells.** Previously compared `shares_bought >= shares_requested`, so partial sells incorrectly evaluated as not-filled. Now compares against `shares_filled`.

## [0.17.11] — 2026-05-18

### Fixed

- **`set_approvals()` / `ensure_approvals()` return a friendly no-op for managed-wallet users (SIM-1976).** Previously both methods raised `ValueError: No wallet configured. Initialize client with private_key.` whenever the SDK had no local signing key — but managed-wallet users (Simmer custodies the key) have no private key to provide. The error told them to do something they can't do. The methods now probe `/api/sdk/settings`; when `wallet_ownership == "native"`, they return a structured `{managed: True, ...}` response with a message pointing at the server-side activation cascade (which auto-fires on the next Polymarket trade — see PR simmer#887). External-wallet users with no key configured still get the existing `ValueError` — no behavior change for them. Reported via Hannes Altberg's case (support thread 6ef49e7a).

## [0.17.10] — 2026-05-17

### Fixed

- **`set_approvals()` now generates the full 12-tx V2 set (was 8) — SIM-1881.** The server's `check_approvals()` adds 4 allowance checks introduced in the 2026-05-01 Polymarket upgrade (FeeEscrow + CTF redemption adapters); the SDK was never updated, so EOA users running `set_approvals()` got `ensure_approvals().ready == False` even after every transaction succeeded. Reported via SIM-1870 (Hannes Altberg). New txs: pUSD→V2_FEE_ESCROW, CTF→CTF_COLLATERAL_ADAPTER, CTF→NEG_RISK_CTF_COLLATERAL_ADAPTER, pUSD→CTF_COLLATERAL_ADAPTER.
- **Allowance lookup keys now use the full lowercased address (was 8-char prefix) — SIM-1881.** `V2_NEG_RISK_EXCHANGE_A` and `_B` both start with `0xe2222d`, so the prefix-based key in `get_missing_approval_transactions()` and `format_approval_guide()` was collision-prone. The server switched to full-address keys earlier in May; the SDK now mirrors that format. Result: previously-set allowances that were incorrectly reported as missing in the SDK's view are now correctly recognized.
- **`get_required_approvals()` mirrors `get_approval_transactions()` (8 → 12 V2 entries).** Metadata API was returning 8 entries while the transaction/missing-check paths required 12. Callers using the exported discovery API would have seen incomplete V2 approval requirements. Codex P2 follow-up to the primary fix.

### Added

- **`redemption_spenders()` helper in `polymarket_contracts`.** Returns the 2 CTF adapter contracts (`CTF_COLLATERAL_ADAPTER`, `NEG_RISK_CTF_COLLATERAL_ADAPTER`) that need ERC1155 `setApprovalForAll` for adapter-routed `redeemPositions`.
- **3 new contract constants in `polymarket_contracts`:** `V2_FEE_ESCROW`, `CTF_COLLATERAL_ADAPTER`, `NEG_RISK_CTF_COLLATERAL_ADAPTER`. Addresses match the server-side canonical constants.

## [0.17.9] — 2026-05-13

### Fixed

- **`set_approvals()` now detects deposit-wallet users and routes to the dashboard.** Previously the function silently set EOA approvals for POLY_1271 deposit-wallet users — approvals that had no effect because collateral lives in the deposit wallet, not the EOA. The function now short-circuits with a structured response (`deposit_wallet_user: True`, `set: 0`, `skipped: 0`, `failed: 0`) and a message directing users to the dashboard's "Activate Trading" EIP-712 flow. No transactions are submitted, no eth-account import attempted. Backward-compatible: existing callers that only check `set/skipped/failed` keys continue to work unchanged; callers wanting to branch on the DW case can check `result.get("deposit_wallet_user")`. SIM-1613.

## [0.17.8] — 2026-05-12

### Added

- **`[ows]` optional extra — one-command OWS install (SIM-1735).** New `pip install 'simmer-sdk[ows]'` pulls in `open-wallet-standard` (the Python bindings for the Open Wallet Standard) alongside the SDK. Eliminates the package-name vs import-name confusion (pip package is `open-wallet-standard`; import is `ows`) that bit our first OWS canary on 2026-05-12. Existing users on `pip install open-wallet-standard` directly still work — the extra is additive, not a breaking change.

### Changed

- Install hints in `ows_utils.py`, `client.py`, and `simmer-wallet-setup/SKILL.md` now recommend `pip install 'simmer-sdk[ows]'` as the primary install path, with `pip install open-wallet-standard` shown as the direct alternative.

## [0.17.7] — 2026-05-12

### Added

- **`client.wrap_on_dw()` — headless USDC.e wrap for external-wallet DW users (SIM-1730).** External-wallet operators with stranded USDC.e on their Polymarket Deposit Wallet can now wrap to pUSD directly from the SDK without a browser session. Mirrors the `activate_polymarket_dw()` shape exactly (EIP-712 prepare → sign → submit). Supports both `WALLET_PRIVATE_KEY` (local signing) and OWS wallet. Idempotent: returns `wrapped=False, amount_units=0` immediately when the deposit wallet has no stranded balance.

  Return shape: `{"wrapped": bool, "amount_units": int, "calls_count": int, "success": bool}`.

  Surfaced by Track 5 of the external-wallet signer-picker workstream.

## [0.17.4] — 2026-05-10

### Fixed

- **Auto-redeem now works for external-wallet deposit-wallet users.** Closes the auto-redeem-gap that affected all 23 external+DW users / 57 agents on Polymarket. Previously, `client.redeem()` and `client.auto_redeem()` returned `failed` every cycle for these users with the cryptic message "External-wallet redemption for deposit-wallet users is not yet supported (Phase 2)" — funds were not at risk (the dashboard fallback worked), but the steady-state log noise was a real confidence hit.

  The position lives on the deposit-wallet contract, so `msg.sender` of the redeem call must be the DW. The legacy unsigned-tx path (which broadcasts from the user EOA) would revert. The fix routes external+DW callers through a new prepare/sign/submit flow that mirrors the dashboard wagmi shape in Python:

  1. `POST /api/sdk/dw-redeem/prepare` — server returns the EIP-712 WALLET batch typed data.
  2. SDK signs locally with `WALLET_PRIVATE_KEY` (or OWS-managed key).
  3. `POST /api/sdk/dw-redeem/submit` — server validates + relays via Polymarket with our builder HMAC + writes `real_trades`.

  Cohort detection (which path to use) reads `wallet_ownership` + `wallet_uses_deposit_wallet` from the cached `/api/sdk/agents/me` response (5-min TTL — same fetch already used for `auto_redeem_enabled`). Older servers that don't return cohort fields fall through to the legacy flow.

  - New module: `simmer_sdk.dw_redeem` (pure HTTP + signing helpers — `prepare_dw_redeem`, `sign_dw_redeem_typed_data`, `submit_dw_redeem`, `redeem_dw_external`).
  - Modified: `client.SimmerClient.redeem()` dispatches to `_redeem_external_dw` for the external+DW cohort.
  - Modified: `client.SimmerClient.auto_redeem()` shares the same cohort cache fetch.

  Requires server-side `/api/sdk/dw-redeem/{prepare,submit}` endpoints (shipped in the simmer monorepo PR landing alongside this release). On a server that predates those endpoints (404 on prepare), the SDK falls back to the legacy `/api/sdk/redeem` path — same behavior as before this release.

  Tracked in the simmer monorepo's `_dev/active/_polymarket-dw-phase-2/NEXT.md` (auto-redeem-gap row).

## [0.17.3] — 2026-05-09

### Fixed

- **GTC/GTD float underflow on clean 2dp sizes (codex P2 follow-up to 0.17.2).** `py_clob_client_v2.get_order_amounts` uses float-based `round_down(size, size_dec)` internally, which does `floor(size * 10**size_dec) / 10**size_dec`. A clean 2-decimal size like `2.30` is stored in IEEE-754 as `2.299999999999999822…`, so `floor(2.30 * 100) = floor(229.999…) = 229` — the user's 2.30-share order silently signs as 2.29. Same class of bug as the FAK/FOK SELL branch already fixed via Decimal-via-str.

  Replaces the `get_order_amounts` call in `_build_and_sign_order_v2_dw` GTC/GTD branch with a Decimal-pure mirror of its algorithm. `Decimal(str(size)).quantize(size_q, ROUND_DOWN)` preserves user intent (`str(2.30) == '2.3'` → quantized to 2dp = `2.30`, not `2.29`). The Decimal product `size × price` always has at most `price_dec + size_dec` decimals (= `amount_dec` for all standard ticks), so the overflow path of the canonical algorithm is unreachable and skipped.

  Caught by codex review on the sibling simmer monorepo PR (`simmer/simmer_v3/polymarket_v2_signing.py:_build_and_sign_order_v2_dw`, PR #647) — same canonical-helper swap, same underflow, same fix.

  SIM-1666.

## [0.17.2] — 2026-05-09

### Fixed

- **GTC/GTD off-tick rejection on deposit-wallet signing path (SIM-1666 follow-up).** `_build_and_sign_order_v2_dw` GTC/GTD branch now delegates to `py_clob_client_v2.OrderBuilder.get_order_amounts` — the canonical precision helper Polymarket V2's CLOB validates against. Previously, polynode's `compute_amounts` did `round(size * 1e6)` without first flooring `size` to `RoundConfig.size`, producing 6-decimal `maker`/`taker` integers whose derived effective price (`maker / taker`) drifted off the tick grid and triggered `Price (X) breaks minimum tick size rule: Y` upstream. 0.17.1's `round_price_to_tick()` quantised the input price but couldn't fix the maker/taker ratio Polymarket recomputes. Affected wallets that had already upgraded to 0.17.1 (rjreyes / mt_1200, 327 hits over 2026-05-07–09 even on the latest SDK).

  The canonical helper does `raw_taker = round_down(size, size_dec=2)` → `raw_maker = raw_taker × raw_price` → `round_down(raw_maker, amount_dec)` if needed. With size pre-floored, the resulting integers are tick-aligned by construction, so both Polymarket's CLOB and Simmer's pre-submit precision gate (`local_dev_server.py:19443`) accept them without further work. Replaces the SIM-1620 post-hoc share-side floor (no longer needed; the canonical path produces clean amounts naturally).

  The fix only touches the GTC/GTD branch of the POLY_1271 (deposit-wallet) path. FAK/FOK BUY (Decimal cents-aligned), FAK/FOK SELL (Decimal pre-floor + compute_amounts), and the non-DW V2 path (already canonical) are unchanged.

  Worked example (price=0.97, size=5.6701030927835, tick=0.001):
  - polynode (broken): `maker=5499999` (6dp ❌), `taker=5670103` (6dp ❌), effective price 0.9699998… off-tick
  - canonical (fixed): `maker=5499900` (4dp ✓), `taker=5670000` (clean ✓), effective price 0.97 exact ✓

  Reported by rjreyes / mt_1200 2026-05-09. SIM-1666.

## [0.17.1] — 2026-05-08

### Fixed

- **Price not on tick grid (SIM-1666).** Added `round_price_to_tick(price, tick_size)` helper using `Decimal.quantize(ROUND_HALF_UP)`, applied at the entry of `build_and_sign_order()` and `build_and_sign_order_ows()` so all V1/V2/DW/OWS paths quantise the raw input price before reaching the CLOB. Fixes ~25+ buy rejections per cycle on rjreyes/weather-trader999 with errors like `Price (0.9690009744) breaks minimum tick size rule: 0.001`. Insufficient on its own for the deposit-wallet GTC path — see 0.17.2 for the maker/taker ratio drift fix.

## [0.17.0] — 2026-05-08

### Added

- **Dual-wallet position listing for deposit-wallet users (SIM-1646).** For users with `wallet_uses_deposit_wallet=True` who have pre-migration positions on their owner EOA, `client.get_positions()` now returns ALL positions — from both the EOA and the deposit wallet — each tagged with a `holder_address` field indicating which on-chain address holds the CTF tokens.

  Previously these users only saw positions held by the deposit wallet, making any pre-migration position invisible to their agent. Confirmed root case: rjreyes had 53 open positions on his EOA that his agent could not see or manage stop-losses on, leading to 190/199 failed trade attempts.

- **Per-trade sell routing by holder (SIM-1646).** `client.trade(action='sell', ...)` and stop-loss execution paths now select the signing method on a per-trade basis based on `holder_address`:

  - **holder == EOA** (pre-migration position): signs as sig-type-0 (V2 EOA-direct). The existing pre-DW signing path.
  - **holder == DW** (post-migration position): signs as sig-type-3 (POLY_1271 batch via deposit wallet). The existing post-migration path.
  - **Non-DW users**: zero behavior change — always sig-type-0 as before.

  The routing decision is per-trade, not per-session, so users with a mix of EOA and DW positions are handled correctly within the same agent run.

- **`holder_address` field on `Position` dataclass.** New optional `str` field. `None` for sim-venue positions and server versions predating SIM-1646. Existing skill flows that don't read `holder_address` are unaffected.

  SIM-1646.

## [0.16.1] — 2026-05-07

### Fixed

- **GTC/GTD precision rejection on deposit-wallet (POLY_1271) signing path.** `_build_and_sign_order_v2_dw` GTC/GTD branch called `compute_amounts(size=raw_float)` which does `round(size * 1e6)` with no tick-aware rounding. For markets with `tick_size=0.001`, shares must be divisible by 10 (max 5 decimal places); a GTC BUY where `size = amount / price` (e.g. `5.547576...`) produced `taker_amount = 5547576` and Polymarket rejected with `takerAmount X.XXXXXX exceeds max 5 decimal precision`. The FAK/FOK BUY path (Decimal-based) was already correct as of 0.12.3 — this only affected GTC/GTD on deposit-wallet users on tick=0.001/0.01/0.1 markets.

  After this version, `taker_amount` (BUY) and `maker_amount` (SELL) are floored to `10^(6-amount_decimals)` precision after `compute_amounts`, mirroring the round-down behavior `py_clob_client_v2.OrderBuilder.build_order` applies internally for the non-DW path. Effective price drift is well below 1 tick (~0.5ppm in the repro case) and conservative for the user (slight overpay on BUY, slightly fewer shares sold on SELL).

  Reported by rjreyes 2026-05-07 and mt_1200 2026-05-07.

  SIM-1620.

## [0.16.0] — 2026-05-06

### Changed

- **Auto-relink no longer silently replaces a managed wallet.** When `_ensure_wallet_linked` (the implicit per-trade auto-link path) detects a mismatch between the local key's address and the server's wallet, it now passes `confirm_replace_managed=false` to `/api/sdk/wallet/link`. The paired server-side guard (SIM-1580) rejects the request with a clear 4xx if the displacement would replace an existing managed wallet — instead of silently moving the managed wallet to legacy and oscillating the account state.

  Surfaced by wongc305@: account migrated from external to a managed deposit wallet via CTO one-off, but his bot env still had `WALLET_PRIVATE_KEY` set. Each trade triggered auto-relink → server flipped back to external with the old (Polymarket-blocklisted) address. ~85 trades over 5 days, 83 failed at Polymarket. Same failure mode would reproduce for every `/v2-setup-wizard` external→managed migration without bot reconfiguration.

  After this version, that misconfig produces a loud, actionable error in the bot's trade response: `"This account already has a managed wallet... remove WALLET_PRIVATE_KEY (and OWS_WALLET if set) from its environment and restart"`. No more silent oscillation.

  Explicit `client.link_wallet()` calls default to `confirm_replace_managed=True` (the user signalled intent to take self-custody), so legitimate managed→external switches still work without changes. Pass `confirm_replace_managed=False` explicitly if you want the safe default.

  SIM-1580 / paired with simmer/PR #559.

## [0.15.1] — 2026-05-06

### Fixed

- **NO-side sells silently failing on V2 neg-risk markets** when `client.trade(side='no', action='sell')` is called without an explicit `price`. The SDK previously derived the limit price from the V1 binary identity `1 - external_price_yes`, which is correct for V1 binary markets and non-neg-risk V2 binaries (CTF redemption keeps the two outcome tokens tightly arbitraged) but **wrong on V2 neg-risk markets** — there YES and NO are independent CLOB tokens with independent orderbooks, so the V1-derived price often sits far above the actual NO best bid. Result: GTC orders never matched and stoplosses sat pending until the market resolved. Reported by mt_1200 (weather-trader999) on 2026-05-02 ($5.54 NO position lost in Atlanta-64-65°F when stoploss tried to exit at price=0.999 ten+ times) and again on 2026-05-06 (multiple stuck stoploss exits).

  The SDK now queries the live orderbook via the new `/api/sdk/markets/{id}/executable-price` server endpoint and uses the returned best bid (SELL) or best ask (BUY) — with a one-tick buffer applied — for any Polymarket trade where `price` was not explicitly provided. The V1 fallback remains for the case where the executable-price endpoint is unreachable (older server, network hiccup) but is no longer the primary path.

  **Workaround on older versions**: pass an explicit `price=<actual_no_bid>` to `client.trade()` until you can upgrade.

  SIM-1560.

## [0.15.0] — 2026-05-06

### Added

- **POLY_1271 (signature_type=3) order signing for Polymarket deposit-wallet
  users.** Required for users who upgraded their EOA to a deposit wallet via
  the dashboard or `POST /api/user/wallet/external-upgrade-to-deposit-wallet`.
  The SDK auto-detects deposit-wallet users from the server settings response
  and uses the right signature type without configuration changes — bots that
  upgrade `simmer-sdk` keep working without code changes.

  The wrapped signature is 317 bytes (ERC-7739 TypedDataSign envelope: inner
  EIP-712 sig + appDomainSeparator + contentsHash + orderTypeString +
  typeStringLength). Wrapping handled by `polynode>=0.10.3`; we delegate the
  byte layout rather than hand-roll it.

  Caller-facing surface: `build_and_sign_order(signature_type=3,
  deposit_wallet_address=...)`. Existing sig type 0 (EOA) callers are
  unchanged. Sig type 3 requires `deposit_wallet_address`; raises
  `ValueError` if missing.

  Refs SIM-1521 / parent SIM-1515.

### Changed

- New dependency: `polynode>=0.10.3`.

## [0.14.2] — 2026-05-04

### Fixed

- **`Gas limit too high (XXX), max 500000`** on external-wallet auto-redeem
  for the new pUSD collateral adapters added in 0.13.3. The 500k cap was
  set when redemption hit `CTF.redeemPositions` directly (~150-300k). The
  new `CtfCollateralAdapter` / `NegRiskCtfCollateralAdapter` do extra
  on-chain work (USDC.e wrap → CTF burn → pUSD mint) that legitimately
  consumes 500-1000k gas. weather-trader999 reported a wave of failures
  with `eth_estimateGas`-derived budgets of 502k–956k getting clipped.
  Cap raised to 1.5M, which gives ~50% headroom for the heaviest adapter
  calls while still guarding against pathological estimates (1.5M @
  ~30 gwei ≈ 0.045 POL ≈ $0.01 worst case).

  External-wallet users on `0.13.3` / `0.14.0` / `0.14.1` need
  `pip install --upgrade simmer-sdk` to pick this up. Note: a parallel
  server-side fix landed in simmer PR #491 — `/api/sdk/wallet/broadcast-tx`
  had the same legacy whitelist gap and rejected adapter txs; the
  dashboard ships that automatically.

## [0.14.1] — 2026-05-04

### Added

- **External-wallet auto-recovery from stale CLOB credentials.** When
  Polymarket rejects a trade with `Unauthorized` / `Invalid api key` (most
  often after Polymarket rotates server-side creds, as happened during
  the 2026-04-28 V2 cutover), `client.trade()` now resets its internal
  `_clob_creds_registered` cache, re-runs `_ensure_clob_credentials()`
  (which derives locally with the user's private key / OWS wallet and
  re-registers with Simmer), and retries the trade once. Single retry
  only — if the retry also fails the original error is surfaced.

  The simmer server clears its cached encrypted creds on the same
  condition (`scripts/local_dev_server.py` external-wallet path), so the
  re-derive's server-side existence check returns false and forces a
  fresh derive. Previously only the managed-wallet path had this
  recovery (line ~19940 of the same file); external wallets sat in a
  silent retry loop, last seen 2026-05-04 with 4 wallets stuck on the
  Polymarket V2 cutover with 0 successes in 6h.

  Managed wallets unaffected (server-side recovery already exists).
  Sim/Kalshi venues unaffected.

## [0.14.0] — 2026-05-04

### Added

- **`client.trade()` now logs failures at `WARNING`.** When a real-trading
  trade returns `success=False` with an `error`, the SDK emits
  `logger.warning("Trade failed on <venue>: <error>")` before returning the
  result. Bots that don't explicitly check `result.success` previously
  looped silently when upstream venues rejected orders (observed
  2026-05-04: a user's bot logged 72 failed Polymarket trades over 17h
  without a single visible signal because the harness only printed on
  exception). Bots that already check `result.success` see no behavior
  change beyond the extra log line. Suppress with
  `logging.getLogger("simmer_sdk.client").setLevel(logging.ERROR)`.

### Changed

- **`skills/simmer/SKILL.md`** trade example now demonstrates the
  `if not result.success` check that idiomatic bots should perform —
  catches the same class of silent-failure bug at the documentation
  layer for LLM-driven agents loading the skill.

## [0.13.3] — 2026-05-04

### Fixed

- **External-wallet auto-redeem failed with `Unsigned tx targets unknown
  contract`.** Server-side SIM-1389/1421 (shipped 2026-05-03) routes
  redemption through the new Polymarket collateral adapters
  (`0xAdA100…` for binary, `0xadA200…` for neg-risk) so payouts land in
  pUSD instead of USDC.e. The SDK's pre-flight contract whitelist on
  `redeem()` only knew the legacy CTF + NegRiskAdapter addresses, so it
  rejected every server-built unsigned tx targeting the new adapters.
  Both new adapters expose the same selector (`0x01b7037c`) and ABI as
  the legacy binary CTF, so the fix is a whitelist add — no signing
  changes. Legacy entries stay in place so older server versions still
  verify.

  External-wallet users on `< 0.13.3` will keep hitting this until they
  upgrade. Managed-wallet users were never affected (server signs +
  broadcasts itself, no client-side validation).

## [0.13.2] — 2026-05-01

### Fixed

- **CLOB credential derivation falls back to the Simmer relay when
  Polymarket's `/auth/api-key` route is Cloudflare-blocked from the
  user's IP** (commonly residential AU / SE Asia ranges). Previously,
  external-wallet users on blocked networks would land at
  `has_credentials=false` with no recovery path — the SDK would log a
  warning and trades would fail with `Missing Polymarket API
  credentials`.

  The new flow: `_ensure_clob_credentials()` first attempts the local
  derive (`py_clob_client.create_or_derive_api_creds()` for raw-key,
  `ows_derive_clob_creds()` for OWS). If that raises (network error,
  HTTP 403, etc.), the SDK falls through to a new private method
  `_derive_creds_via_proxy()`. It builds the L1 auth headers locally —
  the user's private key never leaves their machine — and POSTs only
  those headers to a new Simmer endpoint
  (`POST /api/sdk/wallet/credentials/derive-via-proxy`), which forwards
  to Polymarket from a non-blocked IP and stores the resulting creds.

  No user action required; the fallback is transparent on first trade.

- **`client.link_wallet()` now derives + registers CLOB credentials
  after a successful link.** Before, calling `link_wallet()` on a
  user whose wallet had been migrated managed→external left the user
  in a state where `linked_wallet_address` was set but
  `polymarket_api_creds_encrypted` was null — the next trade would
  fail with `Missing Polymarket API credentials` and re-running
  `link_wallet()` would short-circuit on "already linked" without
  fixing it. The link flow now resets `_clob_creds_registered` and
  calls `_ensure_clob_credentials()` (which goes through the new
  proxy fallback if the direct derive is CF-blocked).

## [0.13.1] — 2026-05-01

### Docs

- **`amount` parameter currency disambiguation across SDK docstrings.**
  Per CLAUDE.md currency-formatting rule (`$SIM` for sim venue, `USDC`
  for real venues), the `amount` parameter docstrings on `client.trade()`
  and the internal Polymarket/Kalshi execution methods previously read
  `Dollar amount to spend`, which is ambiguous for `venue='sim'`. Updated:

  - `client.trade(amount=...)` (top-level): now `Amount to spend (for buys)
    — USDC for polymarket/kalshi, $SIM for sim`
  - `prepare_polymarket_order(amount=...)`: now `USDC amount to spend`
    (Polymarket-only path)
  - `_build_signed_order(amount=...)`: now `USDC amount (for buys)`
    (Polymarket-only path)
  - `_execute_kalshi_byow_trade(amount=...)`: now `USDC amount (for buys)`
    (Kalshi-only path)

  Behavior is unchanged. Follow-up to SIM-1252.

## [0.13.0] — 2026-05-01

### Added

- **`SimmerClient.from_env()` and `SimmerClient.with_ows_wallet()`
  classmethods.** Two ergonomic constructors so callers never have to read
  `os.environ` directly.

  ```python
  from simmer_sdk import SimmerClient

  # Reads SIMMER_API_KEY from env. Auto-detects WALLET_PRIVATE_KEY (external
  # EVM wallet) and OWS_WALLET (OWS-managed wallet) via the regular __init__
  # path. Raises RuntimeError with a dashboard pointer if SIMMER_API_KEY is
  # unset.
  client = SimmerClient.from_env()

  # Explicit OWS routing — pass the wallet name directly. api_key falls back
  # to SIMMER_API_KEY env when None.
  client = SimmerClient.with_ows_wallet("my-agent-wallet")
  client = SimmerClient.with_ows_wallet("my-agent-wallet", api_key="sk_live_...")
  ```

  Both methods forward extra kwargs (`venue`, `base_url`, `live`, etc.) to
  the regular constructor, so any existing usage pattern is reachable
  without going through `__init__` directly.

  This is sugar over the existing `SimmerClient(api_key=..., ...)`
  constructor — no change in client behavior, just a cleaner construction
  surface for skill bundles and bots that want to keep `import os` out of
  their entrypoints.

## [0.12.3] — 2026-04-30

### Fixed

- **Polymarket V2 FAK/FOK BUY: maker amount precision rejection on
  non-cent-aligned prices.** `client.trade(action="buy",
  order_type="FAK")` was producing `makerAmount` values with
  4–5 decimals of USDC precision (e.g. `$5.99767` for a `$6.00` BUY
  on a `tick_size=0.001` market). Polymarket CLOB enforces "FAK/FOK
  maker max 2 decimals" and rejected these orders with `Order
  rejected: invalid amounts, the market buy orders maker amount
  supports a max accuracy of 2 decimals`.

  The V2 path now routes FAK/FOK orders through Polymarket's
  canonical market-order builder (`MarketOrderArgsV2` →
  `OrderBuilder.build_market_order`), which rounds maker (USDC for
  BUY, shares for SELL) down to 2 decimals by construction across
  all tick sizes (`0.01`, `0.001`, `0.0001`). GTC/GTD limit orders
  continue using `OrderArgsV2` → `build_order` and preserve full
  `price × size = maker` precision (CLOB validates that exactly for
  limit orders).

  `build_and_sign_order(...)` gains an optional `amount_usdc` kwarg
  (the original USDC dollar amount for FAK/FOK BUY) so the signed
  maker matches what the caller asked for, not a derived
  `size × price` which can shave a cent under float drift.
  `client._execute_polymarket_byow_trade` plumbs it through
  automatically — most callers do not need to touch this.

  V1 signing path was already correct (post-hoc 2-dec rounding step
  has been there since Feb 17). OWS path remains V1-only and is
  out of scope for this fix; tracked separately in the
  wallet-custody-migration workstream.

  External-wallet users on V2 (default since 2026-04-28 cutover)
  hitting precision rejections on non-cent-aligned prices should
  `pip install -U simmer-sdk` to pick up 0.12.3.

## [0.12.2] — 2026-04-27

### Fixed

- **`is_v2_enabled()` default is now time-gated on the Polymarket V2
  cutover (2026-04-28 11:00 UTC).** Versions 0.10.0–0.12.1 defaulted
  to V2 unconditionally, which signed V2-shaped orders against the
  still-active V1 CLOB pre-cutover and got back
  `{"error": "order_version_mismatch"}`. Affected external-wallet
  users on `simmer-sdk 0.10.0`–`0.12.1` should `pip install -U
  simmer-sdk` to pick up 0.12.2.

  The new default signs V1 before the cutover instant and V2 from
  that instant onward — same installed binary, no upgrade or env-var
  change needed at cutover. The `SIMMER_POLYMARKET_EXCHANGE_VERSION`
  env override (`v1` / `v2`) still wins over the time gate for
  testing or break-glass.

  Managed-wallet users (no `WALLET_PRIVATE_KEY` / `OWS_WALLET` set)
  were unaffected — the SDK forwards their requests to the server,
  which signs server-side based on its own Railway flag. This fix
  only changes behavior for external-wallet (locally-signed) flows.

## [0.12.1] — 2026-04-25

### Fixed

- **OWS wallet trades no longer require Elite-tier per-agent wallet
  registration.** When `OWS_WALLET` was set, `client.trade()` was
  unconditionally injecting `wallet_address` into the request payload,
  forcing the server through the per-agent-wallet validation path
  (`user_agent_wallets` lookup) and rejecting any unregistered wallet
  with `"Agent wallet not found or not owned by you"`. This locked out
  OWS-configured users at Free/Pro tiers and Elite users who hadn't
  gone through the dashboard registration flow. Standard external
  wallets using `WALLET_PRIVATE_KEY` were unaffected.
- The SDK now checks `/api/sdk/agent-wallets` once per client lifetime
  and only injects `wallet_address` when the OWS wallet has a matching
  registration row. Otherwise the trade falls through to the
  user-account-level `linked_wallet_address` path, which works at any
  tier. Per-agent isolation remains an opt-in feature for users who
  explicitly register via dashboard or `register_agent_wallet()`.

## [0.12.0] — 2026-04-25

### Removed

- **`simmer_sdk.risk` module** (entire module — `DrawdownController`,
  `DrawdownState`, and the `from simmer_sdk import DrawdownController`
  top-level export) — withdrawn one day after 0.11.0 with no known
  adopters. The intended use case is already addressed by the agent
  profile PnL chart (whose peak equals the peak this class tracked),
  and platform-level auto-halt was never appropriate to ship as an SDK
  primitive — silent agent halts are a worse UX than the rare cascading
  loss they would catch. Skills that want a portfolio drawdown halt
  should compute it from `SimmerClient.get_briefing()` portfolio values
  directly. No server-side replacement is planned.
- **`simmer_sdk.execution` module** (entire module — `await_fill`,
  `FillStatus`, `FillResult`, `clob_poll_fn`, `clob_cancel_fn`) —
  withdrawn one day after 0.11.0 with no known adopters. The wrapper
  only applied to GTC/GTD orders; Simmer skills default to `FAK`
  (Fill-And-Kill), which the exchange auto-cancels at submission, making
  the wrapper a no-op for the common case. Skills with a genuine
  GTC wait-and-cancel requirement should inline a short poll loop tuned
  to their own strategy — shared defaults across strategies were the
  wrong abstraction.

If you imported either module from 0.11.0 or 0.11.1, pin to `0.11.1` or
migrate per the guidance above. Future replacements (if any) will be
introduced only when a concrete first-party skill has adoption
requirements driving them.

## [0.11.1] — 2026-04-24

### Added

- **`SimmerClient.ensure_can_trade(min_usd, venue, safety_buffer)`** —
  collateral-agnostic balance pre-flight helper for trading skills. One
  status fetch replaces many failed trade round-trips when a wallet is
  underfunded. Reads pUSD on V2 (post-2026-04-28 cutover), USDC.e on V1,
  per the server's `exchange_version`. Returns a stable `{ok, balance,
  collateral, exchange_version, reason, max_safe_size}` dict so skills
  can skip cleanly and cap per-run size to `balance × (1 − safety_buffer)`
  (default 2% buffer for fees / slippage). See
  https://docs.simmer.markets/sdk/risk#balance-pre-flight—client-ensure-can-trade.
  Refs SIM-1063.
- Integrated `ensure_can_trade()` into all 8 first-party Polymarket
  trading skills (copytrading, fast-loop, mert-sniper, signal-sniper,
  weather-trader, elon-tweets, ai-divergence, nothing-ever-happens).
  Underfunded skills now emit a clean automaton skip report
  (`skip_reason="insufficient_balance"`) instead of looping on rejected
  orders. Expected to eliminate ~78% of current skill failures caused
  by underfunded-wallet retry loops.

## [0.11.0] — 2026-04-24

### Added

- **`simmer_sdk.risk`** — new module for portfolio-level risk
  primitives.
  - **`DrawdownController`** — stateful peak-trough tracker with sticky
    auto-halt. Bot calls `update(new_bankroll)` after every realized
    PnL event and `can_trade()` before every new order. Halts at a
    caller-configured `max_drawdown_pct` (default 15%); halt is sticky
    until the operator explicitly calls `resume()`. Distinct from the
    per-trade simulate-before-execute guardian — this is portfolio-level
    and time-invariant. Refs SIM-1072.
- **`simmer_sdk.execution.await_fill()`** — execution-time partial-fill
  wait wrapper with time-boxed escape (SIM-1079). Polls an open limit
  order's `size_matched` and returns one of four terminal statuses:
  `FILLED`, `PARTIAL`, `TIMEOUT_PARTIAL`, `TIMEOUT_NO_FILL`. All
  thresholds (`accept_pct`, `partial_exit_pct`,
  `partial_exit_time_frac`, `poll_interval`) are caller-configurable;
  defaults are 0.95 / 0.50 / 0.70 / 2.0s. Handles cancel-failure and
  transient poll errors gracefully. Opt-in — `client.trade()` is
  unchanged. See https://docs.simmer.markets/sdk/execution.
- **`simmer_sdk.execution.clob_poll_fn` / `clob_cancel_fn`** —
  one-line wiring helpers for `py_clob_client.ClobClient`.

## [0.10.0] — 2026-04-28

### Polymarket V2 migration support

Polymarket cuts over to V2 on **2026-04-28 ~11:00 UTC**. V2 uses **pUSD**
(1:1 wrapper around USDC.e) as exchange collateral and introduces a new
order struct. See https://docs.simmer.markets/v2-migration.

**0.10.0 defaults to V2.** To pin V1 temporarily (rare — V1 CLOB is
retired), set `SIMMER_POLYMARKET_EXCHANGE_VERSION=v1` env, or pin
`simmer-sdk<0.10.0`.

### Added

- **`simmer_sdk.polymarket_contracts`** — new module mirroring the
  server-side contract registry. Exports `is_v2_enabled()`,
  `active_spenders()`, `collateral_token()`, `exchange_version_str()`,
  V1/V2 addresses, and CollateralOnramp/Offramp. Use these instead of
  hardcoding addresses.
- **`simmer_sdk.approvals`** — flag-aware approval tx generation.
  - V2: 4 V2 spenders × pUSD + CTF = 8 approvals
  - V1 (if pinned): 3 V1 spenders × (USDC + USDC.e) + CTF = 9 approvals
- **`build_and_sign_order()`** — now dispatches to V2 path (via
  `py-clob-client-v2`) when flag on, V1 path otherwise. New optional
  `builder_code` and `metadata` args for V2 attribution. `fee_rate_bps`
  arg kept for V1 compat but ignored on V2 (fees are match-time,
  not embedded in the signed order).
- **`SignedOrder`** — now supports both V1 and V2 shape via optional
  fields. `to_dict()` emits only the fields relevant to each version.
  Adds `exchange_version` meta field (`"v1"` or `"v2"`).

### Changed

- **Polymarket collateral** — server-signed trades through
  `client.place_order()` route via pUSD post-cutover (no SDK code
  change needed — the backend handles it via flag).
- **Wallet status response** (from `client.get_wallet_status()` and
  friends) — now includes `balance_pusd`, `balance_usdc_bridged`, and
  `spendable_pusd_balance` fields. Use `spendable_pusd_balance` (raw
  pUSD × (1 − fee buffer, default 5%)) to size orders on V2 — leaves
  headroom for the 2-5% match-time fee that V2 charges but doesn't
  embed in the signed order.
- **V2 "insufficient balance" errors** on `client.place_order()` now
  point to the migration URL if the user still holds USDC.e
  post-cutover.

### Dependencies

- Added `py-clob-client-v2>=1.0.0` (V2 signing path). V1 deps
  `py-clob-client` and `py-order-utils` retained for flag-off users.

### Migration notes

**Server-signed paths** (managed wallets, SDK keys — the default):
Just `pip install -U simmer-sdk`. Trades route through pUSD
automatically post-cutover — no code change required.

**External wallet paths** (you build orders locally via
`build_and_sign_order()`): upgrade to 0.10.0, call
`get_approval_transactions()` to get the V2 spender set, optionally
mint a V2 builder code at
[polymarket.com/settings?tab=builder](https://polymarket.com/settings?tab=builder)
and pass it as `builder_code` (or set `POLY_BUILDER_CODE` env).

**Direct Polymarket CLOB users** (bypassing simmer-sdk): see the
[Integrator section](https://docs.simmer.markets/v2-migration#for-integrators)
of our migration guide.

## [0.9.21] — 2026-04-07

### Added
- **`simmer_sdk.sizing`** — Kelly Criterion + Expected Value position sizing for binary prediction markets.
  - `size_position()` — dollar amount to trade, returns `0.0` when edge is below `min_ev` so skills can simply skip.
  - `kelly_fraction()`, `expected_value()` — raw primitives.
  - `SIZING_CONFIG_SCHEMA` — drop-in `CONFIG_SCHEMA` fragment exposing `SIMMER_POSITION_SIZING`, `SIMMER_KELLY_MULTIPLIER`, `SIMMER_MIN_EV` env vars.
  - Default is fractional Kelly (0.25x) to prevent overbetting. Sourced from research on top Polymarket traders (SIM-370).
- **`auto_redeem()`** wired into all official trading skills — winning Polymarket positions are now claimed automatically each cycle.

### Changed
- **`GammaClient` removed from the SDK.** The Polymarket Gamma helper has been relocated into the `polymarket-ai-divergence` skill (its only consumer). The SDK is scoped to the Simmer API surface plus universal primitives every skill needs (sizing, auth, error handling); third-party API helpers belong with the skills that use them. If you need Polymarket metadata directly, hit `https://gamma-api.polymarket.com/` from your skill — see `skills/building.mdx`.

### Docs
- README "Skill Builder Utilities" section covering `simmer_sdk.sizing`.
- New Mintlify page at `docs.simmer.markets/sdk/position-sizing`.
- `skills/building.mdx` "Recommended primitives" section pointing skill authors at `simmer_sdk.sizing`, with a note about external market data sources.

## [0.9.20] — Prior release

- `import_kalshi_event()` for bulk Kalshi event import.
- Tightened `py-order-utils` and `py-clob-client` minimum versions.
- Removed deprecated `get_skill_config` / `apply_skill_config`.
- Volatility targeting in `polymarket-weather-trader`.
