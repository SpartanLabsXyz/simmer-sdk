"""Tests for simmer_sdk.test_skill — skill test harness."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch
from simmer_sdk.test_skill import discover_skill, run_skill_stage, format_report


# --- Fixtures ---

@pytest.fixture
def tmp_skill(tmp_path):
    """Create a minimal skill directory with clawhub.json."""
    skill_dir = tmp_path / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)

    clawhub = {
        "automaton": {"managed": True, "entrypoint": "trader.py"},
        "requires": {"env": ["SIMMER_API_KEY"], "pip": ["simmer-sdk"]},
    }
    (skill_dir / "clawhub.json").write_text(json.dumps(clawhub))

    script = '''#!/usr/bin/env python3
import os, json
print("running")
if os.environ.get("AUTOMATON_MANAGED"):
    print(json.dumps({"automaton": {"signals": 2, "trades_attempted": 1, "trades_executed": 1, "amount_usd": 5.0}}))
'''
    (skill_dir / "trader.py").write_text(script)
    return skill_dir


# --- Task 1: Skill discovery ---

def test_discover_skill_finds_entrypoint(tmp_skill):
    info = discover_skill(tmp_skill)
    assert info["entrypoint"] == "trader.py"
    assert info["script_path"].exists()


def test_discover_skill_missing_clawhub(tmp_path):
    skill_dir = tmp_path / "skills" / "empty-skill"
    skill_dir.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="clawhub.json"):
        discover_skill(skill_dir)


def test_discover_skill_missing_entrypoint(tmp_path):
    skill_dir = tmp_path / "skills" / "no-script"
    skill_dir.mkdir(parents=True)
    clawhub = {"automaton": {"entrypoint": "missing.py"}}
    (skill_dir / "clawhub.json").write_text(json.dumps(clawhub))
    with pytest.raises(FileNotFoundError, match="missing.py"):
        discover_skill(skill_dir)


# --- Task 2: Run stage tests ---

def test_run_skill_stage_sim_pass(tmp_skill):
    """Skill that emits automaton JSON should pass on sim."""
    info = discover_skill(tmp_skill)
    result = run_skill_stage(info, "sim", timeout=10, api_key="sk_test_fake")
    assert result["status"] == "pass"
    assert result["exit_code"] == 0
    assert result["candidates_found"] == 2
    assert result["trades_executed"] == 1


def test_run_skill_stage_crash(tmp_path):
    """Skill that crashes should fail."""
    skill_dir = tmp_path / "skills" / "crash-skill"
    skill_dir.mkdir(parents=True)
    clawhub = {"automaton": {"entrypoint": "crash.py"}}
    (skill_dir / "clawhub.json").write_text(json.dumps(clawhub))
    (skill_dir / "crash.py").write_text("raise RuntimeError('boom')\n")

    info = discover_skill(skill_dir)
    result = run_skill_stage(info, "sim", timeout=10, api_key="sk_test_fake")
    assert result["status"] == "fail"
    assert result["exit_code"] != 0
    assert any("boom" in e or "Exit code" in e for e in result["errors"])


def test_run_skill_stage_timeout(tmp_path):
    """Skill that hangs should timeout."""
    skill_dir = tmp_path / "skills" / "hang-skill"
    skill_dir.mkdir(parents=True)
    clawhub = {"automaton": {"entrypoint": "hang.py"}}
    (skill_dir / "clawhub.json").write_text(json.dumps(clawhub))
    (skill_dir / "hang.py").write_text("import time; time.sleep(999)\n")

    info = discover_skill(skill_dir)
    result = run_skill_stage(info, "sim", timeout=2, api_key="sk_test_fake")
    assert result["status"] == "fail"
    assert any("Timeout" in e for e in result["errors"])


def test_run_skill_stage_no_automaton_output(tmp_path):
    """Skill without automaton JSON falls back to exit code."""
    skill_dir = tmp_path / "skills" / "silent-skill"
    skill_dir.mkdir(parents=True)
    clawhub = {"automaton": {"entrypoint": "silent.py"}}
    (skill_dir / "clawhub.json").write_text(json.dumps(clawhub))
    (skill_dir / "silent.py").write_text("print('hello')\n")

    info = discover_skill(skill_dir)
    result = run_skill_stage(info, "sim", timeout=10, api_key="sk_test_fake")
    # Sim stage: pass if no crash, even without automaton output
    assert result["status"] == "pass"
    assert result["candidates_found"] == 0
    assert result["warnings"]  # Should warn about no candidates


def test_run_skill_paper_requires_trades(tmp_path):
    """Paper stage should fail if no trades executed."""
    skill_dir = tmp_path / "skills" / "no-trade-skill"
    skill_dir.mkdir(parents=True)
    clawhub = {"automaton": {"entrypoint": "notrade.py"}}
    (skill_dir / "clawhub.json").write_text(json.dumps(clawhub))
    script = '''import os, json
if os.environ.get("AUTOMATON_MANAGED"):
    print(json.dumps({"automaton": {"signals": 5, "trades_attempted": 0, "trades_executed": 0}}))
'''
    (skill_dir / "notrade.py").write_text(script)

    info = discover_skill(skill_dir)
    result = run_skill_stage(info, "paper", timeout=10, api_key="sk_test_fake")
    assert result["status"] == "fail"
    assert any("no trades executed" in e for e in result["errors"])


# --- Task 3: Report formatting ---

def test_format_report_all_pass():
    results = {
        "sim": {"status": "pass", "candidates_found": 3, "trades_attempted": 2,
                "trades_executed": 2, "duration_s": 4.2, "errors": [], "warnings": []},
        "paper": {"status": "pass", "candidates_found": 5, "trades_attempted": 3,
                  "trades_executed": 3, "duration_s": 8.1, "errors": [], "warnings": []},
    }
    report = format_report("test-skill", results)
    assert report["recommendation"] == "PASS — ready for ClawHub publish"
    assert report["skill"] == "test-skill"
    assert report["stages"]["sim"]["status"] == "pass"


def test_format_report_fail():
    results = {
        "sim": {"status": "fail", "candidates_found": 0, "trades_attempted": 0,
                "trades_executed": 0, "duration_s": 1.0, "errors": ["crash"], "warnings": []},
    }
    report = format_report("test-skill", results)
    assert "FAIL" in report["recommendation"]


def test_format_report_warnings():
    results = {
        "sim": {"status": "pass", "candidates_found": 0, "trades_attempted": 0,
                "trades_executed": 0, "duration_s": 3.0, "errors": [],
                "warnings": ["no candidates found on sim venue"]},
        "paper": {"status": "pass", "candidates_found": 5, "trades_attempted": 2,
                  "trades_executed": 2, "duration_s": 6.0, "errors": [], "warnings": []},
    }
    report = format_report("test-skill", results)
    assert "warnings" in report["recommendation"].lower()
    assert "warnings" in report["stages"]["sim"]
