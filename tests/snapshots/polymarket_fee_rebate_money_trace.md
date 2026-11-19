# Polymarket fee/rebate money trace

Fake UTC clock, fake venue, no live HTTP. Mechanism proof only; no trading edge claim.
Same BTC 5m signal, 50-cent price, 5-cent divergence, zero legacy fee sentinel.

## 2026-11-20T23:58:59.999999+00:00
Fast-loop executions: 0. Fee:              $0.0175/share (3.50% on spend, min divergence 0.055)
Mert executions: 0. Fee:   $0.0168/share (2.80% on spend) | declared edge 0.034 vs required 0.037 [category schedule fallback; legacy base_fee=None]
Scaler executions: 1. Fee: $0.0175/share (3.50% on spend at 0.500; category schedule fallback)
Replay explicit BTC coefficient: 0.07; 100 shares at $0.50; fee $1.7500; cash $48.2500.
Taker wV for that fill: 57.50.
Known Gold taker rebate: 18%; unknown account: None.
Maker quoted potential fee-equivalent: $0.7280; pool fraction: 0.2; known 200k maker wV tier: None.
Maker quotes are not fills; no rebate is credited.

## 2026-11-20T23:59:00+00:00
Fast-loop executions: 1. Fee:              $0.0125/share (2.50% on spend, min divergence 0.045)
Mert executions: 1. Fee:   $0.0120/share (2.00% on spend) | declared edge 0.034 vs required 0.032 [category schedule fallback; legacy base_fee=None]
Scaler executions: 1. Fee: $0.0125/share (2.50% on spend at 0.500; category schedule fallback)
Replay explicit BTC coefficient: 0.05; 100 shares at $0.50; fee $1.2500; cash $48.7500.
Taker wV for that fill: 25.00.
Known Gold taker rebate: 10%; unknown account: None.
Maker quoted potential fee-equivalent: $0.5200; pool fraction: None; known 200k maker wV tier: 0.03.
Maker quotes are not fills; no rebate is credited.
