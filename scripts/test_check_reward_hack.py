"""Behavior checks for the PR test-change gate."""

import difflib
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


@pytest.mark.parametrize("argument", ["float(1)", "max(float(1), 2)", "len(')')"])
def test_nested_argument_does_not_hide_widened_tolerance(argument):
    patch = diff("tests/test_demo.py",
                 [f"value = pytest.approx({argument}, abs=0.01)"],
                 [f"value = pytest.approx({argument}, abs=0.1)"])
    assert [(f.line, f.reason) for f in check_diff(patch, "")] == [
        (10, "widened tolerance abs from 0.01 to 0.1"),
    ]


def test_reordered_keywords_compare_each_tolerance_with_its_old_value():
    patch = diff("tests/test_demo.py",
                 ["value = pytest.approx(1, abs=0.01, rel=1e-6)"],
                 ["value = pytest.approx(1, rel=1e-3, abs=0.1)"])
    assert {f.reason for f in check_diff(patch, "")} == {
        "widened tolerance abs from 0.01 to 0.1",
        "widened tolerance rel from 1e-06 to 0.001",
    }


@pytest.mark.parametrize("declaration", ["it", "test"])
def test_deleted_multiline_js_test_reports_declaration_start(declaration):
    patch = diff("mcp/src/demo.test.ts", removed=[
        f"  {declaration}(", '    "deleted",', "    () => {},", "  );",
    ])
    assert [(f.line, f.reason) for f in check_diff(patch, "")] == [
        (10, "removed test deleted"),
    ]


@pytest.mark.parametrize("path,command", [
    ("tests/test_demo.py", "assert actual == expected"),
    ("mcp/src/demo.test.ts", "expect(actual).toBe(expected);"),
    (".github/workflows/ci.yml", "pytest tests/"),
])
def test_reindent_and_move_unchanged_checks_is_clean(path, command):
    patch = diff(path, removed=[f"    {command}"])
    patch += f"@@ -30,0 +29,1 @@\n+        {command}\n"
    assert check_diff(patch, "") == []


@pytest.mark.parametrize("path,command,reason", [
    ("tests/test_demo.py", "assert actual == expected", "removed assertion"),
    (".github/workflows/ci.yml", "pytest tests/", "removed CI test step or command"),
])
def test_only_readded_occurrences_cancel_removed_checks(path, command, reason):
    patch = diff(path, removed=[f"    {command}", f"    {command}"],
                 added=[f"        {command}"])
    assert [(f.line, f.reason) for f in check_diff(patch, "")] == [(11, reason)]


@pytest.mark.parametrize("ending", ["\n", "\r\n"])
def test_reason_cannot_borrow_explanation_from_next_line(ending):
    body = ("Test change reason: tests/test_demo.py -" + ending
            + "Test change reason: tests/other.py - unrelated")
    assert check_diff(diff("tests/test_demo.py", added=["@pytest.mark.skip"]), body)


@pytest.mark.parametrize("patch", [
    diff("tests/test_demo.py",
         ["value = pytest.approx(float(1), abs=0.01, rel=1e-6)"],
         ["value = pytest.approx(float(1), rel=1e-6, abs=0.01)"]),
    diff("mcp/src/demo.test.ts", ["it(", '  "kept",', "  () => {},", ");"],
         ['test("kept", () => {});']),
    diff("tests/test_demo.py", ["    assert ok", "    assert ok"],
         ["        assert ok", "        assert ok"]),
    diff(".github/workflows/ci.yml", ["    pytest tests/", "    pytest tests/"],
         ["        pytest tests/", "        pytest tests/"]),
])
def test_clean_refactors_produce_zero_findings(patch):
    assert check_diff(patch, "") == []


@pytest.mark.parametrize("extra", ["1", "2", "1, abs=0.001"])
def test_inserted_call_does_not_hide_existing_tolerance_widening(extra):
    patch = diff("tests/test_demo.py",
                 ["value = pytest.approx(1, abs=0.01)"],
                 [f"extra = pytest.approx({extra})", "value = pytest.approx(1, abs=0.1)"])
    assert [(f.line, f.reason) for f in check_diff(patch, "")] == [
        (11, "widened tolerance abs from 0.01 to 0.1"),
    ]


def test_reordering_unchanged_calls_is_clean():
    calls = ["first = pytest.approx(1, abs=0.01)",
             "second = pytest.approx(2, abs=0.1)"]
    assert check_diff(diff("tests/test_demo.py", calls, calls[::-1]), "") == []


def test_nested_keyword_does_not_overwrite_outer_tolerance():
    patch = diff("tests/test_demo.py",
                 ["value = pytest.approx(abs=0.01, expected=calculate(abs=1))"],
                 ["value = pytest.approx(abs=0.1, expected=calculate(abs=1))"])
    assert [(f.line, f.reason) for f in check_diff(patch, "")] == [
        (10, "widened tolerance abs from 0.01 to 0.1"),
    ]


@pytest.mark.parametrize("expected", [
    '"abs=1"',
    "calculate(abs=1)",
    '{"abs": [calculate(abs=1)]}',
])
def test_only_outer_keyword_controls_tolerance(expected):
    patch = diff("tests/test_demo.py",
                 [f"value = pytest.approx(abs=0.01, expected={expected})"],
                 [f"value = pytest.approx(abs=0.1, expected={expected})"])
    assert [f.reason for f in check_diff(patch, "")] == [
        "widened tolerance abs from 0.01 to 0.1",
    ]


def test_comment_cannot_overwrite_outer_tolerance():
    patch = diff("tests/test_demo.py",
                 ["value = pytest.approx(abs=0.01,  # abs=1", "    expected=1)"],
                 ["value = pytest.approx(abs=0.1,  # abs=1", "    expected=1)"])
    assert [f.reason for f in check_diff(patch, "")] == [
        "widened tolerance abs from 0.01 to 0.1",
    ]


@pytest.mark.parametrize("checkout", [False, True])
@pytest.mark.parametrize("ending", [",\n    )\n", ")\n"])
def test_default_diff_context_cannot_hide_tolerance_change(tmp_path, monkeypatch, checkout, ending):
    import check_reward_hack as checker

    path = "tests/test_long.py"
    old = ("def test_long():\n"
           "    value = pytest.approx(\n"
           "        [\n"
           "            1,\n"
           "            2,\n"
           "            3,\n"
           "            4,\n"
           "        ],\n"
           "        abs=0.01" + ending)
    new = old.replace("abs=0.01", "abs=0.1")
    patch = f"diff --git a/{path} b/{path}\n" + "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    ))
    assert "pytest.approx(" not in patch
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    if checkout:
        target = tmp_path / path
        target.parent.mkdir()
        target.write_text(new)
    findings = check_diff(patch, "")
    assert len(findings) == 1
    assert findings[0].line == 9
    assert "tolerance abs" in findings[0].reason
    if checkout:
        assert findings[0].reason == "widened tolerance abs from 0.01 to 0.1"


@pytest.mark.parametrize("command", [
    "pytest tests/",
    "|\n      pytest tests/\n      npm test",
    ">\n      pytest tests/",
])
def test_workflow_step_display_name_change_is_clean(command):
    path = ".github/workflows/ci.yml"
    old = f"steps:\n  - name: Run tests\n    run: {command}\n"
    new = old.replace("Run tests", "Run unit tests")
    patch = f"diff --git a/{path} b/{path}\n" + "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    ))
    assert check_diff(patch, "") == []


@pytest.mark.parametrize("old_value,new_value,expected", [
    ("0.01", "0.1", ["widened tolerance abs from 0.01 to 0.1"]),
    ("0.1", "0.01", []),
])
def test_checkout_recovery_across_multiple_hunks(tmp_path, monkeypatch,
                                              old_value, new_value, expected):
    import check_reward_hack as checker

    path = "tests/test_recovery.py"
    old = ("import pytest\n\n" + "\n" * 10
           + "def test_long():\n    value = pytest.approx(\n        [\n"
           + "            1,\n" * 10
           + f"        ],\n        abs={old_value},\n    )\n"
           + "\n" * 10 + "obsolete = 1\n")
    new = (old.replace("import pytest", "import pytest\nimport math")
           .replace(f"abs={old_value}", f"abs={new_value}")
           .replace("obsolete = 1\n", ""))
    patch = f"diff --git a/{path} b/{path}\n" + "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    ))
    assert "pytest.approx(" not in patch
    monkeypatch.setattr(checker, "ROOT", tmp_path)
    target = tmp_path / path
    target.parent.mkdir()
    target.write_text(new)
    findings = check_diff(patch, "")
    assert [f.reason for f in findings] == expected
    if findings:
        assert new.splitlines()[findings[0].line - 1].strip() == f"abs={new_value},"
