#!/usr/bin/env python3
"""
Polymarket DCA Eval Trader

Builds a three-tranche DCA plan for one Polymarket thesis and checks the plan
against a prop-firm-shaped evaluation envelope. Paper mode is the default.

Usage:
    python dca_eval_trader.py --market MARKET_ID --side yes --anchor-price 0.55
    python dca_eval_trader.py --market MARKET_ID --side yes --anchor-price 0.55 --current-price 0.52
    python dca_eval_trader.py --market MARKET_ID --side yes --anchor-price 0.55 --live
"""

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

try:
    from simmer_sdk.skill import load_config, update_config, get_config_path
except ImportError:  # Tests import this module without requiring package install.
    def load_config(schema, _file, slug=None):
        return {key: item["default"] for key, item in schema.items()}

    def update_config(*_args, **_kwargs):
        raise RuntimeError("simmer-sdk is not installed")

    def get_config_path(_file, slug=None):
        return Path(_file).with_name("config.json")


SKILL_SLUG = "polymarket-dca-eval-trader"
TRADE_SOURCE = "sdk:polymarket-dca-eval-trader"
JOURNAL_PATH = Path.home() / ".simmer" / "polymarket-dca-eval-trader" / "journal.jsonl"

CONFIG_SCHEMA = {
    "total_budget_usd": {
        "default": 30.0,
        "env": "SIMMER_DCA_EVAL_TOTAL_BUDGET",
        "type": float,
        "help": "Total intended position size across exactly 3 DCA tranches.",
    },
    "per_market_cap_usd": {
        "default": 50.0,
        "env": "SIMMER_DCA_EVAL_PER_MARKET_CAP",
        "type": float,
        "help": "Maximum total exposure allowed on one market.",
    },
    "daily_cap_usd": {
        "default": 100.0,
        "env": "SIMMER_DCA_EVAL_DAILY_CAP",
        "type": float,
        "help": "Maximum daily new exposure across all DCA eval trades.",
    },
    "tranche_schedule": {
        "default": "0:0:0.34,2.5:24:0.33,5.0:48:0.33",
        "env": "SIMMER_DCA_EVAL_TRANCHE_SCHEDULE",
        "type": str,
        "help": "Exactly 3 entries as displacement_pct:elapsed_hours:size_weight.",
    },
    "stop_loss_pct": {
        "default": 2.5,
        "env": "SIMMER_DCA_EVAL_STOP_LOSS_PCT",
        "type": float,
        "help": "Default stop loss percentage from weighted average entry.",
    },
    "take_profit_pct": {
        "default": 4.5,
        "env": "SIMMER_DCA_EVAL_TAKE_PROFIT_PCT",
        "type": float,
        "help": "Default take profit percentage from weighted average entry.",
    },
    "eval_account_size_usd": {
        "default": 10000.0,
        "env": "SIMMER_DCA_EVAL_ACCOUNT_SIZE",
        "type": float,
        "help": "Nominal evaluation account size for envelope checks.",
    },
    "eval_target_pct": {
        "default": 10.0,
        "env": "SIMMER_DCA_EVAL_TARGET_PCT",
        "type": float,
        "help": "Profit target percentage for reporting. Default 10%.",
    },
    "static_drawdown_pct": {
        "default": 6.0,
        "env": "SIMMER_DCA_EVAL_STATIC_DRAWDOWN_PCT",
        "type": float,
        "help": "Static drawdown limit percentage. Default 6%.",
    },
    "daily_drawdown_pct": {
        "default": 3.0,
        "env": "SIMMER_DCA_EVAL_DAILY_DRAWDOWN_PCT",
        "type": float,
        "help": "Daily drawdown limit percentage. Default 3%.",
    },
    "risk_monitor_min_duration_hours": {
        "default": 0.5,
        "env": "SIMMER_DCA_EVAL_RISK_MONITOR_MIN_HOURS",
        "type": float,
        "help": "Only attach SDK risk monitors when the market lasts at least this long.",
    },
}

cfg = load_config(CONFIG_SCHEMA, __file__, slug=SKILL_SLUG)


@dataclass(frozen=True)
class TrancheRule:
    level: int
    displacement_pct: float
    elapsed_hours: float
    size_weight: float


@dataclass(frozen=True)
class PlannedTranche:
    level: int
    trigger_price: float
    trigger_elapsed_hours: float
    requested_usd: float
    planned_usd: float
    status: str
    reason: str


def clamp_price(price: float) -> float:
    return round(min(0.99, max(0.01, float(price))), 4)


def parse_tranche_schedule(raw: str) -> List[TrancheRule]:
    """Parse exactly 3 displacement/time/weight rules.

    Format: "0:0:0.34,2.5:24:0.33,5:48:0.33"
    """
    rules: List[TrancheRule] = []
    for idx, item in enumerate((raw or "").split(","), start=1):
        parts = [p.strip() for p in item.split(":")]
        if len(parts) != 3:
            raise ValueError("tranche_schedule entries must be displacement_pct:elapsed_hours:size_weight")
        displacement, hours, weight = map(float, parts)
        if displacement < 0 or hours < 0 or weight <= 0:
            raise ValueError("tranche displacement/time must be non-negative and weight must be positive")
        rules.append(TrancheRule(idx, displacement, hours, weight))

    if len(rules) != 3:
        raise ValueError("DCA eval trader requires exactly 3 tranches")

    total_weight = sum(r.size_weight for r in rules)
    if total_weight <= 0:
        raise ValueError("tranche size weights must sum above zero")
    return [
        TrancheRule(r.level, r.displacement_pct, r.elapsed_hours, r.size_weight / total_weight)
        for r in rules
    ]


def trigger_price(anchor_price: float, displacement_pct: float) -> float:
    return clamp_price(float(anchor_price) * (1 - float(displacement_pct) / 100))


def eligible_rules(
    rules: Iterable[TrancheRule],
    anchor_price: float,
    current_price: float,
    elapsed_hours: float,
) -> List[TrancheRule]:
    eligible = []
    for rule in rules:
        price_hit = current_price <= trigger_price(anchor_price, rule.displacement_pct)
        time_hit = elapsed_hours >= rule.elapsed_hours
        if price_hit or time_hit:
            eligible.append(rule)
    return eligible


def plan_tranches(
    rules: Iterable[TrancheRule],
    anchor_price: float,
    total_budget_usd: float,
    current_market_exposure_usd: float = 0.0,
    daily_spent_usd: float = 0.0,
    per_market_cap_usd: float = 50.0,
    daily_cap_usd: float = 100.0,
) -> List[PlannedTranche]:
    """Plan three tranches while enforcing per-market and daily caps."""
    market_remaining = max(0.0, per_market_cap_usd - current_market_exposure_usd)
    daily_remaining = max(0.0, daily_cap_usd - daily_spent_usd)
    cap_remaining = min(market_remaining, daily_remaining)
    planned: List[PlannedTranche] = []

    for rule in rules:
        requested = round(total_budget_usd * rule.size_weight, 2)
        planned_usd = round(min(requested, cap_remaining), 2)
        if planned_usd <= 0:
            status = "blocked"
            reason = "cap_exhausted"
        elif planned_usd < requested:
            status = "capped"
            reason = "per_market_or_daily_cap"
        else:
            status = "planned"
            reason = "ok"
        cap_remaining = max(0.0, cap_remaining - planned_usd)
        planned.append(
            PlannedTranche(
                level=rule.level,
                trigger_price=trigger_price(anchor_price, rule.displacement_pct),
                trigger_elapsed_hours=rule.elapsed_hours,
                requested_usd=requested,
                planned_usd=planned_usd,
                status=status,
                reason=reason,
            )
        )
    return planned


def sl_tp_thresholds(avg_entry_price: float, stop_loss_pct: float = 2.5, take_profit_pct: float = 4.5) -> dict:
    """Return configurable default stop-loss and take-profit token-price thresholds."""
    entry = float(avg_entry_price)
    return {
        "avg_entry_price": clamp_price(entry),
        "stop_loss_pct": float(stop_loss_pct),
        "take_profit_pct": float(take_profit_pct),
        "stop_loss_price": clamp_price(entry * (1 - float(stop_loss_pct) / 100)),
        "take_profit_price": clamp_price(entry * (1 + float(take_profit_pct) / 100)),
    }


def eval_envelope_report(
    proposed_cost_usd: float,
    account_size_usd: float = 10000.0,
    current_equity_usd: Optional[float] = None,
    cumulative_pnl_usd: float = 0.0,
    daily_pnl_usd: float = 0.0,
    target_pct: float = 10.0,
    static_drawdown_pct: float = 6.0,
    daily_drawdown_pct: float = 3.0,
) -> dict:
    """Report whether proposed sizing stays inside the eval constraint envelope."""
    account_size = float(account_size_usd)
    equity = float(current_equity_usd if current_equity_usd is not None else account_size + cumulative_pnl_usd)
    proposed_cost = max(0.0, float(proposed_cost_usd))
    day_start_equity = equity - float(daily_pnl_usd)
    equity_after_full_loss = equity - proposed_cost
    static_floor = account_size * (1 - float(static_drawdown_pct) / 100)
    daily_floor = day_start_equity * (1 - float(daily_drawdown_pct) / 100)
    target_profit = account_size * float(target_pct) / 100

    static_ok = equity_after_full_loss >= static_floor
    daily_ok = equity_after_full_loss >= daily_floor
    return {
        "account_size_usd": round(account_size, 2),
        "proposed_cost_usd": round(proposed_cost, 2),
        "target_profit_usd": round(target_profit, 2),
        "remaining_profit_to_target_usd": round(max(0.0, target_profit - float(cumulative_pnl_usd)), 2),
        "static_drawdown_floor_usd": round(static_floor, 2),
        "daily_drawdown_floor_usd": round(daily_floor, 2),
        "equity_after_full_loss_usd": round(equity_after_full_loss, 2),
        "static_drawdown_ok": static_ok,
        "daily_drawdown_ok": daily_ok,
        "passes_eval_envelope": static_ok and daily_ok,
        "disclaimer": "Envelope check only; this is not a claim that any prop challenge will pass.",
    }


def should_attach_risk_monitor(duration_hours: Optional[float], min_duration_hours: float) -> bool:
    if duration_hours is None:
        return False
    return float(duration_hours) >= float(min_duration_hours)


def _load_daily_spend(path: Optional[Path] = None) -> dict:
    journal = path or JOURNAL_PATH
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    spent = 0.0
    if not journal.exists():
        return {"date": today, "spent": 0.0}
    for line in journal.read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("date") == today and event.get("event") in {"paper_plan", "live_trade"}:
            spent += float(event.get("planned_cost_usd") or event.get("cost_usd") or 0)
    return {"date": today, "spent": round(spent, 2)}


def journal_event(event: dict, path: Optional[Path] = None) -> None:
    journal = path or JOURNAL_PATH
    journal.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "source": TRADE_SOURCE,
        "skill_slug": SKILL_SLUG,
        **event,
    }
    with journal.open("a") as f:
        f.write(json.dumps(payload, sort_keys=True) + "\n")


def _get_client(live: bool):
    try:
        from simmer_sdk import SimmerClient
    except ImportError:
        print("Error: simmer-sdk not installed. Run: pip install simmer-sdk")
        sys.exit(1)
    api_key = os.environ.get("SIMMER_API_KEY")
    if not api_key:
        print("Error: SIMMER_API_KEY environment variable not set")
        print("Get your API key from: simmer.markets/dashboard")
        sys.exit(1)
    return SimmerClient(api_key=api_key, venue=os.environ.get("TRADING_VENUE", "polymarket"), live=live)


def build_plan(args) -> dict:
    rules = parse_tranche_schedule(args.schedule or cfg["tranche_schedule"])
    daily = _load_daily_spend(Path(args.journal) if args.journal else None)
    planned = plan_tranches(
        rules,
        anchor_price=args.anchor_price,
        total_budget_usd=args.budget,
        current_market_exposure_usd=args.current_exposure,
        daily_spent_usd=daily["spent"],
        per_market_cap_usd=args.per_market_cap,
        daily_cap_usd=args.daily_cap,
    )
    eligible = eligible_rules(rules, args.anchor_price, args.current_price, args.elapsed_hours)
    eligible_levels = {r.level for r in eligible}
    executable_cost = sum(t.planned_usd for t in planned if t.level in eligible_levels and t.status != "blocked")
    thresholds = sl_tp_thresholds(args.avg_entry_price or args.current_price, args.stop_loss_pct, args.take_profit_pct)
    envelope = eval_envelope_report(
        executable_cost,
        account_size_usd=args.account_size,
        current_equity_usd=args.current_equity,
        cumulative_pnl_usd=args.cumulative_pnl,
        daily_pnl_usd=args.daily_pnl,
        target_pct=args.target_pct,
        static_drawdown_pct=args.static_drawdown_pct,
        daily_drawdown_pct=args.daily_drawdown_pct,
    )
    return {
        "mode": "live" if args.live else "paper",
        "market_id": args.market,
        "side": args.side.lower(),
        "source": TRADE_SOURCE,
        "rules": [asdict(r) for r in rules],
        "planned_tranches": [asdict(t) for t in planned],
        "eligible_levels": sorted(eligible_levels),
        "executable_cost_usd": round(executable_cost, 2),
        "sl_tp": thresholds,
        "eval_envelope": envelope,
        "risk_monitor": {
            "will_attach": should_attach_risk_monitor(args.market_duration_hours, args.risk_monitor_min_duration_hours),
            "min_duration_hours": args.risk_monitor_min_duration_hours,
            "market_duration_hours": args.market_duration_hours,
        },
        "disclaimer": "Paper/default tool. --live places real orders. No claim that this passes Propr or any prop challenge.",
    }


def run(args) -> int:
    plan = build_plan(args)
    print(json.dumps(plan, indent=2, sort_keys=True))
    journal_event(
        {
            "event": "paper_plan" if not args.live else "live_plan",
            "market_id": args.market,
            "side": args.side.lower(),
            "planned_cost_usd": plan["executable_cost_usd"],
            "passes_eval_envelope": plan["eval_envelope"]["passes_eval_envelope"],
        },
        Path(args.journal) if args.journal else None,
    )

    if not args.live:
        print("\n[PAPER MODE] No order placed. Re-run with --live only after reviewing the plan.")
        return 0

    if not plan["eval_envelope"]["passes_eval_envelope"]:
        print("Blocked: proposed executable tranche cost violates the eval envelope.")
        return 2
    if plan["executable_cost_usd"] <= 0:
        print("No eligible tranche with available cap.")
        return 0

    client = _get_client(live=True)
    preflight = client.ensure_can_trade(min_usd=max(1.0, plan["executable_cost_usd"]))
    if not preflight.get("ok"):
        print(f"Blocked by SDK preflight: {preflight}")
        return 2

    result = client.trade(
        market_id=args.market,
        side=args.side.lower(),
        amount=plan["executable_cost_usd"],
        source=TRADE_SOURCE,
        skill_slug=SKILL_SLUG,
        signal_data={
            "strategy": "dca_eval",
            "eligible_levels": plan["eligible_levels"],
            "eval_envelope": plan["eval_envelope"],
            "sl_tp": plan["sl_tp"],
        },
    )
    print(result)
    journal_event(
        {
            "event": "live_trade",
            "market_id": args.market,
            "side": args.side.lower(),
            "cost_usd": plan["executable_cost_usd"],
            "result": str(result),
        },
        Path(args.journal) if args.journal else None,
    )

    if plan["risk_monitor"]["will_attach"]:
        try:
            client.set_monitor(
                args.market,
                args.side.lower(),
                stop_loss_pct=args.stop_loss_pct / 100,
                take_profit_pct=args.take_profit_pct / 100,
            )
            print("Attached SDK risk monitor with configured SL/TP thresholds.")
        except Exception as exc:
            print(f"Warning: trade placed, but risk monitor setup failed: {exc}")
    else:
        print("Risk monitor not attached: market duration is too short or unknown.")
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Three-tranche Polymarket DCA eval trader")
    parser.add_argument("--market", required=True, help="Polymarket/Simmer market id to trade")
    parser.add_argument("--side", choices=["yes", "no"], required=True, help="Thesis side")
    parser.add_argument("--anchor-price", type=float, required=True, help="Initial thesis price")
    parser.add_argument("--current-price", type=float, default=None, help="Current selected-token price")
    parser.add_argument("--avg-entry-price", type=float, default=None, help="Weighted average entry price")
    parser.add_argument("--elapsed-hours", type=float, default=0.0, help="Hours since DCA plan started")
    parser.add_argument("--market-duration-hours", type=float, default=None, help="Expected remaining/total market duration")
    parser.add_argument("--budget", type=float, default=cfg["total_budget_usd"], help="Total DCA budget")
    parser.add_argument("--per-market-cap", type=float, default=cfg["per_market_cap_usd"])
    parser.add_argument("--daily-cap", type=float, default=cfg["daily_cap_usd"])
    parser.add_argument("--current-exposure", type=float, default=0.0)
    parser.add_argument("--schedule", default=cfg["tranche_schedule"], help="displacement_pct:elapsed_hours:size_weight x3")
    parser.add_argument("--stop-loss-pct", type=float, default=cfg["stop_loss_pct"])
    parser.add_argument("--take-profit-pct", type=float, default=cfg["take_profit_pct"])
    parser.add_argument("--account-size", type=float, default=cfg["eval_account_size_usd"])
    parser.add_argument("--current-equity", type=float, default=None)
    parser.add_argument("--cumulative-pnl", type=float, default=0.0)
    parser.add_argument("--daily-pnl", type=float, default=0.0)
    parser.add_argument("--target-pct", type=float, default=cfg["eval_target_pct"])
    parser.add_argument("--static-drawdown-pct", type=float, default=cfg["static_drawdown_pct"])
    parser.add_argument("--daily-drawdown-pct", type=float, default=cfg["daily_drawdown_pct"])
    parser.add_argument("--risk-monitor-min-duration-hours", type=float, default=cfg["risk_monitor_min_duration_hours"])
    parser.add_argument("--journal", default=None, help="Override journal JSONL path")
    parser.add_argument("--live", action="store_true", help="Place a real trade; default is paper mode")
    parser.add_argument("--config", action="store_true", help="Print config path and effective config")
    parser.add_argument("--set", dest="set_values", action="append", default=[], help="Update config key=value")
    args = parser.parse_args(argv)
    if args.current_price is None:
        args.current_price = args.anchor_price
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.config:
        print(f"Config path: {get_config_path(__file__, slug=SKILL_SLUG)}")
        print(json.dumps(cfg, indent=2, sort_keys=True))
        return 0
    for item in args.set_values:
        if "=" not in item:
            raise SystemExit("--set expects key=value")
        key, value = item.split("=", 1)
        update_config(key, value, CONFIG_SCHEMA, __file__, slug=SKILL_SLUG)
    if args.set_values:
        return 0
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
