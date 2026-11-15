#!/usr/bin/env python3
"""Require file-specific PR explanations for potentially weakened tests."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    reason: str

    @property
    def instruction(self) -> str:
        return f"Test change reason: {self.path} — <explain why this change is needed>"


SKIP = re.compile(r"pytest\.mark\.(?:skip|skipif|xfail)\b|pytest\.(?:skip|xfail)\s*\(|unittest\.skip\w*\b|self\.skipTest\b")
JS_SKIP = re.compile(r"\b(?:it|describe|test)\.(?:skip|todo|only)\b")
TEST_DEF = re.compile(r"\bdef\s+(test_\w+)\s*\(|\b(?:it|test)\s*\(\s*(['\"`].*?['\"`])")
TOLERANCE = re.compile(r"\b(abs|rel|places|delta)\s*=\s*((?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)")


def is_test(path: str) -> bool:
    p = Path(path)
    return (p.suffix == '.py' and (p.name.startswith('test_') or p.stem.endswith('_test') or 'tests' in p.parts)) or (
        path.startswith('mcp/') and p.suffix in {'.js', '.jsx', '.ts', '.tsx'} and
        ('tests' in p.parts or '__tests__' in p.parts or re.search(r'\.(test|spec)\.', p.name) is not None))


def findings(diff: str) -> list[Finding]:
    changes: dict[str, dict[str, list[tuple[int, str]]]] = {}
    hunks = []
    path = old_path = ''
    old = new = 0
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith('diff --git '):
            in_hunk = False
        elif line.startswith('--- '):
            old_path = line[4:].removeprefix('a/').split('\t')[0]
        elif line.startswith('+++ '):
            path = line[4:].removeprefix('b/').split('\t')[0]
            if path == '/dev/null':
                path = old_path
            changes.setdefault(path, {'+': [], '-': []})
        elif line.startswith('@@ '):
            match = re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)', line)
            if match:
                old, new = map(int, match.groups())
                in_hunk = True
                hunk = {'+': [], '-': []}
                hunks.append((path, hunk))
        elif in_hunk and line[:1] in {'+', '-', ' '}:
            sign, content = line[0], line[1:]
            if sign in '+-':
                entry = (new if sign == '+' else old, content)
                changes[path][sign].append(entry)
                hunk[sign].append(entry)
            old += sign != '+'
            new += sign != '-'

    result = []
    for path, lines in changes.items():
        added = lines['+']
        added_text = {text.strip() for _, text in added}
        added_assertions = Counter(text.strip() for _, text in added)
        added_defs = {m.group(0) for _, text in added if (m := TEST_DEF.search(text))}
        for number, text in added:
            if SKIP.search(text) or (is_test(path) and path.startswith('mcp/') and JS_SKIP.search(text)):
                result.append(Finding(path, number, 'added skip, xfail, todo, or only marker'))
        for number, text in lines['-']:
            if is_test(path):
                definition = TEST_DEF.search(text)
                if definition and definition.group(0) not in added_defs:
                    result.append(Finding(path, number, 'removed test'))
                if re.search(r'\bassert\b|\bself\.assert\w*\s*\(', text):
                    if added_assertions[text.strip()]:
                        added_assertions[text.strip()] -= 1
                    else:
                        result.append(Finding(path, number, 'removed assertion'))
            if path.startswith('.github/workflows/') and re.search(r'\b(?:pytest|test|tests)\b', text, re.I) and text.strip() not in added_text:
                result.append(Finding(path, number, 'removed test or pytest CI step'))
    for path, hunk in hunks:
        if not is_test(path):
            continue
        # Pair tolerance-bearing call lines (including keyword continuations) in order.
        tolerances = {}
        for sign in '+-':
            tolerances[sign] = [
                (number, TOLERANCE.findall(text)) for number, text in hunk[sign]
                if 'approx' in text or 'assertAlmostEqual' in text
                or re.match(r'\s*(?:abs|rel|places|delta)\s*=', text)
            ]
        for (_, before), (number, after) in zip(tolerances['-'], tolerances['+']):
            for (key, value), (new_key, new_value) in zip(before, after):
                if key == new_key and ((float(new_value) < float(value)) if key == 'places' else (float(new_value) > float(value))):
                    result.append(Finding(path, number, f'widened {key} tolerance'))
    return list(dict.fromkeys(result))


def unexplained(diff: str, body: str) -> list[Finding]:
    # Require an exact file token and an explanation on the same marker line.
    reasons = [line.split(':', 1)[1].strip() for line in body.splitlines()
               if re.match(r'^\s*Test change reason:', line, re.I)]
    return [f for f in findings(diff) if not any(
        re.search(r'(?<![\w./-])' + re.escape(f.path) + r'(?![\w./-])', reason)
        and reason.replace(f.path, '').strip(' `:;,-—') for reason in reasons)]


def main() -> int:
    base = os.environ.get('GITHUB_BASE_REF')
    ref = f'origin/{base}' if base else 'HEAD~1'
    diff = subprocess.check_output(['git', 'diff', '--no-ext-diff', '--no-renames', '--unified=0', f'{ref}...HEAD'], text=True)
    event_path = os.environ.get('GITHUB_EVENT_PATH')
    event = json.loads(Path(event_path).read_text()) if event_path else {}
    body = (event.get('pull_request') or {}).get('body') or ''
    failures = unexplained(diff, body)
    for finding in failures:
        print(f'{finding.path}:{finding.line}: {finding.reason}\n  Add to PR body: {finding.instruction}')
    print('not mergeable' if failures else 'Reward-hack check passed')
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())
