# vendored from simmer_v3/replay/duckdb_store.py @ befeed1b328b
# DO NOT EDIT HERE — regenerate via scripts/sync_replay_engine.py
"""DuckDB-backed HistoricalStore over the Polymarket trade-tape parquet set.

Source dataset: HuggingFace `SII-WANGZJ/Polymarket_data` (MIT). Verified
schema 2026-06-11 (remote httpfs peek):

  markets.parquet  id, question, slug, condition_id, token1/2, answer1/2,
                   closed, active, archived, outcome_prices (JSON text),
                   volume, event_id, event_slug, event_title,
                   created_at/end_date/updated_at (TIMESTAMPTZ), neg_risk
  quant.parquet    timestamp (unix sec, UBIGINT), block_number, tx hash,
                   log_index, market_id, condition_id, event_id, price,
                   usd_amount, token_amount, side, maker, taker

Paths may be local files (staged bucket copy — production) or https URLs
(httpfs — dev/probe only; do not ship replay jobs on remote reads).

Known fidelity caveat (documented in replay-contract.md): the dataset has no
explicit resolved_at; we use end_date as the resolution timestamp proxy.
Real resolutions can land after end_date (disputes). Conservative for
look-ahead purposes: end_date <= true resolved_at, so a ReplayView at T may
*under*-serve resolutions near the boundary, never over-serve... except for
disputed markets whose outcome changed post-end_date — acceptable for v1
decision-quality scoring; revisit with Struct data.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from typing import Optional

import duckdb

from .store import MarketMeta, PricePoint, Resolution, TradePrint


class DuckDBStore:
    """HistoricalStore implementation. One connection, read-only views."""

    def __init__(self, markets_path: str, quant_path: str, trades_path: Optional[str] = None):
        self._con = duckdb.connect()
        if any(p.startswith(("http://", "https://")) for p in (markets_path, quant_path, trades_path or "")):
            self._con.execute("INSTALL httpfs; LOAD httpfs;")
        self._con.execute(
            f"CREATE VIEW m AS SELECT * FROM read_parquet('{markets_path}')"
        )
        self._con.execute(
            f"CREATE VIEW q AS SELECT * FROM read_parquet('{quant_path}')"
        )
        if trades_path:
            self._con.execute(
                f"CREATE VIEW t AS SELECT * FROM read_parquet('{trades_path}')"
            )
        self._has_trades = trades_path is not None

    def close(self) -> None:
        """Release the DuckDB connection — workers must call this per job
        (holistic-review: connections otherwise leak FDs across queue jobs)."""
        self._con.close()

    # -- HistoricalStore ----------------------------------------------------

    def markets(self, at: datetime, *, limit: int = 500, **filters) -> list[MarketMeta]:
        clauses = ["created_at <= ?", "(end_date IS NULL OR end_date > ?)"]
        params: list = [at, at]
        if "event_id" in filters:
            clauses.append("event_id = ?")
            params.append(filters["event_id"])
        if "slug_like" in filters:
            clauses.append("slug LIKE ?")
            params.append(filters["slug_like"])
        params.append(limit)
        rows = self._con.execute(
            f"""SELECT id, question, slug, condition_id, answer1, answer2,
                       created_at, end_date, event_id, event_title, neg_risk, volume
                FROM m WHERE {' AND '.join(clauses)}
                ORDER BY volume DESC LIMIT ?""",
            params,
        ).fetchall()
        return [
            MarketMeta(
                id=r[0], question=r[1], slug=r[2], condition_id=r[3],
                answer1=r[4], answer2=r[5],
                created_at=_aware(r[6]), end_date=_aware(r[7]),
                event_id=r[8], event_title=r[9],
                neg_risk=bool(r[10]), volume=float(r[11] or 0.0),
            )
            for r in rows
        ]

    def price(self, market_id: str, at: datetime) -> Optional[PricePoint]:
        row = self._con.execute(
            """SELECT to_timestamp(timestamp), price, usd_amount FROM q
               WHERE market_id = ? AND to_timestamp(timestamp) <= ?
               ORDER BY timestamp DESC LIMIT 1""",
            [market_id, at],
        ).fetchone()
        if row is None:
            return None
        return PricePoint(market_id=market_id, ts=_aware(row[0]), price=float(row[1]), usd_amount=float(row[2] or 0.0))

    def prices(self, market_id: str, t0: datetime, t1: datetime) -> list[PricePoint]:
        rows = self._con.execute(
            """SELECT to_timestamp(timestamp), price, usd_amount FROM q
               WHERE market_id = ? AND to_timestamp(timestamp) BETWEEN ? AND ?
               ORDER BY timestamp ASC""",
            [market_id, t0, t1],
        ).fetchall()
        return [
            PricePoint(market_id=market_id, ts=_aware(r[0]), price=float(r[1]), usd_amount=float(r[2] or 0.0))
            for r in rows
        ]

    def trades(self, market_id: str, t0: datetime, t1: datetime) -> list[TradePrint]:
        if self._has_trades:
            sql = """SELECT to_timestamp(timestamp), price, usd_amount, token_amount, taker_direction
                     FROM t WHERE market_id = ? AND to_timestamp(timestamp) BETWEEN ? AND ?
                     ORDER BY timestamp ASC"""
        else:
            sql = """SELECT to_timestamp(timestamp), price, usd_amount, token_amount, side
                     FROM q WHERE market_id = ? AND to_timestamp(timestamp) BETWEEN ? AND ?
                     ORDER BY timestamp ASC"""
        rows = self._con.execute(sql, [market_id, t0, t1]).fetchall()
        return [
            TradePrint(
                market_id=market_id, ts=_aware(r[0]), price=float(r[1]),
                usd_amount=float(r[2] or 0.0), token_amount=float(r[3] or 0.0),
                side=str(r[4] or ""),
            )
            for r in rows
        ]

    def resolution(self, market_id: str) -> Optional[Resolution]:
        row = self._con.execute(
            "SELECT outcome_prices, end_date, closed FROM m WHERE id = ?",
            [market_id],
        ).fetchone()
        if row is None or not row[2]:  # unknown market or not closed
            return None
        outcome_yes = _parse_outcome_yes(row[0])
        if outcome_yes is None or row[1] is None:
            return None
        return Resolution(market_id=market_id, outcome_yes=outcome_yes, resolved_at=_aware(row[1]))


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """DuckDB returns naive datetimes for TIMESTAMPTZ in some paths — pin UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_outcome_yes(outcome_prices: Optional[str]) -> Optional[float]:
    """Parse the answer1 (YES-side) outcome from the dataset's outcome_prices,
    snapped to the binary resolution {0.0, 1.0}.

    REAL dataset format is a single-quoted Python-list repr, e.g. "['1', '0']"
    — NOT valid JSON. So try json.loads first (double-quoted), then fall back
    to ast.literal_eval (single-quoted). Returns the YES-side outcome as 0.0/1.0,
    or None if unparseable / not yet resolved.

    Binary-snap (SIM-3070): a *resolved* binary market IS 0 or 1, but Polymarket
    records the residual near-resolution mark — `['0.0005', '0.9995']` for ~90%
    of closed markets, only ~7% land exactly on `['1','0']`/`['0','1']`. Taking
    the raw 0.0005 made a losing YES position settle at `shares * 0.0005 > 0`,
    which the engine's `wins = settlements with usd > 1e-9` then counted as a
    HIT — inflating hit_rate toward 100% for any YES-buying skill while the
    `buy_and_hold_yes` baseline (same report) correctly showed the loss. Snap to
    {0,1} at the 0.5 threshold so a loser pays exactly 0 and hit_rate is honest
    and consistent with the baselines. The engine only ever replays binary
    Yes/No markets (the harness's gamma shim emits binary outcomes), so the snap
    is always valid here.

    (The single-quote format was missed by JSON-only fixtures and surfaced by
    the 2026-03 NEH pilot — all settlements were silently dropped. See
    cody-learnings 2026-06-11 on fixture-fidelity.)"""
    if not outcome_prices:
        return None
    vals = None
    for parser in (json.loads, ast.literal_eval):
        try:
            vals = parser(outcome_prices)
            break
        except (ValueError, TypeError, SyntaxError):
            continue
    if not isinstance(vals, (list, tuple)) or not vals:
        return None
    try:
        raw = float(vals[0])
    except (ValueError, TypeError):
        return None
    return 1.0 if raw >= 0.5 else 0.0
