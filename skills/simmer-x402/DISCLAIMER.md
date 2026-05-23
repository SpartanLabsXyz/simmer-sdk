# Disclaimer

This skill is a **framework for x402 payment plumbing**, not financial advice.
Read this in full before connecting it to a wallet with real funds.

## No financial advice

Nothing in this skill constitutes financial, investment, or payment-strategy
advice. The skill handles the mechanical flow of detecting `402 Payment
Required` responses and producing valid x402 settlement transactions on Base.
What endpoints you pay for, at what price, and how often is a policy decision
left entirely to the operator.

## Real on-chain payments — irreversible

When this skill executes a payment, it broadcasts a real on-chain USDC
transfer on Base. On-chain transfers cannot be recalled. Confirmed payments
to a malicious or misconfigured endpoint cannot be clawed back.

Mitigations available in the skill:
- `--dry-run` shows the payment payload without broadcasting.
- `--max <USD>` caps the per-call payment amount.
- The `X402_MAX_PAYMENT_USD` env var sets a default cap for unattended runs.

These mitigations bound per-call exposure; they do not prevent malicious or
misconfigured endpoints from charging the cap repeatedly. Audit your call
sites and rate-limit at the agent layer.

## Default parameters are starting points

Default per-call caps and balance thresholds are calibrated for testing
plumbing, not for production payment volumes. Review every parameter before
scaling.

## Scope the funded wallet

The wallet you fund for x402 payments has full balance authority — any call
the agent makes that hits an x402-gated endpoint can spend up to the
configured cap. Treat the x402 wallet as scoped working capital, not a
treasury wallet. Fund it with the amount you expect to spend in the next
session, no more.

## Use of this skill is at your own risk

By installing and running this skill you agree that the authors are not
liable for any losses, direct or indirect, that arise from its use. This
applies regardless of skill provenance — official Simmer skills, community
skills, and skills imported from external repositories all carry this same
disclaimer.
