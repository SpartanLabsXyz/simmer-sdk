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
ASSERT = re.compile(r'\bassert\b|\bself\.assert\w*\s*\(')
TOLERANCE = re.compile(r"\b(abs|rel|places|delta)\s*=\s*((?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)")


def is_test(path: str) -> bool:
    p = Path(path)
    return (p.suffix == '.py' and (p.name.startswith('test_') or p.stem.endswith('_test') or 'tests' in p.parts)) or (
        path.startswith('mcp/') and p.suffix in {'.js', '.jsx', '.ts', '.tsx'} and
        ('tests' in p.parts or '__tests__' in p.parts or re.search(r'\.(test|spec)\.', p.name) is not None))


def tolerance_calls(lines):
    """Collect named keywords per call, including multiline continuations."""
    calls = []
    current = None
    depth = 0
    for number, text in lines:
        starts = list(re.finditer(r'\b(?:approx|assertAlmostEqual)\s*\(', text))
        chunks = [(m.start(), starts[i + 1].start() if i + 1 < len(starts) else len(text))
                  for i, m in enumerate(starts)] or [(0, len(text))]
        for start, end in chunks:
            chunk = text[start:end]
            if starts or current is None:
                if not starts and not re.match(r'\s*(?:abs|rel|places|delta)\s*=', chunk):
                    continue
                current = {}
                calls.append((number, current))
                depth = 0
            current.update(TOLERANCE.findall(chunk))
            depth += chunk.count('(') - chunk.count(')')
            if depth <= 0:
                current = None
    return calls


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
                hunk = {'+': [], '-': [], 'old': [], 'new': []}
                hunks.append((path, hunk))
        elif in_hunk and line[:1] in {'+', '-', ' '}:
            sign, content = line[0], line[1:]
            if sign in '+-':
                entry = (new if sign == '+' else old, content)
                changes[path][sign].append(entry)
                hunk[sign].append(entry)
            if sign != '+':
                hunk['old'].append((old, content))
            if sign != '-':
                hunk['new'].append((new, content))
            old += sign != '+'
            new += sign != '-'

    result = []
    for path, lines in changes.items():
        added = lines['+']
        added_text = {text.strip() for _, text in added}
        removed_markers = Counter(text.strip() for _, text in lines['-'])
        added_defs = {m.group(0) for _, text in added if (m := TEST_DEF.search(text))}
        for number, text in added:
            if (is_test(path) or path.startswith('.github/workflows/')) and (SKIP.search(text) or JS_SKIP.search(text)):
                if removed_markers[text.strip()]:
                    removed_markers[text.strip()] -= 1
                    continue
                result.append(Finding(path, number, 'added skip, xfail, todo, or only marker'))
        for number, text in lines['-']:
            if is_test(path):
                definition = TEST_DEF.search(text)
                if definition and definition.group(0) not in added_defs:
                    result.append(Finding(path, number, 'removed test'))
            if path.startswith('.github/workflows/') and re.search(r'\b(?:pytest|test|tests)\b', text, re.I) and text.strip() not in added_text:
                result.append(Finding(path, number, 'removed test or pytest CI step'))
    for path, hunk in hunks:
        if not is_test(path):
            continue
        removed_assertions = [(n, t) for n, t in hunk['-'] if ASSERT.search(t)]
        added_assertions = sum(bool(ASSERT.search(t)) for _, t in hunk['+'])
        unchanged = Counter(t.strip() for _, t in hunk['+'] if ASSERT.search(t))
        missing = len(removed_assertions) - added_assertions
        for number, text in removed_assertions:
            if unchanged[text.strip()]:
                unchanged[text.strip()] -= 1
            elif missing > 0:
                result.append(Finding(path, number, 'removed assertion'))
                missing -= 1
        # Context lets continuation-only edits inherit an open assert/approx call.
        depth = 0
        added_text = Counter(t.strip() for _, t in hunk['+'])
        removed_numbers = {n for n, _ in hunk['-']}
        for number, text in hunk['old']:
            if depth > 0 and number in removed_numbers and not ASSERT.search(text):
                if added_text[text.strip()]:
                    added_text[text.strip()] -= 1
                elif not TOLERANCE.search(text):
                    result.append(Finding(path, number, 'modified assertion'))
            if depth > 0 or re.search(r'\b(?:assert|approx)\s*\(', text):
                depth = max(0, depth + text.count('(') - text.count(')'))
        for (_, before), (number, after) in zip(tolerance_calls(hunk['old']), tolerance_calls(hunk['new'])):
            for key in before.keys() & after.keys():
                widened = float(after[key]) < float(before[key]) if key == 'places' else float(after[key]) > float(before[key])
                if widened:
                    result.append(Finding(path, number, f'widened {key} tolerance'))
    return list(dict.fromkeys(result))


def unexplained(diff: str, body: str) -> list[Finding]:
    # Require an exact file token and an explanation on the same marker line.
    reasons = [line.split(':', 1)[1].strip() for line in body.splitlines()
               if re.match(r'^\s*Test change reason:', line, re.I)]
    return [f for f in findings(diff) if not any(
        re.search(r'(?<![\w./-])' + re.escape(f.path) + r'(?![\w./-])', reason)
        and reason.replace(f.path, '').strip(' `:;,-—')
        and '<explain' not in reason.lower() for reason in reasons)]


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
