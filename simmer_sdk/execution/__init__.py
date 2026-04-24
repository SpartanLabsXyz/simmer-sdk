"""
simmer_sdk.execution — execution-layer utilities for order lifecycle.

Currently exposes the partial-fill wait-vs-cancel wrapper (SIM-1079). This is
the execution-time counterpart to the SIM-917 backfill/accounting work: it
gives SDK callers a first-class `await_fill()` primitive for structuring the
wait-or-cancel decision on limit orders.
"""

from .partial_fill import (
    FillStatus,
    FillResult,
    await_fill,
    clob_poll_fn,
    clob_cancel_fn,
)

__all__ = [
    "FillStatus",
    "FillResult",
    "await_fill",
    "clob_poll_fn",
    "clob_cancel_fn",
]
