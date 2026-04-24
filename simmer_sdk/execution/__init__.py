"""
simmer_sdk.execution — execution-layer utilities for order lifecycle.

DEPRECATED module — `await_fill()` and its helpers are scheduled for removal
in simmer-sdk 0.12.0. See `simmer_sdk.execution.partial_fill` for the full
rationale and migration guidance.
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
