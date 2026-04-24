"""
Risk primitives for Simmer SDK.

DEPRECATED module — all exports scheduled for removal in simmer-sdk 0.12.0.
See simmer_sdk.risk.drawdown for the full rationale. Skill authors wanting
portfolio halt logic should compute drawdown from SimmerClient.get_briefing()
directly rather than wiring a client-side primitive.
"""

from .drawdown import DrawdownController, DrawdownState

__all__ = ["DrawdownController", "DrawdownState"]
