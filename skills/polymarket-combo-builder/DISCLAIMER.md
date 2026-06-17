# Disclaimer — Combo Builder

This skill places **combos (parlays)** on Polymarket. Read this before going live.

- **A combo is a total-loss product.** Every leg must resolve in your favor to
  win. If any single leg loses, you lose the **entire stake**. A combo is
  strictly higher variance than betting the legs individually.
- **No edge is implied.** This is a tool for expressing a multi-leg view, not a
  strategy with a proven edge. There is nothing risk-free or guaranteed about it.
- **Combos are a BETA Polymarket product** (reverse-engineered RFQ taker flow,
  sports markets today). Endpoints and behavior can change. Surfaces should label
  combos as beta.
- **You sign with your own wallet.** Your private key stays local and is used
  only to sign the order. Simmer never sees it. The stake is the maximum loss.
- **Dry-run by default.** The skill places nothing until you pass `--live`, and
  `--live` requires a configured wallet and a live Simmer client.
- **Minimum stake is $1** (Polymarket order minimum).
- Markets can resolve in unexpected ways; resolution sources and timing are
  Polymarket's. Confirm each leg's exact resolution condition before placing.

You are responsible for your own trades. Nothing here is financial advice.
