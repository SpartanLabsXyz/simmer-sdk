"""
DrawdownController — portfolio-level circuit breaker.

Stateful peak-trough tracker. After every realized PnL event, a bot
calls `update(new_bankroll)`. Before placing a new order, the bot calls
`can_trade()`. When the drawdown from peak exceeds the configured
threshold, the controller latches into a halted state and stays halted
until the operator explicitly calls `resume()`.

Distinct from per-trade guardians (e.g. simulate-before-execute) — this
is portfolio-level and time-invariant. A bounce back to the peak does
NOT un-halt the controller; that decision is deliberately operator-gated.

Usage:
    from simmer_sdk.risk import DrawdownController

    dc = DrawdownController(bankroll=1000.0, max_drawdown_pct=0.15)

    # After every realized PnL event
    state = dc.update(current_bankroll)
    if state["halted"]:
        notify_operator(f"Halted at {state['drawdown']:.1%} drawdown")

    # Before every new order
    if not dc.can_trade():
        return  # skip trading this cycle

    # Operator-initiated recovery
    dc.resume()
"""

from __future__ import annotations

from typing import TypedDict


class DrawdownState(TypedDict):
    """Return shape of `DrawdownController.update()`."""

    drawdown: float
    halted: bool


class DrawdownController:
    """Portfolio-level drawdown circuit breaker with sticky halt.

    Args:
        bankroll: Starting bankroll. Used as the initial peak.
        max_drawdown_pct: Drawdown fraction (0 < x < 1) that triggers the
            halt. Halt triggers when `drawdown >= max_drawdown_pct`, so
            the boundary is inclusive. Default 0.15 (= 15%).

    Raises:
        ValueError: if `bankroll <= 0` or `max_drawdown_pct` is outside
            the open interval (0, 1).
    """

    def __init__(self, bankroll: float, max_drawdown_pct: float = 0.15) -> None:
        if bankroll <= 0:
            raise ValueError(f"bankroll must be positive, got {bankroll!r}")
        if not (0 < max_drawdown_pct < 1):
            raise ValueError(
                f"max_drawdown_pct must be in (0, 1), got {max_drawdown_pct!r}"
            )

        self._peak: float = float(bankroll)
        self._current: float = float(bankroll)
        self._max_drawdown_pct: float = float(max_drawdown_pct)
        self._halted: bool = False

    @property
    def peak(self) -> float:
        """Highest bankroll observed since construction (monotonic)."""
        return self._peak

    @property
    def current(self) -> float:
        """Most recent bankroll passed to `update()`."""
        return self._current

    @property
    def max_drawdown_pct(self) -> float:
        """The configured drawdown threshold."""
        return self._max_drawdown_pct

    @property
    def halted(self) -> bool:
        """Whether the controller is currently halted."""
        return self._halted

    @property
    def drawdown(self) -> float:
        """Current drawdown fraction from peak, in [0, 1]."""
        if self._peak <= 0:
            return 0.0
        dd = (self._peak - self._current) / self._peak
        # Clamp floor at 0 — new highs have no drawdown, not negative.
        return dd if dd > 0 else 0.0

    def update(self, new_bankroll: float) -> DrawdownState:
        """Record a new bankroll reading and recompute halt state.

        Bumps the peak on new highs. Triggers halt when drawdown from
        peak reaches or exceeds `max_drawdown_pct`. Once halted, stays
        halted — this method will not un-halt even if the bankroll
        recovers.

        Args:
            new_bankroll: Current total bankroll after the latest PnL
                event. Must be non-negative.

        Returns:
            A dict with `drawdown` (current drawdown fraction from peak,
            >= 0) and `halted` (bool).

        Raises:
            ValueError: if `new_bankroll` is negative.
        """
        if new_bankroll < 0:
            raise ValueError(
                f"new_bankroll must be non-negative, got {new_bankroll!r}"
            )

        self._current = float(new_bankroll)
        if self._current > self._peak:
            self._peak = self._current

        dd = self.drawdown
        if not self._halted and dd >= self._max_drawdown_pct:
            self._halted = True

        return {"drawdown": dd, "halted": self._halted}

    def can_trade(self) -> bool:
        """Return True iff the controller is not halted."""
        return not self._halted

    def resume(self) -> None:
        """Explicitly clear the halt flag.

        Operator-initiated recovery. Does NOT reset the peak — the next
        drawdown is still measured against the all-time high. To start
        fresh from the current bankroll as a new peak, instantiate a
        new controller.
        """
        self._halted = False

    def __repr__(self) -> str:
        return (
            f"DrawdownController(peak={self._peak:.2f}, "
            f"current={self._current:.2f}, "
            f"drawdown={self.drawdown:.4f}, "
            f"max_drawdown_pct={self._max_drawdown_pct:.4f}, "
            f"halted={self._halted})"
        )
