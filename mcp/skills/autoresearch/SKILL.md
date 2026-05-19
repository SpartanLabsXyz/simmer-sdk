---
name: simmer-autoresearch
description: Set up and run autonomous experiment loops to optimize Simmer trading skills. Mutates skill code + config, measures P&L, keeps what works. Use when asked to "optimize a skill", "run autoresearch", or "improve my trading".
---

# Simmer Autoresearch

Autonomous experiment loop for trading skill optimization: try ideas, keep what works, discard what doesn't, never stop.

Based on [pi-autoresearch](https://github.com/davebcn87/pi-autoresearch) (MIT).

## Tools

- **`init_experiment`** — configure session (name, skill_slug, metric, unit, direction). Call again to re-initialize with a new baseline.
- **`run_experiment`** — runs skill command, times it, captures output.
- **`log_experiment`** — records result. **Use `keep` ONLY when the primary metric improved vs the baseline. `discard` if worse or unchanged.** **Zero trades or `metric=0` is no-signal — `discard`, never `keep`.** `crash` if the skill failed. `checks_failed` if post-run validation failed. `keep` auto-commits via git; the others auto-revert. Always include secondary `metrics` dict. State the before→after comparison in `description` (e.g., `"entry_threshold 0.05→0.03; $12→$18 pnl, keep"`). Optionally include `asi` (Actionable Side Information) for structured diagnostics.
- **`backtest_experiment`** — replay historical trades against new config without live execution. Fast config tuning (seconds vs hours). Requires trades with `signal_data` (SDK 0.9.17+).

## Setup

1. Pick a skill to optimize and a primary metric (usually P&L).
2. `git checkout -b autoresearch/<skill>-<date>`
3. Read the skill source code thoroughly — understand what it does before mutating.
4. Write `autoresearch.md` — session spec with goal, metrics, how to run, constraints.
5. Write `autoresearch.sh` — single command that runs the skill and outputs results.
6. Commit both files.
7. `init_experiment` → run baseline with `run_experiment` → `log_experiment` → start looping.

### autoresearch.md template

```
# Autoresearch: <goal>

## Objective
<What we're optimizing and the workload.>

## Metrics
- **Primary**: <name> (<unit>, lower/higher is better)
- **Secondary**: <name>, <name>, ...

## How to Run
`./autoresearch.sh` — runs the skill for one cycle.

## Constraints
- Only modify files in <skill directory>
- Do not change SDK core code
- Sim venue only (no real money)
```

## The Loop

Each iteration:
1. Form a hypothesis (what change might improve the metric?)
2. Mutate skill code or config
3. `run_experiment` — execute the skill
4. `log_experiment` — compare metric to baseline. Improved → `keep`. Worse or equal → `discard`. Crashed → `crash`. Include the before→after comparison in `description`.

**Code mutations > config tuning.** Structural changes (new data sources, different models, alternative strategies) find bigger wins than parameter tweaks.

Use `backtest_experiment` for fast config exploration before committing to live runs.

## Rules

- **Primary metric is king.** Improved → `keep`. Worse or equal → `discard`. Secondary metrics rarely override this — only discard a primary improvement if a secondary metric degraded catastrophically, and explain why in `description`.
- **No signal = `discard`, never `keep`.** If the experiment produced 0 trades, `metric=0`, or no measurable signal, this is a degenerate run — the skill stopped doing the thing you're trying to optimize. Discard so the next iteration tries something different. If you see this twice in a row, **stop the loop and investigate the skill itself** before mutating further. A dead loop produces meaningless commits and burns runs.
- **State the comparison in `description`.** Every `log_experiment` should make the before→after explicit (e.g., `"reduced entry threshold 0.05→0.03; $12→$18 pnl, 4→6 trades, keep"`). This is load-bearing for future iterations and for the dashboard to reason about your decisions.
- **Never skip the baseline run.** The first experiment establishes the reference point.
- **Always log — even crashes.** The data matters for confidence scoring.
- **Check confidence before trusting results.** >=2x noise floor = likely real. <1x = within noise. 1-2x = marginal, re-run to confirm.
- **Don't chase noise.** If confidence is low, the improvement may be random. Try a different approach instead of refining a noisy one.
- **Don't thrash.** Repeatedly reverting the same idea? Try something structurally different.
- **Simpler is better.** Removing code for equal perf = `keep`. Ugly complexity for tiny gain = probably `discard`.
- **Write ideas to autoresearch.ideas.md.** Promising but deferred optimizations go here. Check it for experiment paths.

**NEVER STOP.** The user may be away for hours. Keep the loop running until interrupted.

## Crash Recovery

- **Baseline crash** -> autoresearch pauses. The skill is misconfigured or the run command is wrong. Fix, then call `init_experiment` to start fresh.
- **3 consecutive crashes** -> autoresearch pauses. Something is systematically broken. Investigate: read crash outputs, check git status, try running manually.
- **Context compaction** -> re-read `autoresearch.md` and `autoresearch.jsonl` to restore context.

## Configuration

Set via environment variables on the MCP server:

| Variable | Default | Purpose |
|----------|---------|---------|
| `SIMMER_API_KEY` | (required) | API key for dashboard sync and backtest |
| `SIMMER_API_URL` | `https://api.simmer.markets` | API base URL |
| `AUTORESEARCH_MAX_EXPERIMENTS` | `50` | Max experiments per session (0 = unlimited) |
