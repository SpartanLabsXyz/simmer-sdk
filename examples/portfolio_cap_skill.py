"""
Reference integration for the portfolio-level concurrent-exposure cap (SIM-1451).

Shows the canonical pattern for a multi-strategy agent that wants to bound
its total open notional across all skills:

    1. Compute a candidate trade size with the existing per-trade Kelly cap
       (``simmer_sdk.sizing.size_position`` — fractional Kelly, default 0.25x,
       hard-capped via ``max_fraction``).
    2. Snapshot current open positions across all skills/strategies via
       ``client.get_positions()`` (or any other source you trust).
    3. Call ``check_portfolio_cap`` with the candidate, bankroll, and the
       snapshot. The primitive returns ``allow`` / ``deny`` / ``trim_to``
       and the size you may safely place.
    4. Use ``decision.allowed_size`` for the trade. ``deny`` → skip;
       ``trim_to`` → place a smaller-than-requested order.

The cap is a *cross-skill* ceiling: every strategy run by the same agent
sees the same total open notional, so a 92¢ scalp + 1¢ reversal + 49¢ MM
together cannot breach (within the limits noted under "Concurrency" in
``simmer_sdk.risk.portfolio_cap``).

Distinction from drawdown control
---------------------------------
The portfolio cap gates *new entries* based on currently open exposure:

    open_notional / bankroll ≤ total_cap_pct   →  may add more
                              >  total_cap_pct →  trim or skip

A drawdown controller (see SIM-1072 ``DrawdownController`` — separate
ticket, separate concern) gates *new entries* based on realized P&L:

    peak_equity - current_equity ≥ Y%  →  halt new entries for Z hours

Both are pre-trade gates, but they answer different questions and are
complementary. Wire both if you need both.

Usage:
    export SIMMER_API_KEY="sk_live_..."
    python portfolio_cap_skill.py
"""

from __future__ import annotations

import os
from typing import Sequence

from simmer_sdk import (
    SimmerClient,
    check_portfolio_cap,
    size_position,
)


def run_one_market_with_portfolio_cap(
    client: SimmerClient,
    market_id: str,
    p_win: float,
    market_price: float,
    bankroll: float,
    *,
    # Per-trade cap from the existing Kelly machinery — 8% hard cap as in
    # the SIM-1451 ticket spec (max_fraction=0.08).
    kelly_multiplier: float = 0.25,
    per_trade_max_fraction: float = 0.08,
    # Portfolio-level cap layered on top — 15% of bankroll across all
    # skills/strategies for this agent.
    total_cap_pct: float = 0.15,
    agent_id: str | None = None,
) -> float:
    """Decide and (optionally) trim a trade size for one market.

    Returns the dollar amount the strategy should actually allocate.
    ``0.0`` means skip — either the per-trade Kelly returned zero, the
    portfolio is already at cap, or the candidate was trimmed to a value
    too small to be useful.
    """
    # 1. Per-trade Kelly sizing (this is the existing primitive).
    candidate = size_position(
        p_win=p_win,
        market_price=market_price,
        bankroll=bankroll,
        method="fractional_kelly",
        kelly_multiplier=kelly_multiplier,
        max_fraction=per_trade_max_fraction,
    )
    if candidate <= 0.0:
        # Negative-EV or below threshold; nothing to gate.
        return 0.0

    # 2. Snapshot open positions across ALL skills for this agent.
    # client.get_positions() returns the canonical Position dataclass,
    # which the cap primitive accepts directly.
    open_positions = client.get_positions()

    # 3. Apply the portfolio-level cap BEFORE order placement.
    decision = check_portfolio_cap(
        candidate_size=candidate,
        bankroll=bankroll,
        open_positions=open_positions,
        total_cap_pct=total_cap_pct,
        agent_id=agent_id,
    )

    if decision.decision == "deny":
        print(
            f"[portfolio-cap] skip market={market_id} "
            f"reason={decision.reason} "
            f"open={decision.current_open_notional:.2f} "
            f"cap={decision.cap_notional:.2f}"
        )
        return 0.0

    if decision.decision == "trim_to":
        print(
            f"[portfolio-cap] trim market={market_id} "
            f"candidate={decision.candidate_size:.2f} "
            f"allowed={decision.allowed_size:.2f} "
            f"headroom={decision.headroom:.2f} "
            f"cap={decision.cap_notional:.2f}"
        )

    # 4. Place the trade at the (possibly trimmed) size.
    return decision.allowed_size


def run_multi_strategy_demo(client: SimmerClient, bankroll: float) -> None:
    """Demonstrates the cap acting across three concurrent strategies.

    The three calls share the same ``client`` (same agent, same positions
    store), so each call sees the cumulative open notional from prior
    fills. With a 15% total cap, the third strategy will typically be
    trimmed or denied even though each one's per-trade Kelly was modest.
    """
    candidates: Sequence[tuple[str, float, float]] = [
        ("market_92c_scalp", 0.95, 0.92),       # high-prob scalp
        ("market_1c_reversal", 0.05, 0.01),     # tail-reversal
        ("market_49c_mm", 0.55, 0.49),          # mean-reverting MM
    ]
    for mkt, p_win, price in candidates:
        size = run_one_market_with_portfolio_cap(
            client,
            market_id=mkt,
            p_win=p_win,
            market_price=price,
            bankroll=bankroll,
            agent_id="demo",
        )
        print(f"  -> placing {size:.2f} on {mkt}")


if __name__ == "__main__":  # pragma: no cover - manual driver
    api_key = os.environ.get("SIMMER_API_KEY")
    if not api_key:
        raise SystemExit("Set SIMMER_API_KEY to run this example")

    client = SimmerClient(api_key=api_key)
    summary = client.get_portfolio() or {}
    bankroll = float(summary.get("balance_usdc") or summary.get("sim_balance") or 0.0)
    if bankroll <= 0:
        raise SystemExit("No bankroll available; fund your account before running")

    run_multi_strategy_demo(client, bankroll=bankroll)
