# Disclaimer

This skill is a **framework**, not a production trading system. Read this
in full before connecting it to a wallet with real funds.

## No financial advice

Nothing in this skill constitutes financial, investment, or trading advice.
Suitability for any account size or risk tolerance is your responsibility to
assess.

## What "copyability screening" means

The auto-curated leader set is filtered using PolyNode's slippage-adjusted
copy P&L screen (`exclude_toxic=true`). This screen removes wallets whose
historical edge is likely to disappear when copied at scale (due to market
impact, slippage, and thin books).

**Copyability screening reduces slippage risk; it does not remove market risk.**
A screened wallet can still hold positions that resolve against you.
Past copy-PnL does not guarantee future copy-PnL.

## No performance or return claims

This skill makes no claims about win rates, return on investment, or expected
profit. Any such framing elsewhere is not an endorsement of this skill. Do
not make trading decisions based on metrics you cannot independently verify.

## Automated trading carries irreversible risk

With `--live`, this skill places real orders on Polymarket. On-chain trades
cannot be recalled. Rebalancing may result in buys and sells that change your
exposure. Signal delays, thin books, and regime shifts can produce losses.

## Sim-first is not optional — it is best practice

Validate all new configurations in `--venue sim` ($SIM mode) before going
live with real USDC. A sim run that produces no losses is not a guarantee of
a live run that produces no losses — but it is evidence that the skill's
plumbing and configuration work as expected.

## Leader churn risk

The curated leader set refreshes daily. Leaders active on one day may not
be in the set the next day. If the skill ran yesterday and a leader exits
a position today but is no longer in the set, the exit may not be mirrored.
Enable `WC_COPYTRADER_DETECT_EXITS=true` (default) to mitigate this
during leader transitions.

## World Cup scope

This skill targets World Cup markets only. Skill behavior outside the
tournament or after resolution depends on the contents of the leader
cache, which itself depends on the availability of WC markets on Polymarket.

## Use at your own risk

By installing and running this skill you agree that the authors are not
liable for any losses, direct or indirect, that arise from its use.
This applies regardless of skill provenance: official Simmer skills,
community skills, and skills imported from external repositories all
carry this same disclaimer.
