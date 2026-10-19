---
name: polymarket-dca-eval-trader
description: Build and optionally execute a three-tranche Polymarket DCA plan with prop-firm-shaped evaluation envelope checks. Use when the user wants a Bubbles/Roya-style staged averaging template for one thesis, with paper mode by default and explicit live opt-in.
metadata:
  author: Simmer (@simmer_markets)
  version: "0.1.0"
  status: scaffold
  displayName: Polymarket DCA Eval Trader
  difficulty: advanced
---
# Polymarket DCA Eval Trader

Three-tranche Polymarket DCA template for a single selected thesis. It is modeled as a Bubbles/Roya-style staged averaging framework, not as a guarantee of passing any evaluation.

Read [DISCLAIMER.md](./DISCLAIMER.md) before connecting funds.

## Safety Rails

- Paper mode is the default. Real orders require `--live`.
- Exactly 3 tranches are supported. This is intentional so sizing and risk checks stay inspectable.
- Per-market and daily exposure caps are enforced before any live order.
- SDK preflight runs before live trading.
- Every journaled event includes `source=sdk:polymarket-dca-eval-trader`.
- SDK risk monitors are attached only when market duration supports them. Short markets may resolve before monitor cadence can act.
- Defaults are configurable, not a pass guarantee.
- No claim is made that this passes Propr or any prop challenge. Partnership or marketing claims remain blocked on a separate Adrian decision.

## Strategy Shape

The skill enters one thesis through exactly three averaging levels:

| Tranche | Default trigger | Default weight |
|---------|-----------------|----------------|
| 1 | 0.0% price displacement or 0h elapsed | 34% |
| 2 | 2.5% adverse price displacement or 24h elapsed | 33% |
| 3 | 5.0% adverse price displacement or 48h elapsed | 33% |

Schedule format:

```bash
SIMMER_DCA_EVAL_TRANCHE_SCHEDULE="0:0:0.34,2.5:24:0.33,5:48:0.33"
```

Each entry is:

```text
displacement_pct:elapsed_hours:size_weight
```

A tranche is eligible when either its adverse price displacement or elapsed-time trigger fires.

## Default SL/TP

The default stop-loss and take-profit thresholds follow the requested Roya/Bubbles reference shape:

| Knob | Default |
|------|---------|
| `SIMMER_DCA_EVAL_STOP_LOSS_PCT` | 2.5 |
| `SIMMER_DCA_EVAL_TAKE_PROFIT_PCT` | 4.5 |

The thresholds are calculated from weighted average selected-token entry price. They are configurable defaults, not evidence that the strategy is profitable.

## Eval Envelope

Before a live order, the skill reports whether the executable tranche size would remain inside:

| Constraint | Default |
|------------|---------|
| Profit target | 10% |
| Static drawdown | 6% |
| Daily drawdown | 3% |

The envelope assumes a full loss on the proposed executable tranche. Passing the envelope only means the proposed size fits those constraints. It does not predict trade outcome, skill profitability, or prop challenge approval.

## Quick Start

```bash
# Paper plan only
python dca_eval_trader.py --market MARKET_ID --side yes --anchor-price 0.55

# Paper plan with current price and elapsed time
python dca_eval_trader.py --market MARKET_ID --side yes --anchor-price 0.55 --current-price 0.52 --elapsed-hours 25

# Live trading after reviewing the JSON plan
python dca_eval_trader.py --market MARKET_ID --side yes --anchor-price 0.55 --current-price 0.52 --elapsed-hours 25 --live
```

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `SIMMER_DCA_EVAL_TOTAL_BUDGET` | 30 | Total intended cost across all 3 tranches |
| `SIMMER_DCA_EVAL_PER_MARKET_CAP` | 50 | Max exposure on one market |
| `SIMMER_DCA_EVAL_DAILY_CAP` | 100 | Max new exposure per UTC day |
| `SIMMER_DCA_EVAL_TRANCHE_SCHEDULE` | `0:0:0.34,2.5:24:0.33,5:48:0.33` | Three tranche rules |
| `SIMMER_DCA_EVAL_STOP_LOSS_PCT` | 2.5 | Stop-loss percent from average entry |
| `SIMMER_DCA_EVAL_TAKE_PROFIT_PCT` | 4.5 | Take-profit percent from average entry |
| `SIMMER_DCA_EVAL_ACCOUNT_SIZE` | 10000 | Eval-envelope account size |
| `SIMMER_DCA_EVAL_TARGET_PCT` | 10 | Profit target percentage |
| `SIMMER_DCA_EVAL_STATIC_DRAWDOWN_PCT` | 6 | Static drawdown percentage |
| `SIMMER_DCA_EVAL_DAILY_DRAWDOWN_PCT` | 3 | Daily drawdown percentage |

## Journal

Default journal path:

```text
~/.simmer/polymarket-dca-eval-trader/journal.jsonl
```

The journal records paper plans and live trades so repeated runs can enforce daily cap usage.

## When Not To Use

Do not use this skill for:

- Multi-market baskets.
- Martingale sizing.
- Fast markets where monitors cannot act before resolution unless the position is sized as if no exit automation exists.
- Any public claim that this passes a prop-firm challenge.
