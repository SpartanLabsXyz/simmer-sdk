# Disclaimer

This skill is a **framework**, not a production trading system. Read this
in full before connecting it to a wallet with real funds.

## No financial advice

Nothing in this skill constitutes financial, investment, or trading
advice. The default strategy implemented here is a starting point, not a
tested edge. Suitability for any account size or risk tolerance is your
responsibility to assess.

## Default feeds and keywords are not validated

The skill ships with no default feeds, and nothing here has been validated
to find news that moves markets before the price does. If your agent trades
on these signals, run it in paper mode for an extended period first.

## This skill does not trade; your agent does

Since 2.0.0 the skill only surfaces article and market pairs. It places no
orders, and `--live` has no effect. Any trade comes from your agent acting
on a signal. On-chain trades cannot be recalled. Strategy errors, signal
lag, market regime shifts, and operator misconfiguration can produce losses
exceeding any specific position size.

## A signal is not an edge

Keyword matching says an article mentions a topic. It does not say the
article bears on the market's resolution criteria, and news that reaches an
RSS feed is often priced in already. Have your agent check both before it
trades.

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
