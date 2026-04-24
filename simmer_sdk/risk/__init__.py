"""
Risk primitives for Simmer SDK.

Portfolio-level risk controls that a trading skill composes on top of
per-trade sizing. These primitives are intentionally stateless about the
Simmer backend — they track local bot state and decide whether the bot
should keep trading.

Currently exports:
    DrawdownController — peak-trough tracker with sticky auto-halt.
"""

from .drawdown import DrawdownController, DrawdownState

__all__ = ["DrawdownController", "DrawdownState"]
