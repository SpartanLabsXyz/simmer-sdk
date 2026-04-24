"""
Partial-fill wait wrapper with time-boxed escape logic.

DEPRECATED: scheduled for removal in simmer-sdk 0.12.0.

`await_fill()` was shipped as a general-purpose wait-or-cancel wrapper, but
only applies to GTC/GTD orders — the default order type in Simmer skills is
FAK (Fill-And-Kill), which the exchange auto-cancels at submission, making
this wrapper a no-op. No first-party skill has adopted `await_fill`, and the
narrow use case (a skill explicitly using GTC/GTD with programmatic cancel
policy) is better handled with a short in-skill poll loop tuned to that
skill's strategy than a shared primitive with shared defaults.

If your skill genuinely needs GTC wait-and-cancel logic today, copy the
state machine into your skill and tune the thresholds to your strategy; the
source will remain available in 0.11.x for reference. We'll reconsider a
shared primitive when a first-party skill has concrete requirements.

`await_fill()` polls an open limit order's `size_matched` and returns one of
four statuses:

  1. FILLED           — filled/target >= accept_pct (default 0.95)
  2. PARTIAL          — early exit: filled/target >= partial_exit_pct AND
                         elapsed >= max_wait * partial_exit_time_frac
                         (default 0.50 past 70% of timeout)
  3. TIMEOUT_PARTIAL  — max_wait elapsed with filled > 0
  4. TIMEOUT_NO_FILL  — max_wait elapsed with zero fill
"""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Optional, Union

_DEPRECATION_MSG = (
    "simmer_sdk.execution.await_fill is deprecated and will be removed in "
    "simmer-sdk 0.12.0. The wrapper only applies to GTC/GTD orders (FAK, the "
    "default order type, auto-cancels at submission) and has no first-party "
    "adopters; skills that need GTC wait-and-cancel should tune a short poll "
    "loop to their own strategy. See https://docs.simmer.markets/sdk/execution."
)


class FillStatus(str, Enum):
    """Terminal status for `await_fill()`. String-valued for JSON logs."""

    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    TIMEOUT_PARTIAL = "TIMEOUT_PARTIAL"
    TIMEOUT_NO_FILL = "TIMEOUT_NO_FILL"


@dataclass
class FillResult:
    """Result of `await_fill()`. `cancel_result` and `cancel_error` are
    populated when a cancel was attempted."""

    status: FillStatus
    order_id: str
    target_size: float
    filled_size: float
    fill_ratio: float  # filled_size / target_size; non-negative, may exceed 1.0 if filled overshoots target
    elapsed: float  # seconds spent polling
    polls: int
    cancel_attempted: bool = False
    cancel_result: Optional[Dict[str, Any]] = None
    cancel_error: Optional[str] = None
    last_poll_error: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "order_id": self.order_id,
            "target_size": self.target_size,
            "filled_size": self.filled_size,
            "fill_ratio": self.fill_ratio,
            "elapsed": self.elapsed,
            "polls": self.polls,
            "cancel_attempted": self.cancel_attempted,
            "cancel_result": self.cancel_result,
            "cancel_error": self.cancel_error,
            "last_poll_error": self.last_poll_error,
            "extra": self.extra,
        }


# ---- Helpers for poll-result normalisation ---------------------------------


def _extract_filled(poll_result: Any) -> float:
    """Normalise a poll return value to a float `size_matched`.

    Accepts:
      - float / int  — treated as already the filled size
      - str          — parsed as float (Polymarket returns strings)
      - dict         — reads 'size_matched' (preferred) or 'filled' / 'matched'
    """
    if poll_result is None:
        return 0.0
    if isinstance(poll_result, (int, float)):
        return float(poll_result)
    if isinstance(poll_result, str):
        try:
            return float(poll_result)
        except ValueError:
            return 0.0
    if isinstance(poll_result, dict):
        for key in ("size_matched", "filled", "matched", "sizeMatched"):
            if key in poll_result and poll_result[key] is not None:
                try:
                    return float(poll_result[key])
                except (TypeError, ValueError):
                    continue
        return 0.0
    return 0.0


def _validate_thresholds(
    accept_pct: float, partial_exit_pct: float, partial_exit_time_frac: float
) -> None:
    if not 0.0 < accept_pct <= 1.0:
        raise ValueError(f"accept_pct must be in (0, 1], got {accept_pct}")
    if not 0.0 < partial_exit_pct <= 1.0:
        raise ValueError(
            f"partial_exit_pct must be in (0, 1], got {partial_exit_pct}"
        )
    if not 0.0 < partial_exit_time_frac <= 1.0:
        raise ValueError(
            f"partial_exit_time_frac must be in (0, 1], got {partial_exit_time_frac}"
        )
    if partial_exit_pct > accept_pct:
        # Not illegal but nonsensical — the partial window would never trigger
        # before the full-fill window. Warn loudly by raising.
        raise ValueError(
            "partial_exit_pct must be <= accept_pct "
            f"(got {partial_exit_pct} > {accept_pct})"
        )


# ---- Core primitive --------------------------------------------------------


def await_fill(
    order_id: str,
    target_size: float,
    max_wait: float,
    *,
    poll: Callable[[str], Any],
    cancel: Callable[[str], Any],
    accept_pct: float = 0.95,
    partial_exit_pct: float = 0.50,
    partial_exit_time_frac: float = 0.70,
    poll_interval: float = 2.0,
    _time: Callable[[], float] = time.monotonic,
    _sleep: Callable[[float], None] = time.sleep,
) -> FillResult:
    """Poll an open order and return one of four terminal `FillStatus` values.

    .. deprecated:: 0.11.2
       Removal scheduled for 0.12.0. See module docstring for rationale.

    Args:
        order_id: Order to poll.
        target_size: Requested size at submit time (same units as
            `size_matched`, usually contracts).
        max_wait: Hard timeout in seconds. Function is guaranteed to return
            before `elapsed > max_wait + poll_interval`.
        poll: Callable `(order_id) -> size_matched` (float) or order dict
            containing a `size_matched` / `filled` / `matched` field. Strings
            are accepted (Polymarket returns decimal strings). Exceptions are
            swallowed and the last error is surfaced on the result.
        cancel: Callable `(order_id) -> dict` invoked when the wrapper
            decides to cancel the remainder. Its return value is propagated
            to `FillResult.cancel_result`. Exceptions are caught and surfaced
            on `FillResult.cancel_error`.
        accept_pct: filled/target threshold for FILLED. Default 0.95.
        partial_exit_pct: filled/target threshold for early PARTIAL exit.
            Default 0.50.
        partial_exit_time_frac: elapsed/max_wait threshold that arms the
            PARTIAL exit window. Default 0.70 (i.e. after 70% of the timeout).
        poll_interval: seconds between polls. Default 2.0.
        _time, _sleep: injected for tests.

    Returns:
        FillResult with status and diagnostics.

    Raises:
        ValueError on out-of-range threshold configuration.
    """
    _validate_thresholds(accept_pct, partial_exit_pct, partial_exit_time_frac)

    warnings.warn(_DEPRECATION_MSG, DeprecationWarning, stacklevel=2)

    if target_size <= 0:
        raise ValueError(f"target_size must be > 0, got {target_size}")
    if max_wait <= 0:
        raise ValueError(f"max_wait must be > 0, got {max_wait}")
    if poll_interval <= 0:
        raise ValueError(f"poll_interval must be > 0, got {poll_interval}")

    start = _time()
    deadline = start + max_wait
    partial_arm_time = start + max_wait * partial_exit_time_frac

    filled = 0.0
    polls = 0
    last_poll_error: Optional[str] = None

    def _ratio(f: float) -> float:
        return f / target_size if target_size > 0 else 0.0

    def _elapsed() -> float:
        return _time() - start

    def _do_cancel() -> Dict[str, Any]:
        nonlocal last_poll_error  # only referenced
        try:
            result = cancel(order_id)
            return {"ok": True, "result": result, "error": None}
        except Exception as e:  # noqa: BLE001 — intentional catch-all
            return {"ok": False, "result": None, "error": str(e)}

    def _finish(
        status: FillStatus,
        do_cancel: bool,
    ) -> FillResult:
        cancel_attempted = False
        cancel_result: Optional[Dict[str, Any]] = None
        cancel_error: Optional[str] = None
        if do_cancel:
            cancel_attempted = True
            outcome = _do_cancel()
            cancel_result = outcome["result"]
            cancel_error = outcome["error"]
        return FillResult(
            status=status,
            order_id=order_id,
            target_size=target_size,
            filled_size=filled,
            fill_ratio=_ratio(filled),
            elapsed=_elapsed(),
            polls=polls,
            cancel_attempted=cancel_attempted,
            cancel_result=cancel_result,
            cancel_error=cancel_error,
            last_poll_error=last_poll_error,
        )

    while True:
        # Poll current fill state.
        try:
            raw = poll(order_id)
            filled = max(filled, _extract_filled(raw))  # monotonic
            last_poll_error = None
        except Exception as e:  # noqa: BLE001
            last_poll_error = str(e)
        polls += 1

        ratio = _ratio(filled)
        now = _time()

        # Path 1: FILLED — natural termination.
        if ratio >= accept_pct:
            # Cancel remainder only if less than full — a 100%-filled order
            # has no open size to cancel.
            should_cancel_remainder = ratio < 1.0
            return _finish(FillStatus.FILLED, do_cancel=should_cancel_remainder)

        # Path 2: PARTIAL — early exit once the partial window is armed.
        if now >= partial_arm_time and ratio >= partial_exit_pct:
            return _finish(FillStatus.PARTIAL, do_cancel=True)

        # Deadline check — done *after* one poll past the deadline so we
        # register any last-second fills.
        if now >= deadline:
            if filled > 0:
                return _finish(FillStatus.TIMEOUT_PARTIAL, do_cancel=True)
            return _finish(FillStatus.TIMEOUT_NO_FILL, do_cancel=True)

        # Sleep to the next poll, but never past the deadline.
        remaining = deadline - now
        _sleep(min(poll_interval, max(remaining, 0.0)))


# ---- Convenience wiring for py_clob_client ---------------------------------


def clob_poll_fn(clob_client: Any) -> Callable[[str], Dict[str, Any]]:
    """Return a `poll` callable for `await_fill()` backed by a
    `py_clob_client.ClobClient`. The CLOB returns a dict with
    `size_matched` as a string."""

    def _poll(order_id: str) -> Dict[str, Any]:
        order = clob_client.get_order(order_id)
        return order or {}

    return _poll


def clob_cancel_fn(clob_client: Any) -> Callable[[str], Dict[str, Any]]:
    """Return a `cancel` callable for `await_fill()` backed by a
    `py_clob_client.ClobClient`."""

    def _cancel(order_id: str) -> Dict[str, Any]:
        return clob_client.cancel(order_id)

    return _cancel
