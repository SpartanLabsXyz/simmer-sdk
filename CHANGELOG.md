# Changelog

All notable changes to `simmer-sdk` are documented here.
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`simmer_sdk.regime` — realized-vol regime gate (SIM-1450).** A
  venue-agnostic primitive that lets a strategy declare which regime
  (range-bound vs trending) it is registered for, then skip entirely when
  the current realized volatility says we're in the wrong regime.

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

  Returns a `RegimeDecision` with `allowed` (bool), `realized_vol`,
  `regime` (`"trending"` / `"range_bound"`), `reason`
  (`"ok"` / `"regime_mismatch"` / `"insufficient_data"` / `"invalid_input"`),
  and `n_candles`. Fails closed when fewer than `lookback_candles` prices
  are supplied. Distinct from the empirical-Kelly haircut in
  `simmer_sdk.sizing` (SIM-1012): the gate is trade / no-trade, the
  haircut scales an already-allowed trade. See
  `examples/regime_gate_skill.py` for the canonical wiring pattern, and
  `REGIME_CONFIG_SCHEMA` for opt-in via `config.json` / env vars.

  Origin: stacyonchain noted that gating a 1¢-reversal strategy on the
  realized vol of the prior 12 candles turned trending-period losses into
  no-ops and meaningfully improved overall results.

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
