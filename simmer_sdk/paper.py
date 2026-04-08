"""
Paper trading portfolio tracker.

Tracks simulated positions in memory for the duration of a single run.
No file I/O — positions reset when the process exits.

Supports:
- Balance tracking (starting capital, realized P&L)
- Auto-settlement on market resolution
- Position queries compatible with SimmerClient.get_positions() format
"""

import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_STARTING_BALANCE = 10_000.0


@dataclass
class PaperPosition:
    """Tracked position from paper trades."""
    market_id: str
    shares_yes: float = 0.0
    shares_no: float = 0.0
    total_cost: float = 0.0


@dataclass
class PaperSettlement:
    """Record of a settled paper position."""
    market_id: str
    outcome: str  # "yes" or "no"
    shares_won: float
    shares_lost: float
    payout: float  # Amount credited to balance
    cost_basis: float  # Original cost of the position
    pnl: float  # payout - cost_basis
    settled_at: float = field(default_factory=time.time)


class PaperPortfolio:
    """In-memory paper portfolio for a single run.

    Tracks a virtual balance, simulated positions, and auto-settles
    when markets resolve.

    Args:
        starting_balance: Initial virtual capital (default: 10,000).
    """

    def __init__(self, starting_balance: float = DEFAULT_STARTING_BALANCE):
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.positions: Dict[str, PaperPosition] = {}
        self.settlements: List[PaperSettlement] = []
        self._trade_log: List[dict] = []

    @property
    def total_pnl(self) -> float:
        """Total realized P&L from settlements."""
        return sum(s.pnl for s in self.settlements)

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
            self.balance -= cost
        else:
            old_shares = getattr(pos, side_attr)
            removed = min(shares, old_shares)
            if old_shares > 0:
                pos.total_cost -= pos.total_cost * (removed / old_shares)
            setattr(pos, side_attr, max(0, old_shares - removed))
            self.balance += cost

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
            "timestamp": time.time(),
        }
        self._trade_log.append(entry)
        self._apply_trade(entry)

    def settle(self, market_id: str, outcome: str) -> Optional[PaperSettlement]:
        """Settle a paper position when the market resolves.

        Args:
            market_id: The resolved market's ID.
            outcome: Resolution outcome — "yes" or "no".

        Returns:
            PaperSettlement record, or None if no position to settle.
        """
        pos = self.positions.get(market_id)
        if pos is None or (pos.shares_yes <= 0 and pos.shares_no <= 0):
            return None

        # Winning shares pay $1 each, losing shares pay $0
        if outcome == "yes":
            payout = pos.shares_yes
            shares_won = pos.shares_yes
            shares_lost = pos.shares_no
        else:
            payout = pos.shares_no
            shares_won = pos.shares_no
            shares_lost = pos.shares_yes

        pnl = payout - pos.total_cost
        self.balance += payout

        settlement = PaperSettlement(
            market_id=market_id,
            outcome=outcome,
            shares_won=shares_won,
            shares_lost=shares_lost,
            payout=round(payout, 4),
            cost_basis=round(pos.total_cost, 4),
            pnl=round(pnl, 4),
        )
        self.settlements.append(settlement)
        del self.positions[market_id]

        logger.info(
            "Paper settlement: market=%s outcome=%s payout=%.2f pnl=%+.2f",
            market_id, outcome, payout, pnl
        )
        return settlement

    def get_open_market_ids(self) -> List[str]:
        """Return market IDs with open paper positions."""
        return [
            mid for mid, pos in self.positions.items()
            if pos.shares_yes > 0 or pos.shares_no > 0
        ]

    def summary(self) -> dict:
        """Return a summary of the paper portfolio state."""
        open_positions = {
            mid: {
                "shares_yes": pos.shares_yes,
                "shares_no": pos.shares_no,
                "cost_basis": round(pos.total_cost, 4),
            }
            for mid, pos in self.positions.items()
            if pos.shares_yes > 0 or pos.shares_no > 0
        }
        return {
            "starting_balance": self.starting_balance,
            "balance": round(self.balance, 4),
            "total_pnl": round(self.total_pnl, 4),
            "open_positions": len(open_positions),
            "settled_positions": len(self.settlements),
            "positions": open_positions,
        }
