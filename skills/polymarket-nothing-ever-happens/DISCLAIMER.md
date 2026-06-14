# Disclaimer

This skill is a **framework**, not a production trading system. Read this
in full before connecting it to a wallet with real funds.

## No financial advice

Nothing in this skill constitutes financial, investment, or trading
advice. The default strategy implemented here is a starting point, not a
tested edge. Suitability for any account size or risk tolerance is your
responsibility to assess.

## Default parameters are not validated

Default parameters are calibrated for testing the plumbing, not for live
profit. They have not been validated to produce positive returns under
current market conditions. Run paper mode for an extended period before
scaling beyond default position sizes.

## Automated trading carries irreversible risk

When this skill runs with `--live`, it places real on-chain orders.
On-chain trades cannot be recalled. Strategy errors, signal lag, market
regime shifts, and operator misconfiguration can produce losses
exceeding any specific position size.

## Risk monitoring may not apply to all market types

Stop-loss and take-profit monitors run on a fixed schedule. Markets that
resolve faster than the monitor cycle cannot be exited automatically.

Some markets also resolve by gapping rather than decaying. Weather
temperature buckets are the clearest case: the losing side can sit near
your entry, then jump straight to about 0 at resolution, with no
intermediate price for a percentage stop to trigger on and no liquidity
to exit into. On these markets a stop-loss cannot cap your loss
regardless of the monitor cycle.

Position sizing is the only reliable risk control on these markets. Set
it conservatively, assuming the full position can go to zero.

## Use of this skill is at your own risk

By installing and running this skill you agree that the authors are not
liable for any losses, direct or indirect, that arise from its use. This
applies regardless of skill provenance — official Simmer skills,
community skills, and skills imported from external repositories all
carry this same disclaimer.

## Where to learn more before going live

- The skill's own `SKILL.md` documents the strategy and parameters
- Your trading venue's documentation covers fee structure, order types,
  and resolution rules
- Simmer SDK documentation covers paper mode, dry-run flags, and
  position monitoring
