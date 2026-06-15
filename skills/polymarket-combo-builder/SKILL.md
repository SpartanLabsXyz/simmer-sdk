---
name: polymarket-combo-builder
description: Build an atomic Polymarket combo (parlay) - pick 2+ binary market legs, get live combined odds quoted over RFQ, and place the whole thing as ONE signed order. Every leg must hit to win; any single leg losing is a total loss of the stake. Works for both EOA and deposit-wallet (managed-style) accounts. Dry-run by default. Sports-general (World Cup ready).
category: world-cup
tags:
  - world-cup
  - parlay
  - combo
metadata:
  author: Simmer (@simmer_markets)
  version: "0.1.0"
  displayName: Combo Builder
  difficulty: beginner
---

# Combo Builder

Place a real Polymarket **combo** (parlay): bundle 2+ binary legs into one
YES/NO position, settled by a single signed RFQ order on Polymarket's combo
exchange. Polymarket quotes the *combined* odds live; you sign once; if every
leg hits you win the combined payout, and if any single leg loses you lose the
whole stake.

This is the atomic combo — distinct from the leg-by-leg
`polymarket-worldcup-parlay-roller`. It works for **deposit-wallet** accounts
(the default for new Simmer/Polymarket users) as well as plain EOA wallets.

Read [DISCLAIMER.md](./DISCLAIMER.md) before going live. This is a framework,
not an edge, and it runs in **dry-run by default**.

Polymarket venue only. Combos are a BETA Polymarket product (sports markets
today); label them as such to users.

## How payout works

A combo's combined price is the makers' quoted probability that **all** legs
hit. Example: three legs near 0.50 each quote around a 0.12–0.15 combined
price, i.e. a ~7–8x payout multiple on the stake. The quote you sign already
bakes in maker spread — it is not a naive product of the leg prices (the skill
shows that product only as a pre-quote preview).

## Setup

Agent: do this once with the user.

1. **Get the thesis** in natural language, e.g. "Brazil to win AND over 2.5
   goals AND Argentina to win."
2. **Resolve each pick to a combo leg.** Combo-eligible legs come from the
   combo-markets feed — `simmer_sdk.combo.fetch_combo_legs()`. Each leg exposes
   `position_ids = [YES_token, NO_token]`; store the **chosen side's** token id.
   Moneyline, spread (`-spread-`), and total/over-under (`-total-`) legs are all
   eligible.
3. **Confirm with the user**, restating each leg's exact resolution condition
   and the total-loss-if-any-leg-loses risk.
4. Write `combo_config.json` from `combo_config.example.json` (>= 2 legs).
5. **Dry-run first**, review the plan, then go live.

## Run

```bash
python combo_builder.py            # dry-run: resolve legs, show estimate + plan, place nothing
python combo_builder.py --live     # place the combo for real (money path)
python combo_builder.py --legs     # browse combo-eligible legs (no config needed)
```

Dry-run opens no socket, signs nothing, and moves no money. `--live` requires a
configured wallet (`WALLET_PRIVATE_KEY`) and a live Simmer client.

## Wallet support

- **EOA** (`signature_type 0`): standard self-custody key.
- **Deposit wallet** (`signature_type 3` / POLY_1271): the managed-style
  deposit-wallet cohort. The skill resolves your cohort automatically from your
  Simmer wallet state and signs the correct way (`maker == signer == deposit
  wallet`, ERC-7739-wrapped).

OWS-signed wallets are not yet supported for combos — use a raw
`WALLET_PRIVATE_KEY` (the deposit-wallet owner key) for now.

## Notes

- **Min stake $1** (Polymarket order minimum). The stake is the most you can lose.
- The combined quote is valid ~5 seconds; the skill signs and accepts inside
  that window automatically, re-quoting if it lapses.
- No "risk-free" or "guaranteed" anything — a combo is strictly higher-variance
  than its legs.
