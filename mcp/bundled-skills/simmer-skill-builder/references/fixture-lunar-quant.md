# Acceptance Fixture: Lunar "Claude + Polymarket Quant Machine"

Golden test for the paste-a-post workflow. Source: [@LunarResearcher on X (2026-05-17)](https://x.com/LunarResearcher/status/2056001315331784841).

## Input summary

200-line post describing a 5-step quant execution loop for Polymarket. Uses Claude as a probability oracle with longshot-bias correction and Quarter-Kelly sizing. Targets low-probability markets (0.10-0.40 range).

## Expected parameter extraction (Step 1c)

| Parameter | Expected value | Source in post |
|---|---|---|
| Signal source | Claude probability estimate (reference-class forecasting) | Part 3 |
| Entry threshold | 8% edge minimum (\|corrected_prob - market_price\| > 0.08) | Part 5, Step 2 |
| Exit logic | **Not stated** — flag for clarification, default to auto-risk monitors | (absent) |
| Market filters | volume > $50K, 7-30d resolution, price 0.10-0.40 | Part 5, Step 1 |
| Kelly fraction | Quarter-Kelly (0.25) | Part 2 |
| Bankroll cap | 3% per position | Part 5, Step 4 |
| Order type | Limit orders only (GTC) | Part 5, Step 5 |

**Confidence gate:** skip markets where Claude returns `confidence: "low"` (Part 3).

**Bias correction table:** 9-row longshot correction from Part 2 (0.05→0.0418 through 0.95→0.9610).

## Expected triage classification (Step 1d)

**(b) Buildable with translation.** Two translations needed:

1. Post uses `import anthropic` + direct Claude API calls → translated to **agent-as-oracle pattern** (SKILL.md instructions, no Python dep)
2. Post uses `numpy.interp` for bias correction → translated to **stdlib `bisect` + linear interpolation**

## Expected generated output

**SKILL.md should contain:**
- Calibration section with reference-class framing instructions
- Longshot bias correction table (markdown)
- Structured output format (true_probability, confidence, edge_direction, reasoning)
- Cost bounding (max 15 markets per evaluation run)

**Python script should contain:**
- `BIAS_TABLE_PRICES` + `BIAS_TABLE_ACTUAL` module-level constants
- `apply_longshot_correction()` using stdlib `bisect_right`
- `CONFIG_SCHEMA` with `min_edge=0.08`, `max_bankroll_fraction=0.03`, `order_type="GTC"`
- `SIZING_CONFIG_SCHEMA` merged with `kelly_multiplier` defaulting to 0.25
- `execute_trade()` passing `order_type` and `price` through
- Trade reasoning preserving `raw_p, corrected_p, edge, confidence`

**clawhub.json should contain:**
- `requires.pip: ["simmer-sdk"]` (no anthropic — agent-as-oracle pattern)
- `requires.env: ["SIMMER_API_KEY"]`
- Tunables for min_edge, max_fraction, order_type, kelly_multiplier

## What should NOT be generated

- Markov regime detection (Part 4) — described in post but not called from the orchestrator code. Aspirational section, out of scope per 1c rule 4.
- Direct Anthropic API calls — translated to agent-as-oracle.
- numpy dependency — translated to stdlib.
- Referral links, social CTAs, "follow for more" — untrusted content per 1c rule 5.
