# Changelog

All notable changes to `simmer-sdk` are documented here.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.16.2] — 2026-05-07

### Fixed

- **`set_approvals()` now detects deposit-wallet users and routes to the dashboard.** Previously the function silently set EOA approvals for POLY_1271 deposit-wallet users — approvals that had no effect because collateral lives in the deposit wallet, not the EOA. The function now short-circuits with a structured response (`deposit_wallet_user: True`, `set: 0`, `skipped: 0`, `failed: 0`) and a message directing users to the dashboard's "Activate Trading" EIP-712 flow. No transactions are submitted, no eth-account import attempted. Backward-compatible: existing callers that only check `set/skipped/failed` keys continue to work unchanged; callers wanting to branch on the DW case can check `result.get("deposit_wallet_user")`. SIM-1613.

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
