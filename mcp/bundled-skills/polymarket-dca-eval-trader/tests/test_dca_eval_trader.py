import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "dca_eval_trader.py"
spec = importlib.util.spec_from_file_location("dca_eval_trader", MODULE_PATH)
dca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dca)


def test_schedule_requires_exactly_three_tranches():
    rules = dca.parse_tranche_schedule("0:0:0.34,2.5:24:0.33,5:48:0.33")

    assert [r.level for r in rules] == [1, 2, 3]
    assert round(sum(r.size_weight for r in rules), 6) == 1
    assert dca.trigger_price(0.60, rules[1].displacement_pct) == 0.585


def test_eligible_rules_fire_on_price_or_time():
    rules = dca.parse_tranche_schedule("0:0:0.34,2.5:24:0.33,5:48:0.33")

    eligible = dca.eligible_rules(rules, anchor_price=0.60, current_price=0.584, elapsed_hours=1)
    assert [r.level for r in eligible] == [1, 2]

    eligible = dca.eligible_rules(rules, anchor_price=0.60, current_price=0.60, elapsed_hours=49)
    assert [r.level for r in eligible] == [1, 2, 3]


def test_cap_enforcement_blocks_tranches_after_market_or_daily_cap():
    rules = dca.parse_tranche_schedule("0:0:0.34,2.5:24:0.33,5:48:0.33")

    planned = dca.plan_tranches(
        rules,
        anchor_price=0.50,
        total_budget_usd=90,
        current_market_exposure_usd=45,
        daily_spent_usd=0,
        per_market_cap_usd=50,
        daily_cap_usd=100,
    )

    assert planned[0].requested_usd == 30.6
    assert planned[0].planned_usd == 5
    assert planned[0].status == "capped"
    assert planned[1].planned_usd == 0
    assert planned[1].status == "blocked"
    assert planned[2].status == "blocked"


def test_sl_tp_thresholds_use_configurable_defaults():
    thresholds = dca.sl_tp_thresholds(0.50)

    assert thresholds["stop_loss_pct"] == 2.5
    assert thresholds["take_profit_pct"] == 4.5
    assert thresholds["stop_loss_price"] == 0.4875
    assert thresholds["take_profit_price"] == 0.5225


def test_eval_envelope_reports_daily_and_static_drawdown():
    ok = dca.eval_envelope_report(
        proposed_cost_usd=100,
        account_size_usd=10000,
        current_equity_usd=10000,
        daily_pnl_usd=0,
    )
    assert ok["passes_eval_envelope"] is True
    assert ok["remaining_profit_to_target_usd"] == 1000

    blocked = dca.eval_envelope_report(
        proposed_cost_usd=400,
        account_size_usd=10000,
        current_equity_usd=10000,
        daily_pnl_usd=0,
    )
    assert blocked["static_drawdown_ok"] is True
    assert blocked["daily_drawdown_ok"] is False
    assert blocked["passes_eval_envelope"] is False


def test_risk_monitor_only_when_duration_supports_it():
    assert dca.should_attach_risk_monitor(1.0, 0.5) is True
    assert dca.should_attach_risk_monitor(0.1, 0.5) is False
    assert dca.should_attach_risk_monitor(None, 0.5) is False
