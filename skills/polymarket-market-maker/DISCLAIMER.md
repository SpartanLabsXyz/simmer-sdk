# Disclaimer

**This skill is a framework, not a production trading system.**

It is provided for educational and research purposes only. Nothing in this skill constitutes financial advice, investment advice, or a recommendation to buy or sell any financial instrument.

## Market-Making Risk

Market-making is **not** directional alpha trading. You are providing liquidity and earning the bid-ask spread, but:

- **Adverse selection.** Informed traders will take your quotes when the true probability has moved. You earn the spread on uninformed flow and lose on informed flow. Net profitability depends on the ratio.
- **Inventory risk.** A one-sided fill (e.g., all your YES bids hit but asks don't) creates directional exposure. The `max_skew_pct` guard limits — but does not eliminate — this risk.
- **Resolution risk.** Markets resolve to 0 or 1. If you hold inventory into resolution on the wrong side, you lose the full position value.
- **Liquidity risk.** Low-volume markets may have wide spreads that look attractive but generate almost no fills. Capital sits idle.

## Trading Real Funds

- **You may lose money.** The default parameters are illustrative starting points, not backtested recommendations.
- **Fees compound.** GTC orders on Polymarket CLOB incur fees on fill. Maker rebates offset but do not eliminate costs on all market types.
- **Slippage.** Automated cancel/replace does not guarantee execution at the intended price.
- **Your responsibility.** By connecting this skill to a wallet, you accept full responsibility for any losses.

## Maker Rebate Disclaimer

The Polymarket Maker Rebates Program (as of 2026) redistributes taker fees to makers on eligible markets (sports, crypto, certain categories). Rebate rates, eligible markets, and program terms are determined by Polymarket and may change without notice. Fee-equivalent volume estimates in this skill are informational only.

## No Guarantees

The authors and distributors make no representations about accuracy, completeness, or fitness for purpose. The strategy logic has not been independently audited.

1. Run in dry-run mode first (default — no `--live` flag).
2. Start with amounts you can afford to lose entirely.
3. Monitor positions regularly.
4. Comply with laws and regulations in your jurisdiction.

**Use at your own risk.**
