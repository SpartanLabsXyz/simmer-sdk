"""Behavior checks for the PR test-change gate."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from check_reward_hack import check_diff


ROOT = Path(__file__).resolve().parents[1]


def diff(path, removed=(), added=(), context=()):
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        f"@@ -10,{len(removed) + len(context)} +10,{len(added) + len(context)} @@\n"
        + "".join(f" {line}\n" for line in context)
        + "".join(f"-{line}\n" for line in removed)
        + "".join(f"+{line}\n" for line in added)
    )


CASES = [
    ("tests/test_demo.py", [], [line])
    for line in (
        '@pytest.mark.skip(reason="later")',
        '@pytest.mark.skipif(True, reason="later")',
        '@pytest.mark.xfail(reason="later")',
        '    pytest.skip("later")',
        '@unittest.skip("later")',
        '@unittest.skipIf(True, "later")',
        '    self.skipTest("later")',
    )
] + [
    ("mcp/src/demo.test.ts", [], [line])
    for line in (
        "it.skip('works', () => {});",
        "describe.skip('works', () => {});",
        "test.skip('works', () => {});",
        "test.todo('works');",
        "it.only('works', () => {});",
        "describe.only('works', () => {});",
        "test.only('works', () => {});",
        "it.todo('works');",
        "describe.todo('works');",
    )
] + [
    ("tests/test_demo.py", ["def test_deleted():"], []),
    ("tests/test_demo.py", ["async def test_deleted():"], []),
    ("mcp/src/demo.test.js", ["it('deleted', () => {});"], []),
    ("mcp/tests/demo.ts", ['test("deleted", () => {});'], []),
    ("tests/test_demo.py", ["    assert actual == expected"], []),
    ("tests/test_demo.py", ["    self.assertEqual(actual, expected)"], []),
    ("mcp/src/demo.test.ts", ["    expect(actual).toBe(expected);"], []),
    ("tests/test_demo.py", ["    value = pytest.approx(1, abs=0.01)"],
     ["    value = pytest.approx(1, abs=0.1)"]),
    ("tests/test_demo.py", ["    value = pytest.approx(1, rel=1e-6)"],
     ["    value = pytest.approx(1, rel=1e-3)"]),
    ("tests/test_demo.py", ["    self.assertAlmostEqual(a, b, places=7)"],
     ["    self.assertAlmostEqual(a, b, places=3)"]),
    ("tests/test_demo.py", ["    self.assertAlmostEqual(a, b, delta=0.01)"],
     ["    self.assertAlmostEqual(a, b, delta=0.1)"]),
    (".github/workflows/ci.yml", ["      - run: pytest tests/"], []),
    (".github/workflows/ci.yml", ["      - name: Run tests"], []),
    (".github/workflows/ci.yml", ["          npm test"], []),
]


@pytest.mark.parametrize("path,removed,added", CASES)
def test_suspicious_change_needs_file_reason(path, removed, added):
    patch = diff(path, removed, added)
    findings = check_diff(patch, "")
    assert findings
    assert all(f.path == path and f.line >= 10 for f in findings)
    assert check_diff(patch, f"Test change reason: {path} - intentional regression coverage update") == []


@pytest.mark.parametrize("body", [
    "Test change reason: tests/test_demo.py.bak - unrelated",
    "Mention tests/test_demo.py here",
    "Test change reason: tests/other.py - unrelated",
    "Test change reason: tests/test_demo.py",
    "Test change reason: tests/test_demo.py -",
])
def test_reason_must_name_exact_file_and_explain(body):
    assert check_diff(diff("tests/test_demo.py", added=["@pytest.mark.skip"]), body)


def test_reason_is_file_specific_and_accepts_markdown_paths():
    patch = diff("tests/test_a.py", added=["@pytest.mark.skip"])
    patch += diff("tests/test_b.py", added=["@pytest.mark.xfail"])
    findings = check_diff(patch, "Test change reason: `tests/test_a.py` - platform limitation")
    assert [f.path for f in findings] == ["tests/test_b.py"]


@pytest.mark.parametrize("patch", [
    "",
    diff("src/client.py", removed=["    assert valid"]),
    diff("tests/test_demo.py", added=["def test_new():", "    assert True"]),
    diff("tests/test_demo.py", removed=["def test_kept():"], added=["def test_kept():"]),
    diff("mcp/src/demo.test.ts", removed=["it('kept', () => {"], added=['test("kept", () => {']),
    diff("tests/test_demo.py", removed=["value = pytest.approx(1, abs=0.1)"],
         added=["value = pytest.approx(1, abs=0.01)"]),
    diff("tests/test_demo.py", added=['    # pytest.skip("example")']),
])
def test_clean_diff(patch):
    assert check_diff(patch, "") == []


def test_multiline_tolerance_with_unchanged_call():
    patch = diff("tests/test_demo.py", ["        rel=0.001,"], ["        rel=0.01,"],
                 ["    expected = pytest.approx(", "        1.0,"])
    assert any("tolerance" in f.reason for f in check_diff(patch, ""))


def test_multiple_findings_keep_original_line_numbers():
    patch = diff("tests/test_demo.py", ["def test_gone():", "    assert ok"],
                 ["@pytest.mark.skip", "def test_other():"])
    findings = check_diff(patch, "")
    assert {(f.line, f.reason) for f in findings} >= {
        (10, "added skip, xfail, todo or only"),
        (10, "removed test test_gone"),
        (11, "removed assertion"),
    }


def test_cli_reads_event_and_reports_every_finding(tmp_path):
    patch = tmp_path / "skip.diff"
    patch.write_text(diff("tests/test_demo.py", added=["@pytest.mark.skip", "@pytest.mark.xfail"]))
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"body": ""}}))
    env = {**os.environ, "GITHUB_EVENT_PATH": str(event)}
    command = [sys.executable, str(ROOT / "scripts/check_reward_hack.py"), "--diff-file", str(patch)]
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "not mergeable" in result.stdout
    assert "tests/test_demo.py:10" in result.stdout
    assert "tests/test_demo.py:11" in result.stdout
    assert "Test change reason: tests/test_demo.py - <explain why this change is needed>" in result.stdout
    event.write_text(json.dumps({"pull_request": {"body": "Test change reason: tests/test_demo.py - exercise the gate"}}))
    result = subprocess.run(command, env=env, capture_output=True, text=True)
    assert result.returncode == 0


@pytest.mark.parametrize("call,key,old,new", [
    ("pytest.approx", "abs", "0.01", "0.1"),
    ("pytest.approx", "rel", "1e-6", "1e-3"),
    ("self.assertAlmostEqual", "places", "7", "3"),
    ("self.assertAlmostEqual", "delta", "0.01", "0.1"),
])
def test_widened_tolerance_is_reported_explicitly(call, key, old, new):
    patch = diff("tests/test_demo.py", [f"    result = {call}(a, {key}={old})"],
                 [f"    result = {call}(a, {key}={new})"])
    assert any("widened tolerance" in f.reason for f in check_diff(patch, ""))
    patch = diff("tests/test_demo.py", [f"    result = {call}(a, {key}={new})"],
                 [f"    result = {call}(a, {key}={old})"])
    assert check_diff(patch, "") == []


def test_deleted_file_uses_old_path():
    patch = (
        "diff --git a/tests/test_deleted.py b/tests/test_deleted.py\n"
        "deleted file mode 100644\n--- a/tests/test_deleted.py\n+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n-def test_deleted():\n-    assert True\n"
    )
    findings = check_diff(patch, "")
    assert len(findings) == 2
    assert {(f.path, f.line) for f in findings} == {
        ("tests/test_deleted.py", 1), ("tests/test_deleted.py", 2),
    }


def test_git_comparison_uses_pr_base_and_fails_closed(tmp_path, monkeypatch, capsys):
    import check_reward_hack as checker

    subprocess.run(["git", "init", "--bare", str(tmp_path)], check=True, capture_output=True)
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    monkeypatch.setenv("GITHUB_BASE_REF", "feature/base")
    monkeypatch.setattr(sys, "argv", ["check_reward_hack.py"])
    assert checker.main() == 2
    assert "failed to read inputs" in capsys.readouterr().err


def test_default_git_command_and_event_body(monkeypatch, capsys, tmp_path):
    import check_reward_hack as checker

    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"body": None}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_BASE_REF", "release")
    commands = []

    def fake_check_output(command, **kwargs):
        commands.append(command)
        return diff("tests/test_demo.py", added=["@pytest.mark.skip"])

    monkeypatch.setattr(subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(sys, "argv", ["check_reward_hack.py"])
    assert checker.main() == 1
    assert commands == [["git", "-c", "core.quotePath=false", "diff", "--no-ext-diff",
                         "--no-renames", "origin/release...HEAD"]]
    assert "not mergeable" in capsys.readouterr().out
