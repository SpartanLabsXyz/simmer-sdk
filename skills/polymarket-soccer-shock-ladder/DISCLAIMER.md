# Disclaimer

This skill is a **framework**, not a production trading system. Read this
in full before connecting it to a wallet with real funds.

## No financial advice

Nothing in this skill constitutes financial, investment, or trading
advice. The ladder strategy implemented here (a shock-fade based on
@RohOnChain's published FIFA framework) is a starting point, not a
guaranteed edge. Suitability for any account size or risk tolerance is
your responsibility to assess. Edges depend on spreads, fees, latency,
and market regime.

## Default parameters are not validated

The default per-shock stake and bucket filter are conservative starting
points calibrated for a small-size live test, not for proven profit.
They have not been validated to produce positive returns. Run in
**dry-run** (the default) and review what the skill *would* place before
ever passing `--live`, and keep position sizing small until the strategy
proves out for you.

## Automated trading carries irreversible risk

With `--live`, this skill places real on-chain limit orders on Polymarket.
On-chain trades cannot be recalled. Each shock places up to four resting
buy orders; unfilled rungs are cancelled at the order TTL, but fills are
real exposure. Signal lag, thin books after a shock, partial fills, and
regime shifts can produce losses exceeding any single rung's size.

## Polymarket only

The ladder is a CLOB limit-order mechanic. The `$SIM` (LMSR) venue has no
order book, so `--venue sim` is a **plumbing smoke test only**. It cannot
replicate the strategy. Do not read $SIM behavior as a strategy validation.

## Use of this skill is at your own risk

By installing and running this skill you agree that the authors are not
liable for any losses, direct or indirect, that arise from its use. This
applies regardless of skill provenance: official Simmer skills, community
skills, and skills imported from external repositories all carry this same
disclaimer.

## Where to learn more before going live

- This skill's `SKILL.md` documents the strategy, the signal, and the parameters
- Polymarket's documentation covers fee structure, order types, and resolution
- Simmer SDK documentation covers dry-run, venues, and order management
