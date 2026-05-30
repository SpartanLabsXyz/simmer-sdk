# Disclaimer

This skill is a framework, not a production trading system.

Nothing here is financial, investment, or trading advice. The DCA schedule,
stop-loss, take-profit, and evaluation envelope defaults are examples for
testing and operator review. They are not validated to produce positive returns.

Running with `--live` places real orders. On-chain trades cannot be recalled.
Market structure, liquidity, slippage, stale inputs, venue outages, and operator
misconfiguration can all produce losses.

The eval-envelope simulator is only a sizing check against a generic
prop-firm-shaped constraint set: 10% target, 6% static drawdown, and 3% daily
drawdown by default. It does not imply that Propr or any other prop challenge
will approve, pass, fund, or partner with this strategy.

Risk monitors run on platform cadence. Markets that resolve faster than that
cadence may not be exited automatically, even if a stop-loss or take-profit is
configured. Size such markets as if no automated exit exists.
