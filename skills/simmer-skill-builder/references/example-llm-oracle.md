# Example: LLM Probability Oracle

Agent-as-oracle pattern. The agent provides probability estimates using its own LLM capability; the Python script handles deterministic math (bias correction, Kelly sizing, trade execution). No LLM SDK dependency needed — the agent IS the LLM.

This pattern is common in KOL strategy posts where the strategy uses Claude/GPT as a "probability engine" to estimate true probabilities for prediction markets.

## How it works

1. **SKILL.md body** instructs the agent on the calibration approach (reference-class framing, structured output, confidence gates)
2. **Agent evaluates** each candidate market using its own reasoning, producing a probability estimate + confidence level
3. **Python script receives** the estimate and applies deterministic gates: bias correction → Kelly sizing → position cap → limit order execution

The agent provides the intelligence. The script provides the math. Different agent runtimes (GPT-5.5, Claude, Qwen) each apply the calibration with their own strengths.

## SKILL.md body (agent instructions section)

The generated SKILL.md should include a section like this in its body, after the setup instructions:

```markdown
## How to evaluate markets

For each candidate market that passes the scan filters, evaluate it using
reference-class forecasting. Produce a structured assessment:

### Calibration approach

1. Identify the **reference class**: what category of event is this? (election,
   crypto price, sports, weather, geopolitical). What is the historical base rate
   for this class of outcome?
2. Apply **base-rate anchoring**: start from the base rate, then adjust based on
   the specific circumstances of this market. Weight base rates over narrative.
3. Estimate **true probability** as a float between 0.03 and 0.97. Never output
   probabilities outside this range — extreme confidence is almost always wrong
   in prediction markets.
4. Assess **confidence**: "high" (strong base-rate data, well-understood domain),
   "medium" (decent data but some uncertainty), "low" (speculative, limited data).
5. Identify the **edge direction**: compare your probability to the market price.
   If your estimate is higher → edge is YES. Lower → edge is NO.
   If within 5pp of market price → no actionable edge.

### Structured output

After evaluating each market, pass these values to the trading script:

- `true_probability` (float, 0.03-0.97)
- `confidence` ("high", "medium", "low")
- `edge_direction` ("YES", "NO", "NONE")
- `reasoning` (1-2 sentences explaining the key factors)

Skip markets where confidence is "low" or edge_direction is "NONE".

### Cost bounding

Evaluate at most 15 candidate markets per run. Each evaluation costs one LLM
reasoning step — unbounded scanning wastes agent compute. Apply scan filters
(volume, resolution window, price range) before evaluation, not after.
```

## Longshot bias correction table

Include this in the SKILL.md body so the agent understands the correction, and also embed the values in the Python script for deterministic application:

```markdown
### Longshot bias correction

Prediction markets systematically overprice longshots and underprice favorites.
Apply this correction to your raw probability estimate before sizing:

| Market price | Implied prob | Actual win rate | Adjustment |
|---|---|---|---|
| 0.05 | 5.0% | 4.18% | -16% |
| 0.10 | 10.0% | 8.90% | -11% |
| 0.20 | 20.0% | 19.40% | -3% |
| 0.30 | 30.0% | 29.50% | -1.7% |
| 0.50 | 50.0% | 49.80% | -0.4% |
| 0.70 | 70.0% | 70.50% | +0.7% |
| 0.80 | 80.0% | 81.40% | +1.8% |
| 0.90 | 90.0% | 91.20% | +1.3% |
| 0.95 | 95.0% | 96.10% | +1.2% |

The correction is non-linear and asymmetric. In the tails (where retail
concentrates), the correction is massive. At midpoint, negligible.
```

## Python script structure

The script handles everything deterministic. The agent calls it with probability estimates.

```python
#!/usr/bin/env python3
"""
Polymarket LLM Oracle — deterministic trading engine.

The agent evaluates markets and provides probability estimates.
This script applies bias correction, Kelly sizing, and executes trades.

Usage:
    python oracle_trader.py                           # Dry run
    python oracle_trader.py --live                    # Real trades
    python oracle_trader.py --evaluate MARKET_ID PROB CONFIDENCE SIDE REASONING
    python oracle_trader.py --positions               # Show positions
"""

import os
import sys
import json
import argparse
from bisect import bisect_right
from datetime import datetime, timezone

sys.stdout.reconfigure(line_buffering=True)

from simmer_sdk.skill import load_config, update_config, get_config_path
from simmer_sdk.sizing import SIZING_CONFIG_SCHEMA, size_position

SKILL_SLUG = "polymarket-llm-oracle"

CONFIG_SCHEMA = {
    "min_edge": {"env": "SIMMER_ORACLE_MIN_EDGE", "default": 0.08, "type": float},
    "min_volume": {"env": "SIMMER_ORACLE_MIN_VOLUME", "default": 50000, "type": float},
    "min_days_to_resolution": {"env": "SIMMER_ORACLE_MIN_DAYS", "default": 7, "type": int},
    "max_days_to_resolution": {"env": "SIMMER_ORACLE_MAX_DAYS", "default": 30, "type": int},
    "price_range_low": {"env": "SIMMER_ORACLE_PRICE_LOW", "default": 0.10, "type": float},
    "price_range_high": {"env": "SIMMER_ORACLE_PRICE_HIGH", "default": 0.40, "type": float},
    "max_bankroll_fraction": {"env": "SIMMER_ORACLE_MAX_FRACTION", "default": 0.03, "type": float},
    "order_type": {"env": "SIMMER_ORACLE_ORDER_TYPE", "default": "GTC", "type": str},
    "max_trades_per_run": {"env": "SIMMER_ORACLE_MAX_TRADES", "default": 3, "type": int},
    **SIZING_CONFIG_SCHEMA,
}

_config = load_config(CONFIG_SCHEMA, __file__, slug=SKILL_SLUG)

# --- Bias correction (stdlib only, no numpy) ---

BIAS_TABLE_PRICES = [0.05, 0.10, 0.20, 0.30, 0.50, 0.70, 0.80, 0.90, 0.95]
BIAS_TABLE_ACTUAL = [0.0418, 0.0890, 0.1940, 0.2950, 0.4980, 0.7050, 0.8140, 0.9120, 0.9610]


def apply_longshot_correction(raw_prob):
    """Correct for systematic longshot bias using linear interpolation (stdlib)."""
    if raw_prob <= BIAS_TABLE_PRICES[0]:
        return BIAS_TABLE_ACTUAL[0]
    if raw_prob >= BIAS_TABLE_PRICES[-1]:
        return BIAS_TABLE_ACTUAL[-1]
    i = bisect_right(BIAS_TABLE_PRICES, raw_prob) - 1
    t = (raw_prob - BIAS_TABLE_PRICES[i]) / (BIAS_TABLE_PRICES[i + 1] - BIAS_TABLE_PRICES[i])
    return BIAS_TABLE_ACTUAL[i] + t * (BIAS_TABLE_ACTUAL[i + 1] - BIAS_TABLE_ACTUAL[i])


def evaluate_and_trade(market_id, raw_prob, confidence, side, reasoning, live=False):
    """Apply bias correction, size, and execute if edge survives."""
    client = get_client(live=live)
    market = client.get_market_by_id(market_id)
    if not market:
        print(f"  Market {market_id} not found")
        return None

    market_price = market.current_probability
    corrected_prob = apply_longshot_correction(raw_prob)
    edge = corrected_prob - market_price if side == "yes" else market_price - corrected_prob

    print(f"  Raw prob: {raw_prob:.3f} → Corrected: {corrected_prob:.3f}")
    print(f"  Market price: {market_price:.3f} | Edge: {edge:+.3f} | Side: {side.upper()}")

    if edge < _config["min_edge"]:
        print(f"  SKIP — edge {edge:.3f} below threshold {_config['min_edge']}")
        return None

    if confidence == "low":
        print(f"  SKIP — low confidence")
        return None

    portfolio = client.get_portfolio()
    bankroll = portfolio.get("balance_usdc", 0) + portfolio.get("total_exposure", 0)

    amount = size_position(
        p_win=corrected_prob,
        market_price=market_price,
        bankroll=bankroll,
        kelly_multiplier=_config["kelly_multiplier"],
        max_fraction=_config["max_bankroll_fraction"],
        min_ev=_config["min_ev"],
    )

    if amount <= 0:
        print(f"  SKIP — size_position returned $0 (below min_ev)")
        return None

    trade_reasoning = (
        f"{reasoning} | "
        f"raw_p={raw_prob:.3f}, corrected_p={corrected_prob:.3f}, "
        f"edge={edge:+.3f}, confidence={confidence}"
    )

    trade_price = market_price - 0.005 if side == "yes" else market_price + 0.005
    trade_price = max(0.01, min(0.99, round(trade_price, 3)))

    return execute_trade(
        market_id=market_id,
        side=side,
        amount=amount,
        reasoning=trade_reasoning,
        price=trade_price,
        order_type=_config["order_type"],
    )
```

## Key design decisions in this example

**Agent provides `raw_prob`, script applies correction.** The bias correction table is deterministic math — it doesn't need LLM reasoning. The agent's job is calibrated probability estimation; the script's job is market-microstructure adjustment.

**Trade reasoning preserves the full chain.** `raw_p → corrected_p → edge → confidence` are all visible in the public reasoning field. Users and reviewers can trace exactly how the decision was made.

**GTC limit orders by default.** The script places limits 0.5c inside the spread. For a quant strategy, maker rebates compound over many trades (2.24pp maker-taker spread).

**`max_fraction=0.03` by default.** Quarter-Kelly with a 3% bankroll cap. Conservative — this is how the KOL strategies actually size.

**Cost bounding is in the SKILL.md, not the script.** The "evaluate at most 15 markets" rule lives in the agent instructions because it governs the agent's behavior, not the script's. The script processes whatever the agent sends it.

## Power-user alternative: embedded LLM SDK

For users who want deterministic reproducibility (same model, same prompt, same output regardless of which agent runtime runs the skill), add an LLM SDK as a dependency:

```json
{
  "requires": {
    "env": ["SIMMER_API_KEY", "ANTHROPIC_API_KEY"],
    "pip": ["simmer-sdk", "anthropic"]
  }
}
```

The script then calls the LLM API directly instead of receiving estimates from the agent. This trades runtime-agnosticism for reproducibility. Frame as opt-in in the SKILL.md body:

```markdown
> **Power-user mode:** If you want deterministic probability estimates
> (same model every run, independent of which agent runtime you use),
> set `ANTHROPIC_API_KEY` and the script will call Claude directly
> instead of relying on your agent's built-in reasoning.
```
