# AGENTS.md — Simmer SDK

Conventions for AI coding agents and reviewers working in this repo. This is the
**public Python SDK + official skills** for Simmer. See `CONTRIBUTING.md` for PR
hygiene and `README.md` for the client usage patterns. This file captures the
non-obvious rules a reviewer should enforce.

## This repo is public

No secrets, API keys, internal risk thresholds, scoring algorithms, or proprietary
strategy logic. Examples use `sk_live_...` / `sk_test_...` placeholders, never real keys.

## Currency formatting

- **Simmer (simulated) venue:** `$SIM` *suffix* — `100.00 $SIM`.
- **Real venues (Polymarket/Kalshi):** `$` *prefix* — `$100.00`.
- Never `$SIM100.00` or `$100.00 $SIM`.
- When a value or API field could be either, disambiguate by prefixing the field
  name with `sim_` or `usdc_`.

## `live` is a constructor arg, not a per-trade flag

`SimmerClient(..., live=False)` controls paper-vs-real execution for the whole client.
`live=False` previews against the simulated path and places **no real order**. Don't
add a per-call `live=` parameter to trade methods — flag any PR that does.

## Market discovery is recency-windowed

Unfiltered `get_markets()` returns the newest ~1,000 active markets, not the full set.
Code that needs broad coverage must filter (`sort="volume"`, `q=`, `tags=`). A loop
that assumes `get_markets()` sees every market is a bug.

## Wallet signing model

- The SDK signs **locally** — via OWS (`OWS_WALLET`) or a raw `WALLET_PRIVATE_KEY`.
  The Simmer server never holds or sees an external wallet's private key.
- Endpoints that need a browser session token (withdraw, set-allowances,
  place-order-server, wallet create) **cannot** be called from the SDK or an API key.
  SDK code must not pretend to; route users to the dashboard instead.
- DW (deposit-wallet) trading requires activation first (`activate_polymarket_dw`,
  and `activate_combo_dw` for parlays) before `trade()` / `place_combo()` will work.

## Examples must match real client signatures

Doc and skill code examples are bound against the shipped SDK by CI (`skill-example-bind`).
Every `client.<method>(...)` shown in `README.md`, `skills/**/SKILL.md`, or docstrings
must be a real method with real parameter names. Don't introduce illustrative-but-fake
calls — they break the bind gate.

## Skills

- The `simmer` overview skill's canonical source is `skills/simmer/SKILL.md` (the ClawHub
  publish home). Bump `metadata.version` when you change it. Downstream copies (e.g. the
  website's `skill.md`) are **generated** from it — never hand-edit a generated copy.
- User-contributed trading skills belong on ClawHub, not in this repo (see CONTRIBUTING).

## Versioning & publishing

Before republishing to PyPI or ClawHub, bump the version **above the currently published
latest** — a version at or below the live one is silently ignored by installers. Don't
assume the version in source is the published one; check the registry.

## Tests

`pytest tests/`. Keep PRs focused — one fix or one feature; don't mix unrelated changes.
