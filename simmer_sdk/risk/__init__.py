"""
Risk-management primitives for Simmer SDK skills.

A *risk primitive* is a pre-trade guard: before a strategy places an order,
it asks the primitive whether the candidate size is admissible given the
agent's current state (open exposure, recent drawdown, daily loss, …).
Each primitive returns a structured decision (allow / deny / trim) so the
caller can act deterministically without implementing its own risk math.

Currently provides:

- ``check_portfolio_cap`` — portfolio-level concurrent-exposure cap.
  Sums current open notional across all skills/strategies for an agent and
  enforces a hard ceiling as a fraction of bankroll. Returns
  ``allow`` / ``deny`` / ``trim_to``. This is a *cross-skill* ceiling that
  layers on top of any per-trade Kelly cap from ``simmer_sdk.sizing`` —
  per-trade Kelly says how big *this* trade may be, the portfolio cap says
  how big *all open trades together* may be.

Distinction from drawdown control (see SIM-1072 ``DrawdownController``,
not yet shipped): the portfolio cap is a *forward-looking entry gate*
("sum of open exposure ≤ X% of bankroll") — it gates new entries based on
already-open positions. A drawdown controller is a *backward-looking halt*
("peak-to-trough loss ≥ Y% halts new trades for Z hours") — it gates new
entries based on realized P&L. They are complementary and live separately.

Example:
    from simmer_sdk.risk import check_portfolio_cap
    from simmer_sdk.sizing import size_position

    candidate = size_position(p_win=0.62, market_price=0.55, bankroll=10_000.0)

    decision = check_portfolio_cap(
        candidate_size=candidate,
        bankroll=10_000.0,
        open_positions=client.get_positions(),
        total_cap_pct=0.15,
        agent_id="agent_123",
    )
    if decision.decision == "deny":
        return  # at cap — skip
    final_size = decision.allowed_size  # candidate, or trimmed to fit

The primitive is opt-in (off by default). Skills wire it explicitly before
order placement; nothing inside the SDK calls it implicitly.
"""

from __future__ import annotations

from .portfolio_cap import (
    DEFAULT_TOTAL_CAP_PCT,
    PORTFOLIO_CAP_CONFIG_SCHEMA,
    PortfolioCapDecision,
    check_portfolio_cap,
    sum_open_notional,
)

__all__ = [
    "PortfolioCapDecision",
    "check_portfolio_cap",
    "sum_open_notional",
    "DEFAULT_TOTAL_CAP_PCT",
    "PORTFOLIO_CAP_CONFIG_SCHEMA",
]
