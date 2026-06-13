# vendored from simmer_v3/replay/engine.py @ 2c909730b34a
# DO NOT EDIT HERE — regenerate via scripts/sync_replay_engine.py
"""Replay engine — the tick loop + report builder (SIM-3070 chunk 3).

Per replay-contract.md:

    for T in ticks(window, cadence):
        freeze(T) → advance resting fills → settle resolutions → strategy acts
    report()

Strategies talk HTTP to the replay server (fastapi TestClient — in-process,
same wire shapes as production), so anything that can call /api/sdk/* can be
replayed. v0 ships a Strategy protocol for scripted strategies; LLM-driven
SKILL.md execution is the session-mode product (Phase 1.5), not this engine.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional, Protocol

from fastapi.testclient import TestClient

from .server import ReplaySession, create_app
from .simstate import SimState
from .store import HistoricalStore

# Canonical engine version — bump on any change that can alter results (fill
# model, look-ahead semantics, fee handling). The catalog's stale_engine badge
# compares stored reports' reproducibility.engine against this.
ENGINE_VERSION = "0.1.1"  # 0.1.1: snap resolved binary outcome to {0,1} (SIM-3070 hit_rate fix)

REALISM_GAPS = [
    "no slippage",
    "no market impact at size",
    "no queue position",
    "no latency",
    "no maker rebates",
    "trade-tape prices, not orderbook",
]


class Strategy(Protocol):
    def on_tick(self, api: TestClient, now: datetime) -> None: ...


@dataclass
class ReplayConfig:
    t0: datetime
    t1: datetime
    cadence: timedelta = timedelta(minutes=15)
    starting_balance: float = 1000.0
    fee_rate: float = 0.0
    skill_slug: str = "unknown"
    skill_version: str = "0"
    dataset_rev: str = "unknown"
    engine_version: str = ENGINE_VERSION
    seed: int = 0
    max_evaluations: int = 50_000  # ticks × markets-evaluated budget (contract)
    # Behavior-changing run parameters that aren't first-class fields: strategy
    # knobs (price_cap), bundle entrypoint/args/content digest. MUST be passed
    # by callers — config_hash is the ReportStore upsert key, so two runs that
    # can produce different results must never hash identical (holistic-review P1).
    params: dict = field(default_factory=dict)

    def config_hash(self) -> str:
        blob = json.dumps({
            "t0": self.t0.isoformat(), "t1": self.t1.isoformat(),
            "cadence_s": self.cadence.total_seconds(),
            "balance": self.starting_balance, "fee": self.fee_rate,
            "skill": f"{self.skill_slug}@{self.skill_version}",
            "dataset": self.dataset_rev, "engine": self.engine_version,
            "seed": self.seed, "max_evaluations": self.max_evaluations,
            "params": self.params,
        }, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


class ReplayEngine:
    def __init__(self, store: HistoricalStore, strategy: Strategy, config: ReplayConfig,
                 kline_store: Optional[Any] = None):
        self.store = store
        self.strategy = strategy
        self.config = config
        # The candles plane (SIM-3070 1.5C): a BinanceKlineStore feeds
        # /api/replay-data/candles end-clamped to the frozen tick. None →
        # the endpoint 404s (the trio's get_candles can't read signal, so the
        # skill honestly sees "no candles" rather than future/empty data).
        # Wiring it HERE is the single point that reaches both the in-process
        # TestClient (self.api) AND the subprocess SessionServer (harness),
        # since both serve create_app(self.session).
        self.kline_store = kline_store
        self.sim = SimState(starting_balance=config.starting_balance, fee_rate=config.fee_rate)
        self.session = ReplaySession(store=store, sim=self.sim, now=config.t0,
                                     max_evaluations=config.max_evaluations,
                                     kline_store=kline_store)
        self.api = TestClient(create_app(self.session))
        self.equity_curve: list[dict] = []
        self.tick_count = 0

    def run(self) -> dict:
        cfg = self.config
        t = cfg.t0
        prev = cfg.t0
        self.evaluations_exhausted = False
        while t <= cfg.t1:
            self.session.freeze(t)
            view = self.session.view
            self.sim.advance_fills(view, since=prev)
            self.sim.settle(view)
            self.strategy.on_tick(self.api, t)
            self.equity_curve.append({"ts": t.isoformat(), "equity": self.sim.equity(view)})
            self.tick_count += 1
            prev = t
            t = t + cfg.cadence
            # Contract run budget (holistic-review P1): 50K ticks × markets per
            # run is a HARD cap, not advisory — a 1m-cadence × 365d × 1000-market
            # job must truncate, not run to OOM / monopolize the worker queue.
            if self.session.evaluations >= cfg.max_evaluations:
                self.evaluations_exhausted = True
                break
        # final settlement pass at window end
        self.session.freeze(cfg.t1)
        self.sim.settle(self.session.view)
        return self.report()

    # -- report ---------------------------------------------------------------

    def report(self) -> dict:
        cfg = self.config
        final_view = self.session.view
        equity = self.sim.equity(final_view)
        trades = [f for f in self.sim.fills if f.kind != "settlement"]
        settlements = [f for f in self.sim.fills if f.kind == "settlement"]
        wins = sum(1 for s in settlements if s.usd > 1e-9)
        decided = len(settlements)
        return {
            "summary": {
                "markets_traded": len({f.market_id for f in trades}),
                "decisions": len(self.session.decisions),
                "trades": len(trades),
                "settlements": decided,
                "hit_rate": (wins / decided) if decided else None,
                "pnl": equity - cfg.starting_balance,
                "final_equity": equity,
                "max_drawdown": _max_drawdown([p["equity"] for p in self.equity_curve]),
                "ticks": self.tick_count,
                "evaluations": self.session.evaluations,
                "evaluations_exhausted": getattr(self, "evaluations_exhausted", False),
            },
            "baselines": self._baselines(),
            "decisions": self.session.decisions,
            "fills": [
                {"ts": f.ts.isoformat(), "market_id": f.market_id, "side": f.side,
                 "action": f.action, "shares": round(f.shares, 6), "price": f.price,
                 "usd": round(f.usd, 6), "kind": f.kind}
                for f in self.sim.fills
            ],
            "equity_curve": self.equity_curve,
            "realism_gaps": REALISM_GAPS,
            # Candles-plane usage (SIM-3070): lets a 0-trade result be audited.
            # For an external-signal skill (the trio), candle_requests==0 means
            # the skill never consulted the plane and candles_served==0 with
            # requests>0 means a data gap — either way a 0-trade run is NOT a
            # trustworthy 'verified no-signal' and coverage_ok must not be
            # asserted on it.
            "data_plane": {
                "kline_store": self.kline_store is not None,
                "candle_requests": self.session.candle_requests,
                "candles_served": self.session.candles_served,
            },
            "reproducibility": {
                "dataset": cfg.dataset_rev,
                "window": [cfg.t0.isoformat(), cfg.t1.isoformat()],
                "cadence": f"{int(cfg.cadence.total_seconds())}s",
                "skill": f"{cfg.skill_slug}@{cfg.skill_version}",
                "engine": cfg.engine_version,
                "config_hash": cfg.config_hash(),
                "seed": cfg.seed,
            },
        }

    def _baselines(self) -> dict:
        """Same markets, same entry times, same notionals — different side rule.

        buy_and_hold_yes: buy YES at the strategy's entry print, hold to
        resolution (or mark at window end). random: coin-flip side per entry
        (seeded). Both ignore fees for v0 (documented)."""
        entries = [d for d in self.session.decisions if d.get("action") == "buy"]
        rng = random.Random(self.config.seed)
        out = {"buy_and_hold_yes": 0.0, "random": 0.0, "note": "same entries/notional; fees ignored in baselines v0"}
        for d in entries:
            mid = d["market_id"]
            amt = float(d.get("amount") or 0.0)
            if amt <= 0:
                continue
            entry_point = self.store.price(mid, datetime.fromisoformat(d["ts"]))
            res = self.store.resolution(mid)
            if entry_point is None:
                continue
            final_yes = res.outcome_yes if (res and res.resolved_at <= self.config.t1) else None
            if final_yes is None:
                last = self.store.price(mid, self.config.t1)
                final_yes = last.price if last else entry_point.price
            if entry_point.price > 0:
                out["buy_and_hold_yes"] += amt * (final_yes / entry_point.price) - amt
            side = rng.choice(["yes", "no"])
            px = entry_point.price if side == "yes" else 1.0 - entry_point.price
            payout = final_yes if side == "yes" else 1.0 - final_yes
            if px > 0:
                out["random"] += amt * (payout / px) - amt
        out["buy_and_hold_yes"] = round(out["buy_and_hold_yes"], 6)
        out["random"] = round(out["random"], 6)
        return out


def _max_drawdown(series: list[float]) -> float:
    peak, mdd = float("-inf"), 0.0
    for v in series:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return round(mdd, 6)
