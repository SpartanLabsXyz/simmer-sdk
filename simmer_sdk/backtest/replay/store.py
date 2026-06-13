# vendored from simmer_v3/replay/store.py @ a759c3089aa2
# DO NOT EDIT HERE — regenerate via scripts/sync_replay_engine.py
"""HistoricalStore protocol + the frozen-clock view the replay server uses.

Look-ahead rule (load-bearing, see replay-contract.md): nothing dated > T is
ever served — including resolution outcomes, market metadata edits, and
anything derived. Enforcement lives HERE, in ReplayView, not in callers:
the replay server only ever holds a ReplayView frozen at the current tick,
so a skill cannot phrase a request that reaches future data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class MarketMeta:
    """Market metadata as knowable at listing time. NEVER carries outcome."""

    id: str
    question: str
    slug: str
    condition_id: str
    answer1: str
    answer2: str
    created_at: datetime
    end_date: Optional[datetime]
    event_id: Optional[str] = None
    event_title: Optional[str] = None
    neg_risk: bool = False
    volume: float = 0.0


@dataclass(frozen=True)
class PricePoint:
    market_id: str
    ts: datetime
    price: float  # unified-YES price, 0..1
    usd_amount: float = 0.0


@dataclass(frozen=True)
class TradePrint:
    market_id: str
    ts: datetime
    price: float
    usd_amount: float
    token_amount: float
    side: str  # BUY/SELL from taker perspective where the source provides it


@dataclass(frozen=True)
class Resolution:
    market_id: str
    outcome_yes: float  # 1.0 / 0.0, or fractional for scalar-ish resolutions
    resolved_at: datetime


@runtime_checkable
class HistoricalStore(Protocol):
    """Time-explicit reads over the historical tape.

    Implementations are dumb data access — every method takes explicit time
    bounds and must not apply look-ahead policy beyond honoring those bounds.
    Policy (clamping to the frozen tick) lives in ReplayView.
    """

    def markets(self, at: datetime, *, limit: int = 500, **filters) -> list[MarketMeta]:
        """Markets tradable at `at`: created_at <= at and not yet ended."""
        ...

    def price(self, market_id: str, at: datetime) -> Optional[PricePoint]:
        """Last tape print at or before `at`. None if no print yet."""
        ...

    def prices(self, market_id: str, t0: datetime, t1: datetime) -> list[PricePoint]:
        """Tape prints in [t0, t1], ascending."""
        ...

    def trades(self, market_id: str, t0: datetime, t1: datetime) -> list[TradePrint]:
        """Trade prints in [t0, t1], ascending."""
        ...

    def resolution(self, market_id: str) -> Optional[Resolution]:
        """Resolution outcome, or None if the market never resolved in the
        dataset. Time-gating is ReplayView's job — raw store returns it."""
        ...


class LookaheadError(RuntimeError):
    """A read attempted to reach past the frozen tick. Always a bug in the
    caller (or a probe test passing) — never user-visible in replay output."""


class ReplayView:
    """A HistoricalStore frozen at tick T. The ONLY store handle the replay
    server is allowed to hold while serving a skill's requests.

    Every read is clamped or rejected so that no data dated > T escapes:
      - markets()/price() are evaluated at T.
      - prices()/trades() windows are clamped to t1 <= T (raise on t0 > T).
      - resolution() is served ONLY if resolved_at <= T.
    """

    def __init__(self, store: HistoricalStore, at: datetime):
        if at.tzinfo is None:
            raise ValueError("ReplayView tick must be timezone-aware")
        self._store = store
        self._at = at

    @property
    def now(self) -> datetime:
        """The frozen clock. The replay server derives every time-dependent
        response field (e.g. seconds_to_expiry) from this, never wall clock."""
        return self._at

    def markets(self, *, limit: int = 500, **filters) -> list[MarketMeta]:
        return self._store.markets(self._at, limit=limit, **filters)

    def price(self, market_id: str) -> Optional[PricePoint]:
        return self._store.price(market_id, self._at)

    def prices(self, market_id: str, t0: datetime, t1: Optional[datetime] = None) -> list[PricePoint]:
        t1 = self._clamp_window(t0, t1)
        return self._store.prices(market_id, t0, t1)

    def trades(self, market_id: str, t0: datetime, t1: Optional[datetime] = None) -> list[TradePrint]:
        t1 = self._clamp_window(t0, t1)
        return self._store.trades(market_id, t0, t1)

    def resolution(self, market_id: str) -> Optional[Resolution]:
        res = self._store.resolution(market_id)
        if res is None or res.resolved_at > self._at:
            return None
        return res

    def _clamp_window(self, t0: datetime, t1: Optional[datetime]) -> datetime:
        if t0 > self._at:
            raise LookaheadError(f"window start {t0.isoformat()} is past frozen tick {self._at.isoformat()}")
        if t1 is None or t1 > self._at:
            return self._at
        return t1


def utc(*args: int) -> datetime:
    """Test/fixture helper: datetime(*args) in UTC."""
    return datetime(*args, tzinfo=timezone.utc)
