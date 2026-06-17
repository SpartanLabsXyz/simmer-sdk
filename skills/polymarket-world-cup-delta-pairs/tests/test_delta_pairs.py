"""Unit tests for world_cup_delta_pairs — pure logic, no network calls."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub out SDK import before loading the skill module
import types
simmer_sdk_stub = types.ModuleType("simmer_sdk")
simmer_skill_stub = types.ModuleType("simmer_sdk.skill")
simmer_skill_stub.load_config = lambda schema, *a, **kw: {k: v["default"] for k, v in schema.items()}
simmer_skill_stub.get_config_path = lambda *a, **kw: type("P", (), {"__str__": lambda s: "/tmp/cfg"})()
simmer_sdk_stub.skill = simmer_skill_stub
sys.modules.setdefault("simmer_sdk", simmer_sdk_stub)
sys.modules.setdefault("simmer_sdk.skill", simmer_skill_stub)

os.environ.setdefault("SIMMER_API_KEY", "test-key")

import world_cup_delta_pairs as wdp


def _make_market(mid_id, question, price, stage_override=None, volume=1000):
    stage = stage_override or wdp._market_stage(question)
    return {
        "id": mid_id,
        "question": question,
        "mid_price": price,
        "stage": stage,
        "teams": wdp._extract_teams(question),
        "volume": volume,
    }


def test_team_extraction_basic():
    teams = wdp._extract_teams("Will France win Group E?")
    assert "France" in teams


def test_team_extraction_no_false_positives():
    # "World Cup" alone should not match a team name
    teams = wdp._extract_teams("Who wins the 2026 FIFA World Cup?")
    assert teams == []


def test_stage_classification():
    assert wdp._market_stage("Will Argentina advance from Group C?") == "advance"
    assert wdp._market_stage("Will Brazil win Group H?") == "group_win"
    assert wdp._market_stage("2026 FIFA World Cup Winner: England?") == "champion"
    assert wdp._market_stage("Will Spain reach the final?") == "final"


def test_delta_pairs_finds_inverted():
    markets = [
        _make_market("a1", "Will Brazil advance from Group?", 0.80, "advance"),
        _make_market("a2", "2026 World Cup Winner: Brazil?", 0.85, "champion"),  # inverted!
    ]
    pairs = wdp.compute_delta_pairs(markets)
    assert len(pairs) == 1
    assert pairs[0]["is_inverted"] is True
    assert pairs[0]["team"] == "Brazil"


def test_delta_pairs_normal_ordering():
    markets = [
        _make_market("b1", "Will France win Group E?", 0.70, "group_win"),
        _make_market("b2", "2026 World Cup Winner: France?", 0.20, "champion"),
    ]
    pairs = wdp.compute_delta_pairs(markets)
    assert len(pairs) == 1
    p = pairs[0]
    assert p["is_inverted"] is False
    assert abs(p["raw_delta"] - 0.50) < 0.001
    assert abs(p["implied_conditional"] - (0.20 / 0.70)) < 0.001


def test_no_pairs_single_market():
    markets = [
        _make_market("c1", "Will England reach the final?", 0.30, "final"),
    ]
    pairs = wdp.compute_delta_pairs(markets)
    assert pairs == []


def test_no_duplicate_pairs():
    markets = [
        _make_market("d1", "Will Germany advance from Group?", 0.75, "advance"),
        _make_market("d2", "2026 World Cup Winner: Germany?", 0.15, "champion"),
    ]
    pairs = wdp.compute_delta_pairs(markets)
    assert len(pairs) == 1  # only one unique pair


def test_stage_order_correct():
    markets = [
        _make_market("e1", "2026 World Cup Winner: Spain?", 0.12, "champion"),
        _make_market("e2", "Will Spain win Group C?", 0.65, "group_win"),
    ]
    pairs = wdp.compute_delta_pairs(markets)
    assert len(pairs) == 1
    p = pairs[0]
    # A should be earlier (group_win), B should be later (champion)
    assert p["market_a"]["stage"] == "group_win"
    assert p["market_b"]["stage"] == "champion"
