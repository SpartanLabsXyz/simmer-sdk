# Disclaimer

## Research tooling, not financial advice

Nothing this skill produces is financial, investment, or trading advice. It
re-ranks a list of wallets using Nansen's Polymarket PnL data and attaches
reason codes. Deciding whether to act on that ranking, and with how much
capital, is your responsibility.

## It does not place trades

Every function and CLI command here is signal-only. The skill returns a
ranked list and stops. The live insider scan refuses to run outside dry-run
by design and raises rather than warning. Nothing in this package can move
funds, and adding execution on top of it is a deliberate change you make at
your own risk.

## The scoring is unvalidated

The scoring weights, the 50/50 blend between the Nansen score and your
existing score, the `MIN_RESOLVED_MARKETS` floor and the tag penalties are
reasoned starting points. They have not been fitted to data, backtested, or
forward-tested. No result shows that this re-ranking improves realised
copytrading returns. Treat the output as research until that changes.

## Past performance is not predictive

The primary signal is realised PnL in a specific market. A wallet that
earned in a market may have been lucky, may have had information you do not
have, and may not repeat. Copying a wallet also means copying its entry
price, its timing and its risk tolerance, none of which this skill can see.
Edges depend on spreads, fees, latency and liquidity.

## Wallet coverage is partial

Owner-address harvesting covers roughly 40% of a typical leaderboard, so
some wallets are scored on less data than others. Wallets absent from the
target market's leaderboard are kept at their original score rather than
scored against a signal that does not exist. Read the reason codes; they
tell you which case each wallet fell into.

## It spends your money

This skill is bring-your-own-key. Every call consumes credits on your own
Nansen account. The credit guards are hard caps, not estimates, but you
should still confirm your balance before a large run. Nansen's published
price list did not match what we were billed for one endpoint, so treat any
credit figure as approximate.

## Third-party data

Nansen is an independent data provider. Simmer does not control the accuracy,
availability or pricing of their API, and their schemas can change without
notice. If their data is wrong, this skill's output is wrong.
