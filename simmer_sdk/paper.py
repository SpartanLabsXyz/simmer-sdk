"""
Paper trading portfolio tracker.

Tracks simulated positions in memory for the duration of a single run.
No file I/O — positions reset when the process exits.
"""

from dataclasses import dataclass
from typing import Dict


@dataclass
class PaperPosition:
    """Tracked position from paper trades."""
    market_id: str
    shares_yes: float = 0.0
    shares_no: float = 0.0
    total_cost: float = 0.0


class PaperPortfolio:
    """In-memory paper portfolio for a single run."""

    def __init__(self):
        self.positions: Dict[str, PaperPosition] = {}

    def _apply_trade(self, trade: dict):
        """Update in-memory position from a trade record."""
        mid = trade["market_id"]
        if mid not in self.positions:
            self.positions[mid] = PaperPosition(market_id=mid)
        pos = self.positions[mid]
        shares = trade.get("shares_filled", 0)
        cost = trade.get("cost", 0)
        side_attr = f"shares_{trade['side']}"

        if trade["action"] == "buy":
            setattr(pos, side_attr, getattr(pos, side_attr) + shares)
            pos.total_cost += cost
        else:
            old_shares = getattr(pos, side_attr)
            removed = min(shares, old_shares)
            if old_shares > 0:
                pos.total_cost -= pos.total_cost * (removed / old_shares)
            setattr(pos, side_attr, max(0, old_shares - removed))

    def get_position(self, market_id: str) -> PaperPosition:
        """Get current paper position for a market."""
        return self.positions.get(market_id, PaperPosition(market_id=market_id))

    def log_trade(self, market_id: str, side: str, action: str,
                  shares: float, cost: float, price: float):
        """Record trade in memory and update positions."""
        entry = {
            "market_id": market_id,
            "side": side,
            "action": action,
            "shares_filled": shares,
            "cost": cost,
            "price": price,
        }
        self._apply_trade(entry)
