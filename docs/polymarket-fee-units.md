# Polymarket fee parameter units

Verified against Polymarket DeFi documentation on 2026-11-18. These rules do not
cover Polymarket US or QCX.

## The coefficient comes from fee curve parameters

The [official fee formula](https://docs.polymarket.com/trading/fees) is
`shares × feeRate × price × (1 - price)`. The coefficient is price-dependent in
its effect on trade cost. It is not a flat fraction of notional.

The [CLOB market endpoint](https://docs.polymarket.com/api-reference/markets/get-clob-market-info)
returns a separate `fd` object for fee curve parameters. In the
[official Python client](https://github.com/Polymarket/py-clob-client-v2/blob/main/py_clob_client_v2/client.py),
`get_clob_market_info()` reads `fd.r` directly into `FeeInfo.rate` and `fd.e`
into `FeeInfo.exponent`. Its balance adjustment passes both values to the
[official fee calculator](https://github.com/Polymarket/py-clob-client-v2/blob/main/py_clob_client_v2/fees.py),
which calculates the platform fee as
`(notional / price) × rate × (price × (1 - price)) ** exponent`.

Therefore `fd.r` maps directly to `feeRate` when `fd.e == 1`. There is no
division by 10,000. Other exponents are outside this helper's curve and return
unknown. Missing, malformed, or unsupported parameters also return unknown.
An explicit valid `fd.r == 0` is different from a missing coefficient.

The [market details documentation](https://docs.polymarket.com/market-data/market-details)
also describes Gamma `feeSchedule.rate` as the fee calculation's base rate.
Its example uses rate `0.04` and exponent `1`.

## Legacy basis points do not establish this coefficient

The [fee-rate endpoint reference](https://docs.polymarket.com/api-reference/market-data/get-fee-rate)
describes `base_fee` as basis points. It does not establish a conversion from
that field to the current fee curve's coefficient. The official client keeps
`base_fee` in its legacy fee-rate cache, separately from `FeeInfo`.

[Upstream issue #107](https://github.com/Polymarket/py-clob-client-v2/issues/107)
reports `base_fee == 1000` alongside different `fd.r` values, including `0`.
Those are reporter observations, not an official conversion contract.
No universal `base_fee / 10000` mapping is established by the sources above.
Legacy `fee_rate_bps` and `base_fee` values therefore remain wire metadata.
Neither a positive legacy value nor the SDK's zero sentinel supplies a known
coefficient. Signing and match-time reconciliation are outside this change.

The [November schedule](https://docs.polymarket.com/programs/fee-and-rebate-changes-november-2026)
provides date-aware category estimates when a valid venue coefficient is
unavailable. Such estimates identify their schedule source. They do not
represent a successful venue lookup.

## Offline evidence

[`tests/fixtures/polymarket_fee_parameters.json`](../tests/fixtures/polymarket_fee_parameters.json)
records documentation examples and explicitly labelled synthetic parser cases.
It is not a live market capture. Its expected results prove unit handling and
unknown handling, not actual fees charged by a venue or trading edge.
