#!/usr/bin/env python3
"""Require PR-body explanations for potentially weakened test coverage.

Reasons use one line per file:
Test change reason: tests/test_example.py - explain why this change is needed

This is a diff heuristic. Catch-all exceptions and hard-coded expectations remain
reviewer checks. --diff-file allows offline reproduction with a unified diff.
"""

import argparse
from collections import Counter
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SKIP = re.compile(
    r"@pytest\.mark\.(?:skipif|skip|xfail)\b|\bpytest\.skip\s*\("
    r"|\bunittest\.skip(?:If|Unless)?\b|\bself\.skipTest\s*\("
)
JS_SKIP = re.compile(r"\b(?:it|describe|test)\s*\.\s*(?:skip|todo|only)\b")
PY_TEST = re.compile(r"^[ \t]*(?:async\s+)?def\s+(test_\w+)\s*\(", re.M)
JS_TEST = re.compile(r"\b(?:it|test)\s*\(\s*(['\"`])(.*?)\1")
ASSERT = re.compile(r"^\s*(?:assert\b|self\.assert\w*\s*\(|expect\s*\(|assert[.(])")
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
CALL = re.compile(r"\b(pytest\.approx|(?:self\.)?assertAlmostEqual)\s*\(")
CALL_TOKEN = re.compile(
    r"'''(?:\\.|(?!''').)*'''|\"\"\"(?:\\.|(?!\"\"\").)*\"\"\""
    r"|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|\#[^\n]*|[()\[\]{},]", re.S,
)
CI_TEST = re.compile(r"\b(?:pytest|tests?|run_skill_tests)\b|(?:^|[/_])test[_./]", re.I)


@dataclass(frozen=True)
class Line:
    kind: str
    number: int
    text: str


@dataclass
class FileDiff:
    path: str
    lines: list[Line] = field(default_factory=list)


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    reason: str


def parse_diff(patch: str) -> list[FileDiff]:
    files = []
    current = None
    old_line = new_line = 0
    in_hunk = False
    for raw in patch.splitlines():
        if raw.startswith("diff --git "):
            current = FileDiff("")
            files.append(current)
            in_hunk = False
        elif not in_hunk and current is not None and raw.startswith(("--- ", "+++ ")):
            path = raw[4:].split("\t", 1)[0].strip('"')
            if path != "/dev/null":
                current.path = path[2:] if path.startswith(("a/", "b/")) else path
        elif raw.startswith("@@ "):
            match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if match:
                old_line, new_line = map(int, match.groups())
                in_hunk = True
        elif in_hunk and current is not None and raw[:1] in ("+", "-", " "):
            kind = raw[0]
            current.lines.append(Line(kind, old_line if kind == "-" else new_line, raw[1:]))
            old_line += kind != "+"
            new_line += kind != "-"
    return files


def is_test_file(path: str) -> bool:
    p = Path(path)
    if p.suffix == ".py":
        return p.name.startswith("test_") or p.name.endswith("_test.py") or "tests" in p.parts
    return (
        path.startswith("mcp/") and p.suffix in {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}
        and ("tests" in p.parts or "__tests__" in p.parts or re.search(r"\.(?:test|spec)\.", p.name) is not None)
    )


def indexed_text(lines: list[Line]) -> tuple[str, list[tuple[int, Line]]]:
    text = "\n".join(line.text for line in lines)
    offsets = []
    offset = 0
    for line in lines:
        offsets.append((offset, line))
        offset += len(line.text) + 1
    return text, offsets


def test_declarations(lines: list[Line], python: bool) -> list[tuple[str, Line]]:
    text, offsets = indexed_text(lines)
    return [
        (match[1] if python else match[2],
         next(line for start, line in reversed(offsets) if start <= match.start()))
        for match in (PY_TEST if python else JS_TEST).finditer(text)
    ]


def call_arguments(text: str, start: int) -> list[tuple[int, str]]:
    depth = 1
    arguments = []
    argument_start = start
    for token in CALL_TOKEN.finditer(text, start):
        if token[0] in ("(", "[", "{"):
            depth += 1
        elif token[0] in (")", "]", "}"):
            depth -= 1
            if depth == 0:
                arguments.append((argument_start, text[argument_start:token.start()]))
                return arguments
        elif token[0] == "," and depth == 1:
            arguments.append((argument_start, text[argument_start:token.start()]))
            argument_start = token.end()
    # Diff hunks may end before the call's closing parenthesis.
    arguments.append((argument_start, text[argument_start:]))
    return arguments


def normalized_argument(text: str) -> str:
    parts = []
    start = 0
    for token in CALL_TOKEN.finditer(text):
        parts.append(re.sub(r"\s+", "", text[start:token.start()]))
        if not token[0].startswith("#"):
            parts.append(token[0])
        start = token.end()
    parts.append(re.sub(r"\s+", "", text[start:]))
    return "".join(parts)


def tolerance_values(lines: list[Line]) -> tuple[list[tuple[str, dict[str, tuple[float, Line]]]], set[int]]:
    text, offsets = indexed_text(lines)
    code = CALL_TOKEN.sub(
        lambda token: " " * len(token[0]) if token[0].startswith(("#", "'", '"')) else token[0], text,
    )
    calls = []
    covered = set()
    for call in CALL.finditer(code):
        values = {}
        identity = []
        arguments = call_arguments(text, call.end())
        end = arguments[-1][0] + len(arguments[-1][1])
        covered.update(line.number for start, line in offsets
                       if start <= end and start + len(line.text) >= call.start())
        for position, argument in arguments:
            argument = CALL_TOKEN.sub(
                lambda token: " " * len(token[0]) if token[0].startswith("#") else token[0], argument,
            )
            value = re.fullmatch(rf"\s*(abs|rel|places|delta)\s*=\s*({NUMBER})\s*", argument)
            if value and value[1] in ({"abs", "rel"} if call[1] == "pytest.approx" else {"places", "delta"}):
                source = next(line for start, line in reversed(offsets) if start <= position + value.start(1))
                values[value[1]] = (float(value[2]), source)
            elif argument.strip():
                identity.append(normalized_argument(argument))
        prefix = text[text.rfind("\n", 0, call.start()) + 1:call.start()]
        calls.append((normalized_argument(prefix) + call[1] + "(" + ",".join(identity) + ")", values))
    return calls, covered


def tolerance_sources(file: FileDiff) -> tuple[list[Line], list[Line], bool]:
    old_lines = [line for line in file.lines if line.kind != "+"]
    new_lines = [line for line in file.lines if line.kind != "-"]
    path = (ROOT / file.path).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        return old_lines, new_lines, False
    try:
        head = path.read_text().splitlines()
    except (OSError, UnicodeError):
        return old_lines, new_lines, False
    if any(line.number < 1 or line.number > len(head) or head[line.number - 1] != line.text
           for line in new_lines):
        return old_lines, new_lines, False
    base = head.copy()
    edits = []
    delta = 0
    for line in file.lines:
        if line.kind == "-":
            edits.append((line.number - 1 + delta, line))
            delta -= 1
        elif line.kind == "+":
            edits.append((line.number - 1, line))
            delta += 1
    for index, line in reversed(edits):
        if index < 0 or index > len(base):
            return old_lines, new_lines, False
        if line.kind == "+":
            if index == len(base) or base[index] != line.text:
                return old_lines, new_lines, False
            del base[index]
        else:
            base.insert(index, line.text)
    removed = {line.number for line in old_lines if line.kind == "-"}
    added = {line.number for line in new_lines if line.kind == "+"}
    return (
        [Line("-" if number in removed else " ", number, text) for number, text in enumerate(base, 1)],
        [Line("+" if number in added else " ", number, text) for number, text in enumerate(head, 1)],
        True,
    )


def unchanged_step_command(name: Line, lines: list[Line]) -> bool:
    match = re.match(r"^(\s*)-\s+name:", name.text)
    if not match:
        return False
    indent = len(match[1])
    for line in lines[lines.index(name) + 1:]:
        if line.kind == "+" or not line.text.strip():
            continue
        if len(line.text) - len(line.text.lstrip()) <= indent:
            break
        if re.match(r"\s*run:\s*\S", line.text):
            if line.kind != " ":
                return False
            if not line.text.rstrip().endswith(("|", ">")):
                return True
            run_indent = len(line.text) - len(line.text.lstrip())
            for command in lines[lines.index(line) + 1:]:
                if not command.text.strip():
                    continue
                if len(command.text) - len(command.text.lstrip()) <= run_indent:
                    break
                if command.kind != " ":
                    return False
            return True
    return False


def unmatched_removed(removed: list[Line], added: list[Line]) -> list[Line]:
    readded = Counter(line.text.lstrip() for line in added)
    unmatched = []
    for line in removed:
        text = line.text.lstrip()
        if readded[text]:
            readded[text] -= 1
        else:
            unmatched.append(line)
    return unmatched


def has_reason(body: str, path: str) -> bool:
    pattern = rf"[ \t]*Test change reason:[ \t]*`?{re.escape(path)}`?[ \t]+-[ \t]+\S.*"
    return any(re.fullmatch(pattern, line) for line in body.splitlines())


def check_diff(patch: str, body: str) -> list[Finding]:
    findings = []
    for file in parse_diff(patch):
        if has_reason(body, file.path):
            continue
        added = [line for line in file.lines if line.kind == "+"]
        removed = [line for line in file.lines if line.kind == "-"]
        unmatched = unmatched_removed(removed, added)
        python = file.path.endswith(".py")
        test_file = is_test_file(file.path)

        def flag(line: Line, reason: str) -> None:
            findings.append(Finding(file.path, line.number, reason))

        for line in added:
            if line.text.lstrip().startswith(("#", "//", "/*", "*")):
                continue
            if (python and SKIP.search(line.text)) or (test_file and not python and JS_SKIP.search(line.text)):
                flag(line, "added skip, xfail, todo or only")
        if test_file:
            old_lines = [line for line in file.lines if line.kind != "+"]
            new_lines = [line for line in file.lines if line.kind != "-"]
            readded = Counter(name for name, _ in test_declarations(new_lines, python))
            for name, line in test_declarations(old_lines, python):
                if readded[name]:
                    readded[name] -= 1
                else:
                    flag(line, f"removed test {name}")
            for line in unmatched:
                if ASSERT.search(line.text):
                    flag(line, "removed assertion")
            old_sources, new_sources, recovered = tolerance_sources(file)
            old_values, _ = tolerance_values(old_sources)
            new_values, covered = tolerance_values(new_sources)
            grouped_old = {}
            grouped_new = {}
            for call, values in old_values:
                grouped_old.setdefault(call, []).append(values)
            for call, values in new_values:
                grouped_new.setdefault(call, []).append(values)
            for call, olds in grouped_old.items():
                news = grouped_new.get(call, []).copy()
                changed_old = []
                for old in olds:
                    unchanged = next((new for new in news if
                                      {key: value for key, (value, _) in old.items()} ==
                                      {key: value for key, (value, _) in new.items()}), None)
                    if unchanged is not None:
                        news.remove(unchanged)
                    else:
                        changed_old.append(old)
                for old in changed_old:
                    candidates = [candidate for candidate in news if candidate.keys() & old.keys()]
                    new = max(candidates, key=lambda candidate: len(candidate.keys() & old.keys()), default=None)
                    if new is None:
                        continue
                    news.remove(new)
                    for key, (old_value, _) in old.items():
                        if key not in new:
                            continue
                        value, line = new[key]
                        if line.kind == "+" and (value < old_value if key == "places" else value > old_value):
                            flag(line, f"widened tolerance {key} from {old_value:g} to {value:g}")
            if not recovered:
                for line in added:
                    value = re.match(rf"^\s*(abs|rel|places|delta)\s*=\s*({NUMBER})(?=\s*(?:[,)]|#|$))", line.text)
                    if value and line.number not in covered:
                        flag(line, f"changed tolerance {value[1]} without enclosing call context")
        if file.path.startswith(".github/workflows/") and file.path.endswith((".yml", ".yaml")):
            for line in unmatched:
                if (not line.text.lstrip().startswith("#") and CI_TEST.search(line.text)
                        and not unchanged_step_command(line, file.lines)):
                    flag(line, "removed CI test step or command")
    return findings


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True)


def current_pull_request() -> dict:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return {}
    with Path(event_path).open() as fh:
        event = json.load(fh)
    pr = event.get("pull_request")
    return pr if isinstance(pr, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diff-file", type=Path, help="read an offline unified diff instead of git")
    args = parser.parse_args()
    try:
        if args.diff_file:
            patch = args.diff_file.read_text()
        else:
            base = os.environ.get("GITHUB_BASE_REF", "main")
            patch = run_git(["-c", "core.quotePath=false", "diff", "--no-ext-diff", "--no-renames", f"origin/{base}...HEAD"])
        body = current_pull_request().get("body") or ""
        findings = check_diff(patch, body)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Reward-hack check failed to read inputs: {exc}", file=sys.stderr)
        return 2
    if findings:
        print("Reward-hack check: not mergeable. Explain each flagged file in the PR body.")
        for finding in findings:
            print(f"{finding.path}:{finding.line}: {finding.reason}")
            print(f"  Add: Test change reason: {finding.path} - <explain why this change is needed>")
        return 1
    print("Reward-hack check passed. No unexplained test changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
