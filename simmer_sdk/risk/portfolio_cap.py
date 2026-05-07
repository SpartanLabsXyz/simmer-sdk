"""
Portfolio-level concurrent-exposure cap.

Enforces a hard ceiling on the total open notional an agent may hold across
all skills/strategies, expressed as a fraction of bankroll. Returns one of
three decisions for any candidate trade:

    "allow"   — candidate fits under the cap; place at requested size.
    "deny"    — agent is already at or above the cap; skip the trade.
    "trim_to" — candidate would breach the cap; place at the largest size
                that still fits (``allowed_size`` < ``candidate_size``).

This complements per-trade sizing in :mod:`simmer_sdk.sizing` (Kelly and
fractional-Kelly cap how big *this* trade may be) by adding a cross-skill
ceiling on *all* open trades. A multi-strategy agent running, for example,
a 92¢ scalp + a 1¢ reversal + a 49¢ market-maker can use the per-trade cap
on each leg and still bound total open exposure.

Distinct from the SIM-1072 ``DrawdownController`` (not yet shipped):

    portfolio cap         — forward-looking entry gate (open exposure ≤ X% bankroll)
    DrawdownController    — backward-looking halt (peak-to-trough loss ≥ Y% halts entries)

Both can be wired in the same skill; they answer different questions.

Concurrency
-----------
The primitive itself is pure: given the same inputs it always returns the
same decision. There is no shared mutable state, so it is safe to call
from many threads/processes concurrently. What it *cannot* do at the
primitive layer is solve TOCTOU between fetching positions, deciding, and
placing the order — two concurrent skills can each see the same snapshot
and each place a "fits" trade that together breach the cap. Callers that
need stricter guarantees should hold a lock around fetch → decide → place,
or accept eventual breach within a small window. The cap is designed for
the common case where each skill places trades serially and breaches are
small relative to the cap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

# Default portfolio-level concurrent-exposure cap as a fraction of bankroll.
# 15% mirrors the cross-strategy ceiling stacyonchain runs on top of an 8%
# per-trade Kelly hard cap; operators SHOULD tune per agent risk appetite.
DEFAULT_TOTAL_CAP_PCT = 0.15

_VALID_DECISIONS = ("allow", "deny", "trim_to")


@dataclass(frozen=True)
class PortfolioCapDecision:
    """Result of a portfolio-level exposure-cap check.

    Attributes:
        decision: One of ``"allow"``, ``"deny"``, ``"trim_to"``.
        allowed_size: The size the caller may place. Equal to
            ``candidate_size`` for ``allow``, ``0.0`` for ``deny``, and
            ``cap_notional - current_open_notional`` for ``trim_to``.
        candidate_size: The size the caller proposed (echoed back).
        current_open_notional: Sum of open notional across all positions
            considered. Source of the cross-skill aggregation.
        cap_notional: ``bankroll * total_cap_pct`` — the absolute ceiling.
        headroom: ``cap_notional - current_open_notional``. Negative values
            mean the agent is already over-cap (typically yields ``deny``).
        reason: Short machine-readable code explaining the decision
            (e.g. ``"under_cap"``, ``"at_cap"``, ``"would_exceed_cap"``,
            ``"invalid_bankroll"``).
        agent_id: Optional caller-provided identifier, echoed back unchanged
            for log correlation. Not used in the decision.
    """

    decision: str
    allowed_size: float
    candidate_size: float
    current_open_notional: float
    cap_notional: float
    headroom: float
    reason: str
    agent_id: Optional[str] = None

    def __post_init__(self) -> None:  # pragma: no cover - guard only
        if self.decision not in _VALID_DECISIONS:
            raise ValueError(
                f"PortfolioCapDecision.decision must be one of "
                f"{_VALID_DECISIONS!r}, got {self.decision!r}"
            )


def _position_notional(pos: Any) -> float:
    """Best-effort extraction of open notional from a position-like object.

    Accepts:
        - ``simmer_sdk.client.Position`` (or any object with a
          ``current_value`` attribute).
        - ``dict`` with a ``"current_value"`` (preferred) or
          ``"notional"`` key.
        - ``int`` / ``float`` raw notional.

    Returns ``0.0`` for anything we cannot interpret or that yields a
    non-finite / non-numeric value. Closed positions (``status`` ==
    ``"closed"`` or zero shares) contribute ``0.0`` if those fields are
    present and unambiguous.
    """
    if pos is None:
        return 0.0

    # Raw numeric — caller already aggregated.
    if isinstance(pos, (int, float)) and not isinstance(pos, bool):
        return float(pos) if _is_finite_nonneg(pos) else 0.0

    # dict-style position payload.
    if isinstance(pos, dict):
        status = pos.get("status")
        if status == "closed":
            return 0.0
        for key in ("current_value", "notional", "open_notional"):
            if key in pos and pos[key] is not None:
                val = pos[key]
                if isinstance(val, (int, float)) and _is_finite_nonneg(val):
                    return float(val)
                return 0.0
        return 0.0

    # Object-style position (e.g. SDK Position dataclass) — duck-typed.
    status = getattr(pos, "status", None)
    if status == "closed":
        return 0.0
    val = getattr(pos, "current_value", None)
    if val is None:
        return 0.0
    if isinstance(val, (int, float)) and _is_finite_nonneg(val):
        return float(val)
    return 0.0


def _is_finite_nonneg(x: float) -> bool:
    """True for finite, non-negative numbers (rejects NaN, inf, negatives)."""
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return False
    if xf != xf:  # NaN check (NaN != NaN)
        return False
    if xf in (float("inf"), float("-inf")):
        return False
    return xf >= 0.0


def sum_open_notional(positions: Iterable[Any]) -> float:
    """Sum open notional across a sequence of position-like records.

    See :func:`_position_notional` for accepted shapes. Records that cannot
    be interpreted contribute ``0.0`` (fail-quiet) — this primitive must
    not raise on unfamiliar payloads, since callers may aggregate positions
    from multiple venues with slightly different shapes. If you need strict
    parsing, pre-process positions yourself and pass the sum via
    ``current_open_notional`` to :func:`check_portfolio_cap`.
    """
    if positions is None:
        return 0.0
    total = 0.0
    for p in positions:
        total += _position_notional(p)
    return total


def check_portfolio_cap(
    candidate_size: float,
    bankroll: float,
    *,
    open_positions: Optional[Iterable[Any]] = None,
    current_open_notional: Optional[float] = None,
    total_cap_pct: float = DEFAULT_TOTAL_CAP_PCT,
    agent_id: Optional[str] = None,
) -> PortfolioCapDecision:
    """Decide whether ``candidate_size`` is admissible under the portfolio cap.

    Args:
        candidate_size: Dollar size the strategy proposes to allocate to
            the next trade. Must be ``>= 0``. Negative values are treated
            as ``0`` (no trade).
        bankroll: Available capital denominated in the same units as
            positions' ``current_value``. Must be ``> 0``; otherwise the
            primitive returns ``deny`` with reason ``"invalid_bankroll"``.
        open_positions: Iterable of position records (SDK ``Position``
            objects, dicts with ``current_value``, or pre-summed numbers).
            Mutually exclusive with ``current_open_notional``.
        current_open_notional: Pre-aggregated open notional across all
            skills/strategies. Use when you already summed positions
            yourself (e.g. for a custom venue or a cached value). Mutually
            exclusive with ``open_positions``.
        total_cap_pct: Fraction of bankroll allowed as total open notional.
            Defaults to :data:`DEFAULT_TOTAL_CAP_PCT` (0.15). Must be in
            ``(0, 1]``; outside this range the primitive returns ``deny``
            with reason ``"invalid_cap_pct"``.
        agent_id: Optional identifier echoed back on the decision for log
            correlation. Not used in the decision.

    Returns:
        A :class:`PortfolioCapDecision` describing the verdict and the
        size the caller may safely place.

    Raises:
        ValueError: If both ``open_positions`` and ``current_open_notional``
            are supplied (the inputs are ambiguous).

    Example:
        >>> from simmer_sdk.risk import check_portfolio_cap
        >>> d = check_portfolio_cap(
        ...     candidate_size=200.0,
        ...     bankroll=10_000.0,
        ...     current_open_notional=1_400.0,  # 14% of bankroll already open
        ...     total_cap_pct=0.15,
        ... )
        >>> d.decision, round(d.allowed_size, 2)
        ('trim_to', 100.0)
    """
    if open_positions is not None and current_open_notional is not None:
        raise ValueError(
            "check_portfolio_cap: pass either open_positions or "
            "current_open_notional, not both"
        )

    # Validate bankroll first — without it we cannot compute a cap.
    if not _is_finite_nonneg(bankroll) or bankroll <= 0.0:
        return PortfolioCapDecision(
            decision="deny",
            allowed_size=0.0,
            candidate_size=max(0.0, float(candidate_size) if _is_finite_nonneg(candidate_size) else 0.0),
            current_open_notional=0.0,
            cap_notional=0.0,
            headroom=0.0,
            reason="invalid_bankroll",
            agent_id=agent_id,
        )

    # Validate cap pct — must be in (0, 1].
    if not _is_finite_nonneg(total_cap_pct) or total_cap_pct <= 0.0 or total_cap_pct > 1.0:
        return PortfolioCapDecision(
            decision="deny",
            allowed_size=0.0,
            candidate_size=max(0.0, float(candidate_size) if _is_finite_nonneg(candidate_size) else 0.0),
            current_open_notional=0.0,
            cap_notional=0.0,
            headroom=0.0,
            reason="invalid_cap_pct",
            agent_id=agent_id,
        )

    # Normalize candidate (negatives / NaN -> 0).
    cand = float(candidate_size) if _is_finite_nonneg(candidate_size) else 0.0

    # Aggregate open notional.
    if current_open_notional is not None:
        if not _is_finite_nonneg(current_open_notional):
            open_notional = 0.0
        else:
            open_notional = float(current_open_notional)
    else:
        open_notional = sum_open_notional(open_positions or ())

    cap_notional = float(bankroll) * float(total_cap_pct)
    headroom = cap_notional - open_notional

    # Candidate of zero is a no-op; report allow with reason "no_candidate"
    # so the caller can distinguish from a trimmed-to-zero outcome.
    if cand <= 0.0:
        return PortfolioCapDecision(
            decision="allow",
            allowed_size=0.0,
            candidate_size=0.0,
            current_open_notional=open_notional,
            cap_notional=cap_notional,
            headroom=headroom,
            reason="no_candidate",
            agent_id=agent_id,
        )

    # Already at or above cap -> deny outright. Headroom <= 0 means no
    # bytes left to allocate. Use a small tolerance to absorb fp noise.
    EPSILON = 1e-9
    if headroom <= EPSILON:
        return PortfolioCapDecision(
            decision="deny",
            allowed_size=0.0,
            candidate_size=cand,
            current_open_notional=open_notional,
            cap_notional=cap_notional,
            headroom=headroom,
            reason="at_cap" if abs(headroom) <= EPSILON else "over_cap",
            agent_id=agent_id,
        )

    # Candidate fits entirely under the cap.
    if cand <= headroom + EPSILON:
        return PortfolioCapDecision(
            decision="allow",
            allowed_size=cand,
            candidate_size=cand,
            current_open_notional=open_notional,
            cap_notional=cap_notional,
            headroom=headroom,
            reason="under_cap",
            agent_id=agent_id,
        )

    # Candidate would breach -> trim to remaining headroom.
    return PortfolioCapDecision(
        decision="trim_to",
        allowed_size=max(0.0, headroom),
        candidate_size=cand,
        current_open_notional=open_notional,
        cap_notional=cap_notional,
        headroom=headroom,
        reason="would_exceed_cap",
        agent_id=agent_id,
    )


# Config schema skills can merge into their CONFIG_SCHEMA to expose the cap
# as a config-driven knob. Default is `enabled=False` (opt-in per ticket).
#
# Usage in a skill:
#     from simmer_sdk.risk import PORTFOLIO_CAP_CONFIG_SCHEMA
#     CONFIG_SCHEMA = {
#         "my_param": {"env": "MY_PARAM", "default": 42, "type": int},
#         **PORTFOLIO_CAP_CONFIG_SCHEMA,
#     }
PORTFOLIO_CAP_CONFIG_SCHEMA = {
    "portfolio_cap_enabled": {
        "env": "SIMMER_PORTFOLIO_CAP_ENABLED",
        "default": False,
        "type": bool,
    },
    "portfolio_cap_pct": {
        "env": "SIMMER_PORTFOLIO_CAP_PCT",
        "default": DEFAULT_TOTAL_CAP_PCT,
        "type": float,
    },
}
