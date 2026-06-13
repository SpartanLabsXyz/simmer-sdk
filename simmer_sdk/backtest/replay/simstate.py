# vendored from simmer_v3/replay/simstate.py @ 96544b0f6a6c
# DO NOT EDIT HERE — regenerate via scripts/sync_replay_engine.py
"""SimState — the simulated agent portfolio during replay (SIM-3070).

Implements the v1 fill model from replay-contract.md, deliberately simple:

  - Marketable orders fill fully at the last tape print <= T. No slippage,
    no size impact, no partial fills.
  - Resting limit orders fill when the tape prints through the limit price
    (fill AT the limit price). No queue-position modeling.
  - Fees: flat taker fee rate on notional (default 0.0 — Polymarket's
    historical default; configurable per run).
  - Settlement: at resolution, YES shares pay outcome_yes each, NO shares
    pay (1 - outcome_yes).

Prices are unified-YES (0..1). NO-side trades convert via (1 - yes_price).
Every executed fill and settlement is appended to `fills` — the engine's
decision log and equity curve are derived from it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .store import ReplayView, Resolution


class ReplayTradeError(ValueError):
    """Order rejected — surfaces to the skill as a normal API error body."""


@dataclass
class Fill:
    ts: datetime
    market_id: str
    side: str            # "yes" | "no"
    action: str          # "buy" | "sell" | "settle"
    shares: float
    price: float         # per-share price paid/received in the side's terms
    usd: float           # signed cash delta (negative = cash out)
    fee: float
    kind: str            # "market" | "limit" | "settlement"
    order_id: Optional[str] = None


@dataclass
class Position:
    shares_yes: float = 0.0
    shares_no: float = 0.0
    # Cost basis tracked PER SIDE so partial sells can reduce it correctly
    # (holistic-review P2: a pooled basis was never reduced on sell, so the
    # /positions pnl a skill reads mid-run was understated after any exit).
    cost_basis_yes_usd: float = 0.0
    cost_basis_no_usd: float = 0.0

    @property
    def cost_basis_usd(self) -> float:
        return self.cost_basis_yes_usd + self.cost_basis_no_usd

    def add_basis(self, side: str, usd: float) -> None:
        if side == "yes":
            self.cost_basis_yes_usd += usd
        else:
            self.cost_basis_no_usd += usd

    def reduce_basis(self, side: str, shares_sold: float, held_before: float) -> None:
        """Reduce the side's basis proportionally to the fraction of shares sold."""
        if held_before <= 0:
            return
        frac = min(1.0, shares_sold / held_before)
        if side == "yes":
            self.cost_basis_yes_usd *= (1.0 - frac)
        else:
            self.cost_basis_no_usd *= (1.0 - frac)


@dataclass
class LimitOrder:
    order_id: str
    market_id: str
    side: str            # "yes" | "no"
    action: str          # "buy" | "sell"
    limit_price: float   # in the side's own terms (NO orders quoted in NO price)
    usd_amount: float    # for buys: max spend; for sells: shares instead
    shares: float = 0.0  # for sells
    placed_at: Optional[datetime] = None


def _side_price(yes_price: float, side: str) -> float:
    return yes_price if side == "yes" else 1.0 - yes_price


class SimState:
    """Mutable per-replay portfolio. One instance per replay job/session."""

    def __init__(self, starting_balance: float = 1000.0, fee_rate: float = 0.0):
        self.cash = float(starting_balance)
        self.starting_balance = float(starting_balance)
        self.fee_rate = float(fee_rate)
        self.positions: dict[str, Position] = {}
        self.open_orders: dict[str, LimitOrder] = {}
        self.fills: list[Fill] = []
        self._order_seq = 0

    # -- marketable orders ---------------------------------------------------

    def buy(self, view: ReplayView, market_id: str, side: str, usd_amount: float,
            limit_price: Optional[float] = None) -> Fill | LimitOrder:
        side = _norm_side(side)
        if usd_amount <= 0:
            raise ReplayTradeError("usd_amount must be positive")
        point = view.price(market_id)
        if point is None:
            raise ReplayTradeError(f"no tape price yet for {market_id}")
        px = _side_price(point.price, side)

        if limit_price is not None and px > limit_price:
            return self._rest_order(view, market_id, side, "buy", limit_price, usd_amount=usd_amount)

        fee = usd_amount * self.fee_rate
        total = usd_amount + fee
        if total > self.cash + 1e-9:
            raise ReplayTradeError(f"insufficient balance: need {total:.2f}, have {self.cash:.2f}")
        if px <= 0:
            raise ReplayTradeError(f"non-positive {side} price {px}")
        shares = usd_amount / px
        self.cash -= total
        pos = self.positions.setdefault(market_id, Position())
        if side == "yes":
            pos.shares_yes += shares
        else:
            pos.shares_no += shares
        pos.add_basis(side, usd_amount)
        fill = Fill(ts=view.now, market_id=market_id, side=side, action="buy",
                    shares=shares, price=px, usd=-total, fee=fee, kind="market")
        self.fills.append(fill)
        return fill

    def sell(self, view: ReplayView, market_id: str, side: str, shares: float,
             limit_price: Optional[float] = None) -> Fill | LimitOrder:
        side = _norm_side(side)
        pos = self.positions.get(market_id)
        held = (pos.shares_yes if side == "yes" else pos.shares_no) if pos else 0.0
        if shares <= 0 or shares > held + 1e-9:
            raise ReplayTradeError(f"cannot sell {shares} {side} shares, hold {held}")
        point = view.price(market_id)
        if point is None:
            raise ReplayTradeError(f"no tape price yet for {market_id}")
        px = _side_price(point.price, side)

        if limit_price is not None and px < limit_price:
            return self._rest_order(view, market_id, side, "sell", limit_price, shares=shares)

        proceeds = shares * px
        fee = proceeds * self.fee_rate
        self.cash += proceeds - fee
        pos.reduce_basis(side, shares, held)
        if side == "yes":
            pos.shares_yes -= shares
        else:
            pos.shares_no -= shares
        fill = Fill(ts=view.now, market_id=market_id, side=side, action="sell",
                    shares=shares, price=px, usd=proceeds - fee, fee=fee, kind="market")
        self.fills.append(fill)
        return fill

    # -- resting limit orders ------------------------------------------------

    def _rest_order(self, view: ReplayView, market_id: str, side: str, action: str,
                    limit_price: float, usd_amount: float = 0.0, shares: float = 0.0) -> LimitOrder:
        if not (0.0 < limit_price < 1.0):
            raise ReplayTradeError(f"limit_price must be in (0,1), got {limit_price}")
        self._order_seq += 1
        order = LimitOrder(order_id=f"o{self._order_seq}", market_id=market_id, side=side,
                           action=action, limit_price=limit_price,
                           usd_amount=usd_amount, shares=shares, placed_at=view.now)
        self.open_orders[order.order_id] = order
        return order

    def cancel(self, order_id: str) -> bool:
        return self.open_orders.pop(order_id, None) is not None

    def advance_fills(self, view: ReplayView, since: datetime) -> list[Fill]:
        """Check resting orders against tape prints in (since, view.now].

        Fill rule (contract v1): a buy at limit L fills if any print's
        side-price <= L; a sell fills if any print's side-price >= L.
        Fill price is the LIMIT price. Called by the engine once per tick.
        """
        executed: list[Fill] = []
        for order in list(self.open_orders.values()):
            prints = view.prices(order.market_id, since)
            if not prints:
                continue
            crossed = False
            for p in prints:
                # interval is (since, now] — the store's BETWEEN is inclusive on
                # the left, but a print AT `since` was already visible when the
                # order was placed (holistic-review P2: don't fill from it).
                if p.ts <= since:
                    continue
                spx = _side_price(p.price, order.side)
                if order.action == "buy" and spx <= order.limit_price + 1e-12:
                    crossed = True
                    break
                if order.action == "sell" and spx >= order.limit_price - 1e-12:
                    crossed = True
                    break
            if not crossed:
                continue
            del self.open_orders[order.order_id]
            fill = self._execute_at_limit(view, order)
            if fill is not None:
                executed.append(fill)
        return executed

    def _execute_at_limit(self, view: ReplayView, order: LimitOrder) -> Optional[Fill]:
        px = order.limit_price
        if order.action == "buy":
            fee = order.usd_amount * self.fee_rate
            total = order.usd_amount + fee
            if total > self.cash + 1e-9:
                return None  # order lapses — insufficient cash at cross time
            shares = order.usd_amount / px
            self.cash -= total
            pos = self.positions.setdefault(order.market_id, Position())
            if order.side == "yes":
                pos.shares_yes += shares
            else:
                pos.shares_no += shares
            pos.add_basis(order.side, order.usd_amount)
            fill = Fill(ts=view.now, market_id=order.market_id, side=order.side, action="buy",
                        shares=shares, price=px, usd=-total, fee=fee, kind="limit",
                        order_id=order.order_id)
        else:
            pos = self.positions.get(order.market_id)
            held = (pos.shares_yes if order.side == "yes" else pos.shares_no) if pos else 0.0
            shares = min(order.shares, held)
            if shares <= 0:
                return None
            proceeds = shares * px
            fee = proceeds * self.fee_rate
            self.cash += proceeds - fee
            pos.reduce_basis(order.side, shares, held)
            if order.side == "yes":
                pos.shares_yes -= shares
            else:
                pos.shares_no -= shares
            fill = Fill(ts=view.now, market_id=order.market_id, side=order.side, action="sell",
                        shares=shares, price=px, usd=proceeds - fee, fee=fee, kind="limit",
                        order_id=order.order_id)
        self.fills.append(fill)
        return fill

    # -- settlement + valuation ----------------------------------------------

    def settle(self, view: ReplayView) -> list[Fill]:
        """Settle any held position whose market has resolved as of the tick.

        ReplayView time-gates resolution, so calling every tick is safe —
        a market settles on the first tick at/after its resolved_at.
        """
        settled: list[Fill] = []
        for market_id, pos in list(self.positions.items()):
            if pos.shares_yes <= 0 and pos.shares_no <= 0:
                del self.positions[market_id]
                continue
            res: Optional[Resolution] = view.resolution(market_id)
            if res is None:
                continue
            payout = pos.shares_yes * res.outcome_yes + pos.shares_no * (1.0 - res.outcome_yes)
            self.cash += payout
            fill = Fill(ts=view.now, market_id=market_id,
                        side="yes" if pos.shares_yes >= pos.shares_no else "no",
                        action="settle", shares=pos.shares_yes + pos.shares_no,
                        price=res.outcome_yes, usd=payout, fee=0.0, kind="settlement")
            self.fills.append(fill)
            settled.append(fill)
            # resting orders on a resolved market lapse
            for oid, o in list(self.open_orders.items()):
                if o.market_id == market_id:
                    del self.open_orders[oid]
            del self.positions[market_id]
        return settled

    def equity(self, view: ReplayView) -> float:
        """Cash + mark-to-tape value of open positions at the tick."""
        total = self.cash
        for market_id, pos in self.positions.items():
            point = view.price(market_id)
            if point is None:
                continue
            total += pos.shares_yes * point.price + pos.shares_no * (1.0 - point.price)
        return total


def _norm_side(side: str) -> str:
    s = (side or "").strip().lower()
    if s in ("yes", "y", "long"):
        return "yes"
    if s in ("no", "n", "short"):
        return "no"
    raise ReplayTradeError(f"side must be yes/no, got {side!r}")
