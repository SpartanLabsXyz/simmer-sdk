# vendored from simmer_v3/replay/server.py @ fc7f82cadfd5
# DO NOT EDIT HERE — regenerate via scripts/sync_replay_engine.py
"""Replay API server — the minimal /api/sdk surface skills consume (SIM-3070).

Architecture A from replay-contract.md: skills run UNMODIFIED, pointed at this
server instead of api.simmer.markets. Backed by ReplayView (frozen clock) +
SimState (simulated portfolio). All time-dependent response fields derive from
the frozen tick, never wall clock.

Endpoint set derived from the 2026-06-11 SDK-surface audit of official skills
(client.py + 6 pilot skills). Implemented: markets, fast-markets (alias),
markets/importable, markets/import, context, history, positions, trade,
portfolio, briefing, agents/me, orders/open, orders/{id} DELETE, redeem.
Stubbed (documented): best_bid/ask = last tape price (trade-tape has no book),
monitors accepted-but-inert, redeem maps to settlement state.

Known SDK gap (documented for Phase 1): SimmerClient has NO env-var base-URL
override — base_url is a constructor arg. The harness passes base_url when
constructing the client for a skill, or sets SIMMER_API_URL once the SDK grows
that override (one-line additive change, queued on SIM-3070).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request

from .simstate import ReplayTradeError, SimState
from .store import HistoricalStore, ReplayView


@dataclass
class ReplaySession:
    """One replay run: a store, a portfolio, and a movable frozen clock."""

    store: HistoricalStore
    sim: SimState
    now: datetime
    agent_id: str = "replay-agent"
    venue: str = "polymarket"
    last_tick: Optional[datetime] = None
    decisions: list[dict] = field(default_factory=list)
    # ticks × markets-served budget (contract: 50K cap). Incremented by every
    # market-listing endpoint; once exhausted the endpoints serve EMPTY lists
    # (hard cap at the serve boundary — a single huge listing must not blow
    # past it), and the engine truncates the run after the tick.
    evaluations: int = 0
    max_evaluations: Optional[int] = None
    # Optional candles plane (SIM-3070 1.5C): a BinanceKlineStore serving
    # /api/replay-data/candles with end CLAMPED to the frozen tick. Absent →
    # the endpoint 404s (skills that need candles aren't replayable on this
    # session — honest absence beats empty data).
    kline_store: Optional[Any] = None
    # Candles-plane usage counters (SIM-3070). The engine surfaces these in the
    # report so a 0-trade result from an external-signal skill is auditable:
    # candle_requests==0 means the skill never asked (wrong skill / wrong
    # cadence), and requests>0 with candles_served==0 means a DATA GAP (empty
    # cache / unavailable archive month), NOT a real no-signal — that
    # distinction is what keeps a data gap from minting a false 'verified
    # no-signal' badge under coverage_ok.
    candle_requests: int = 0
    candles_served: int = 0
    # Serializes candles-plane access. The /api/replay-data/candles endpoint is
    # a SYNC FastAPI handler, so uvicorn dispatches it on a threadpool; a skill
    # that fetches candles concurrently (multiple symbols/intervals per tick)
    # would otherwise race the kline_store's single DuckDB connection — which
    # silently cross-contaminates results (wrong candles served as complete=true,
    # NOT a fail-safe error) — and lose the non-atomic counter increments. One
    # lock per session suffices: each replay run owns its own store.
    _candles_lock: Any = field(default_factory=threading.Lock, compare=False, repr=False)

    @property
    def budget_exhausted(self) -> bool:
        return self.max_evaluations is not None and self.evaluations >= self.max_evaluations

    def freeze(self, at: datetime) -> None:
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        self.last_tick = self.now
        self.now = at

    @property
    def view(self) -> ReplayView:
        return ReplayView(self.store, self.now)


def _market_payload(session: ReplaySession, meta) -> dict[str, Any]:
    point = session.view.price(meta.id)
    price = point.price if point else None
    resolves_at = meta.end_date.isoformat() if meta.end_date else None
    secs = (meta.end_date - session.now).total_seconds() if meta.end_date else None
    return {
        "id": meta.id,
        "question": meta.question,
        "slug": meta.slug,
        "status": "active",
        "venue": session.venue,
        "current_probability": price,
        "yes_price": price,
        "no_price": (1.0 - price) if price is not None else None,
        "resolves_at": resolves_at,
        "seconds_to_resolution": secs,
        "is_live_now": True,
        "volume": meta.volume,
        "polymarket_condition_id": meta.condition_id,
        "polymarket_token_id": f"replay-{meta.id}-yes",
        "polymarket_no_token_id": f"replay-{meta.id}-no",
        "polymarket_neg_risk": meta.neg_risk,
        # trade-tape has no order book — stub both sides at last price (realism
        # gap "trade-tape prices, not orderbook" is disclosed in every report)
        "best_bid": price,
        "best_ask": price,
        "best_bid_size": None,
        "best_ask_size": None,
        "spread": 0.0 if price is not None else None,
        "quote_ts": point.ts.isoformat() if point else None,
    }


def create_app(session: ReplaySession) -> FastAPI:
    app = FastAPI(title="simmer-replay", docs_url=None, redoc_url=None)
    app.state.session = session

    def _budgeted(rows: list) -> list:
        """Enforce the ticks×markets budget AT THE SERVE BOUNDARY: clamp the
        rows to the remaining budget and serve nothing once exhausted. The
        engine's post-tick truncation alone would let one huge listing blow
        far past the cap (Codex pass-1 on the holistic-review fixes)."""
        if session.max_evaluations is not None:
            remaining = session.max_evaluations - session.evaluations
            rows = rows[:max(0, remaining)]
        session.evaluations += len(rows)
        return rows

    @app.get("/api/sdk/health")
    def health():
        return {"status": "ok", "mode": "replay", "frozen_at": session.now.isoformat()}

    @app.get("/api/sdk/agents/me")
    def agents_me():
        return {
            "agent_id": session.agent_id,
            "tier": "replay",
            "real_trading_enabled": False,
            "wallet_address": None,
        }

    @app.get("/api/sdk/markets")
    def markets(limit: int = 50, q: Optional[str] = None, status: Optional[str] = None):
        metas = session.view.markets(limit=limit)
        if q:
            ql = q.lower()
            metas = [m for m in metas if ql in m.question.lower() or ql in m.slug.lower()]
        rows = _budgeted(metas[:limit])
        return {"markets": [_market_payload(session, m) for m in rows]}

    @app.get("/api/sdk/fast-markets")
    def fast_markets(limit: int = 50, asset: Optional[str] = None, window: Optional[str] = None):
        # alias over markets(); fast-market filtering by slug pattern
        metas = session.view.markets(limit=limit * 4)
        if asset:
            metas = [m for m in metas if asset.lower() in m.slug.lower()]
        if window:
            metas = [m for m in metas if window.lower() in m.slug.lower()]
        rows = _budgeted(metas[:limit])
        return {"markets": [_market_payload(session, m) for m in rows]}

    @app.get("/api/sdk/markets/importable")
    def importable(limit: int = 50, q: Optional[str] = None, min_volume: float = 0.0,
                   category: Optional[str] = None):
        metas = session.view.markets(limit=limit * 4)
        if q:
            ql = q.lower()
            metas = [m for m in metas if ql in m.question.lower() or ql in m.slug.lower()]
        metas = [m for m in metas if m.volume >= min_volume]
        rows = _budgeted(metas[:limit])
        return {
            "markets": [
                {
                    "question": m.question,
                    "url": f"https://polymarket.com/event/{m.event_id or m.slug}/{m.slug}",
                    "condition_id": m.condition_id,
                    "market_id": m.id,
                    "current_price": (p.price if (p := session.view.price(m.id)) else None),
                    "volume_24h": m.volume,
                }
                for m in rows
            ]
        }

    @app.post("/api/sdk/markets/import")
    async def import_market(request: Request):
        body = await request.json()
        url = (body.get("polymarket_url") or body.get("url") or "").rstrip("/")
        slug = url.split("/")[-1] if url else ""
        if not slug:
            raise HTTPException(422, "polymarket_url required")
        metas = session.view.markets(limit=100000, slug_like=slug)
        if not metas:
            raise HTTPException(404, f"market {slug!r} not found in replay tape at this tick")
        m = metas[0]
        return {"market_id": m.id, "question": m.question, "status": "active", "already_imported": True}

    @app.get("/api/sdk/context/{market_id}")
    def context(market_id: str):
        payload = _one_market(session, market_id)
        pos = session.sim.positions.get(market_id)
        return {
            "market": payload,
            "positions": _positions_rows(session, only=market_id),
            "discipline": {"warnings": []},
            "slippage": {"estimate_pct": None, "note": "replay: no orderbook, slippage not modeled"},
            "edge": None,
            "warnings": [],
        }

    @app.get("/api/sdk/markets/{market_id}/history")
    def history(market_id: str, hours: int = 24 * 14):
        from datetime import timedelta

        t0 = session.now - timedelta(hours=hours)
        pts = session.view.prices(market_id, t0)
        return {
            "points": [
                {"timestamp": p.ts.isoformat(), "price_yes": p.price, "price_no": 1.0 - p.price}
                for p in pts
            ]
        }

    @app.get("/api/sdk/positions")
    def positions(venue: Optional[str] = None, status: Optional[str] = None):
        rows = _positions_rows(session)
        if status == "resolved":
            rows = [r for r in rows if r["redeemable"]]
        return {"positions": rows, "sim_balance": session.sim.cash}

    @app.post("/api/sdk/trade")
    async def trade(request: Request):
        body = await request.json()
        market_id = body.get("market_id")
        side = (body.get("side") or "yes").lower()
        action = (body.get("action") or "buy").lower()
        amount = float(body.get("amount") or 0.0)
        shares = float(body.get("shares") or 0.0)
        price = body.get("price")
        limit_price = float(price) if price is not None and (body.get("order_type") or "").lower() in ("limit", "gtc") else None
        view = session.view
        try:
            if action == "buy":
                result = session.sim.buy(view, market_id, side, amount, limit_price=limit_price)
            elif action == "sell":
                qty = shares or _held(session, market_id, side)
                result = session.sim.sell(view, market_id, side, qty, limit_price=limit_price)
            else:
                raise ReplayTradeError(f"unsupported action {action!r}")
        except ReplayTradeError as e:
            return {"success": False, "error": str(e), "balance": session.sim.cash}
        session.decisions.append({
            "ts": session.now.isoformat(), "market_id": market_id, "side": side,
            "action": action, "amount": amount, "shares": shares,
            "reasoning": body.get("reasoning"), "skill_slug": body.get("skill_slug"),
            "source": body.get("source"), "signal_data": body.get("signal_data"),
        })
        if hasattr(result, "order_id") and result.__class__.__name__ == "LimitOrder":
            return {"success": True, "order_id": result.order_id, "order_status": "open",
                    "fill_status": "unfilled", "balance": session.sim.cash}
        return {
            "success": True,
            "trade_id": f"replay-{len(session.sim.fills)}",
            "shares_bought": result.shares if action == "buy" else 0.0,
            "shares_sold": result.shares if action == "sell" else 0.0,
            "cost": abs(result.usd),
            "new_price": result.price,
            "order_status": "filled",
            "fill_status": "filled",
            "balance": session.sim.cash,
        }

    @app.get("/api/sdk/orders/open")
    def open_orders():
        orders = [
            {"order_id": o.order_id, "market_id": o.market_id, "side": o.side,
             "action": o.action, "price": o.limit_price, "usd_amount": o.usd_amount,
             "shares": o.shares, "placed_at": o.placed_at.isoformat() if o.placed_at else None}
            for o in session.sim.open_orders.values()
        ]
        return {"orders": orders, "count": len(orders)}

    @app.delete("/api/sdk/orders/{order_id}")
    def cancel_order(order_id: str):
        ok = session.sim.cancel(order_id)
        return {"success": ok, "error": None if ok else "order not found"}

    @app.get("/api/sdk/portfolio")
    def portfolio(venue: Optional[str] = None):
        eq = session.sim.equity(session.view)
        bucket = {
            "balance": session.sim.cash,
            "collateral": session.sim.cash,
            "pnl": eq - session.sim.starting_balance,
            "positions_count": len(session.sim.positions),
            "total_exposure": eq - session.sim.cash,
            "exchange_version": "replay",
        }
        return {"sim": bucket, "polymarket": bucket, "kalshi": {"balance": 0.0},
                "portfolio_value": eq, "ok": True, "max_safe_size": session.sim.cash}

    @app.get("/api/sdk/briefing")
    def briefing(since: Optional[str] = None):
        eq = session.sim.equity(session.view)
        return {
            "portfolio_value": eq,
            "cash_balance": session.sim.cash,
            "balance": session.sim.cash,
            "triggered_risk_alerts": [],
            "venues": {session.venue: {"balance": session.sim.cash, "portfolio_value": eq}},
            "skill_updates": [],
            "as_of": session.now.isoformat(),
        }

    @app.post("/api/sdk/positions/{market_id}/monitor")
    async def set_monitor(market_id: str, request: Request):
        body = await request.json()
        return {"market_id": market_id, "side": body.get("side"),
                "stop_loss_pct": body.get("stop_loss_pct"),
                "take_profit_pct": body.get("take_profit_pct"),
                "note": "replay: monitors accepted but inert (engine settles at resolution)"}

    @app.post("/api/sdk/redeem")
    async def redeem(request: Request):
        # settlement happens engine-side each tick; redeem reports current state
        return {"success": True, "note": "replay: resolved positions auto-settle at tick"}

    @app.get("/api/replay-data/candles")
    def replay_candles(symbol: str, interval: str = "1m",
                       start: str = "", end: str = ""):
        """Candles under replay: end CLAMPED to the frozen tick (look-ahead
        enforcement at the serve boundary, like the eval cap) and ALWAYS
        complete=true — a replayed skill must never be told to fall back to
        live Binance, which would be future data relative to the tick."""
        if session.kline_store is None:
            raise HTTPException(404, "this replay session has no candles plane")
        from .candles_service import validate_params

        try:
            sym, ivl, t0, t1 = validate_params(symbol, interval, start, end)
        except ValueError as e:
            raise HTTPException(422, str(e))
        # session.now can be naive until the first freeze() (the CLI parses
        # naive ISO) — normalize before comparing with the aware params
        # (Codex P1: TypeError here would 500 the whole replay data plane).
        tick = session.now if session.now.tzinfo else session.now.replace(tzinfo=timezone.utc)
        served_t1 = min(t1, tick)
        # Serialize the DuckDB read + the counter writes: this sync endpoint runs
        # in uvicorn's threadpool, so concurrent skill requests would otherwise
        # race the kline_store's single connection (silent wrong-candles) and the
        # non-atomic += counters. Audit counters: a served request always counts;
        # candles_served stays flat on a data gap (empty window) so a 0-trade run
        # can be told apart from a real no-signal (see ReplaySession field comment).
        with session._candles_lock:
            candles = []
            if served_t1 > t0:
                klines = session.kline_store.klines(sym, ivl, t0, served_t1)
                candles = [
                    {"open_time": k.open_time.isoformat(), "close_time": k.close_time.isoformat(),
                     "open": k.open, "high": k.high, "low": k.low, "close": k.close,
                     "volume": k.volume}
                    for k in klines
                ]
            session.candle_requests += 1
            session.candles_served += len(candles)
        return {"symbol": sym, "interval": ivl, "start": t0.isoformat(), "end": t1.isoformat(),
                "served_through": served_t1.isoformat() if served_t1 > t0 else t0.isoformat(),
                "complete": True, "candles": candles, "source": "replay-tape"}

    return app


def _one_market(session: ReplaySession, market_id: str) -> dict:
    metas = [m for m in session.view.markets(limit=100000) if m.id == market_id]
    if not metas:
        raise HTTPException(404, f"market {market_id} not found at this tick")
    return _market_payload(session, metas[0])


def _held(session: ReplaySession, market_id: str, side: str) -> float:
    pos = session.sim.positions.get(market_id)
    if not pos:
        return 0.0
    return pos.shares_yes if side == "yes" else pos.shares_no


def _positions_rows(session: ReplaySession, only: Optional[str] = None) -> list[dict]:
    rows = []
    for market_id, pos in session.sim.positions.items():
        if only and market_id != only:
            continue
        point = session.view.price(market_id)
        price = point.price if point else None
        value = (pos.shares_yes * price + pos.shares_no * (1.0 - price)) if price is not None else None
        res = session.view.resolution(market_id)
        rows.append({
            "market_id": market_id,
            "shares_yes": pos.shares_yes,
            "shares_no": pos.shares_no,
            "cost_basis": pos.cost_basis_usd,
            "current_price": price,
            "current_value": value,
            "pnl": (value - pos.cost_basis_usd) if value is not None else None,
            "status": "resolved" if res else "active",
            "redeemable": res is not None,
            "venue": session.venue,
        })
    return rows
