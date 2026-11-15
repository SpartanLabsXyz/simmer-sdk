#!/usr/bin/env python3
"""Require file-specific PR explanations for potentially weakened tests."""
import ast
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
JS_ASSERT = re.compile(r'\bassert\b|\bself\.assert\w*\s*\(')
JS_SKIP = re.compile(r"\b(?:it|describe|test)\.(?:skip|todo|only)\b")
TEST_DEF = re.compile(r"\b(?:it|test)\s*\(\s*(['\"`].*?['\"`])")


def is_test(path: str) -> bool:
    p = Path(path)
    return (p.suffix == '.py' and (p.name.startswith('test_') or p.stem.endswith('_test') or 'tests' in p.parts)) or (
        path.startswith('mcp/') and p.suffix in {'.js', '.jsx', '.ts', '.tsx'} and
        ('tests' in p.parts or '__tests__' in p.parts or re.search(r'\.(test|spec)\.', p.name) is not None))


def dotted_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f'{dotted_name(node.value)}.{node.attr}'
    return ''


def tolerance_names(node):
    if isinstance(node, ast.Call):
        name = dotted_name(node.func)
        if name == 'pytest.approx':
            return {'abs', 'rel'}
        if name.endswith('.assertAlmostEqual'):
            return {'places', 'delta'}
    return set()


def call_arguments(node):
    args = list(node.args)
    keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
    # unittest also accepts places, msg and delta positionally.
    if dotted_name(node.func).endswith('.assertAlmostEqual'):
        keywords.update(zip(('places', 'msg', 'delta'), args[2:]))
        args = args[:2]
    return args, keywords


def structure(node, *, without_tolerances=False):
    """Location-free AST identity; keyword order does not change a call."""
    if isinstance(node, ast.Call):
        args, keywords = call_arguments(node)
        ignored = tolerance_names(node) if without_tolerances else set()
        return ('Call', structure(node.func),
                tuple(structure(arg, without_tolerances=without_tolerances) for arg in args),
                tuple((key, structure(value, without_tolerances=without_tolerances))
                      for key, value in sorted(keywords.items()) if key not in ignored),
                tuple(structure(kw.value) for kw in node.keywords if kw.arg is None))
    if isinstance(node, ast.AST):
        return (type(node).__name__, tuple(
            (field, structure(value, without_tolerances=without_tolerances))
            for field, value in ast.iter_fields(node)
            if not (isinstance(node, ast.Assert) and field == 'msg')))
    if isinstance(node, list):
        return tuple(structure(value, without_tolerances=without_tolerances) for value in node)
    return node


def is_assertion(node):
    if isinstance(node, ast.Assert):
        return True
    if isinstance(node, (ast.Expr, ast.Assign, ast.AnnAssign, ast.Return)):
        return any(isinstance(child, ast.Call) and (
            dotted_name(child.func).startswith('self.assert') or tolerance_names(child))
            for child in ast.walk(node))
    return False


def tolerance_calls(node):
    """Traverse structurally matched checks in canonical keyword order."""
    if isinstance(node, ast.Call):
        if tolerance_names(node):
            yield node
        args, keywords = call_arguments(node)
        children = [node.func, *args, *(value for key, value in sorted(keywords.items())
                                      if key not in tolerance_names(node))]
        children.extend(kw.value for kw in node.keywords if kw.arg is None)
    elif isinstance(node, ast.Assert):
        children = [node.test]
    else:
        children = ast.iter_child_nodes(node)
    for child in children:
        yield from tolerance_calls(child)


def python_checks(tree):
    """Keep test ownership for changed checks, and file-wide identity for moves."""
    tests, assertions, markers = [], [], []

    def visit(node, scope=(), in_assertion=False):
        if isinstance(node, ast.ClassDef):
            scope = (*scope, node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = (*scope, node.name)
            if node.name.startswith('test_'):
                tests.append((scope, node))
        if not in_assertion and is_assertion(node):
            assertions.append((scope, node))
            in_assertion = True
        name = dotted_name(node.func if isinstance(node, ast.Call) else node)
        if name in {'pytest.mark.skip', 'pytest.mark.skipif', 'pytest.mark.xfail',
                    'pytest.skip', 'pytest.xfail', 'unittest.skip', 'unittest.skipIf',
                    'unittest.skipUnless', 'self.skipTest'}:
            markers.append((scope, node))
            return  # Do not count both a marker call and its function attribute.
        for child in ast.iter_child_nodes(node):
            visit(child, scope, in_assertion)

    visit(tree)
    return tests, assertions, markers


def match_checks(before, after, key):
    """Consume each occurrence once, preserving duplicate-removal detection."""
    remaining = list(after)
    pairs, removed = [], []
    for old in before:
        match = next((i for i, new in enumerate(remaining) if key(old) == key(new)), None)
        if match is None:
            removed.append(old)
        else:
            pairs.append((old, remaining.pop(match)))
    return pairs, removed, remaining


def numeric_literal(node):
    if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = numeric_literal(node.operand)
        if value is not None:
            return -value if isinstance(node.op, ast.USub) else value
    return None


def python_findings(path, before, after):
    try:
        old = python_checks(ast.parse(before))
        new = python_checks(ast.parse(after))
    except SyntaxError as error:
        return [Finding(path, error.lineno or 1, 'cannot parse Python test snapshot')]

    result = []
    _, removed_tests, added_tests = match_checks(old[0], new[0], lambda item: item[0])
    # A test moved to a different class is a re-add, but duplicate names still count.
    _, removed_tests, _ = match_checks(removed_tests, added_tests, lambda item: item[1].name)
    result.extend(Finding(path, node.lineno, 'removed test') for _, node in removed_tests)

    _, _, added_markers = match_checks(old[2], new[2], lambda item: structure(item[1]))
    result.extend(Finding(path, node.lineno, 'added skip, xfail, todo, or only marker')
                  for _, node in added_markers)

    # Resolve same-owner checks before file-wide moves can hide tolerance changes.
    _, assertions_before, assertions_after = match_checks(
        old[1], new[1], lambda item: (item[0], structure(item[1])))
    matched, assertions_before, assertions_after = match_checks(
        assertions_before, assertions_after,
        lambda item: (item[0], structure(item[1], without_tolerances=True)))
    _, assertions_before, assertions_after = match_checks(
        assertions_before, assertions_after, lambda item: structure(item[1]))
    moved, assertions_before, assertions_after = match_checks(
        assertions_before, assertions_after,
        lambda item: structure(item[1], without_tolerances=True))
    matched.extend(moved)  # Moves must pass the same tolerance comparison.
    changed, removed, _ = match_checks(assertions_before, assertions_after, lambda item: item[0])
    result.extend(Finding(path, node.lineno, 'modified assertion') for _, (_, node) in changed)
    result.extend(Finding(path, node.lineno, 'removed assertion') for _, node in removed)

    for (_, before_check), (_, after_check) in matched:
        # Equal tolerance-free trees guarantee corresponding calls, including
        # repeated operands in different positions of the same assertion.
        for before_call, after_call in zip(tolerance_calls(before_check), tolerance_calls(after_check)):
            before_kw = call_arguments(before_call)[1]
            after_kw = call_arguments(after_call)[1]
            for name in sorted(tolerance_names(before_call)):
                before_value, after_value = before_kw.get(name), after_kw.get(name)
                if structure(before_value) == structure(after_value):
                    continue
                before_number, after_number = numeric_literal(before_value), numeric_literal(after_value)
                if before_number is None or after_number is None:
                    result.append(Finding(path, after_call.lineno, f'modified {name} tolerance'))
                elif (after_number < before_number if name == 'places' else after_number > before_number):
                    result.append(Finding(path, after_call.lineno, f'widened {name} tolerance'))
    return result


def base_ref():
    base = os.environ.get('GITHUB_BASE_REF')
    return f'origin/{base}' if base else 'HEAD~1'


def findings(diff: str, snapshots=None) -> list[Finding]:
    """Use the patch for file selection; Python checks require complete snapshots.

    snapshots optionally supplies {path: (base_source, head_source)} for offline tests.
    CI reads those sources from the same merge-base and HEAD used by git diff.
    """
    changes = {}
    paths = {}
    path = old_path = ''
    old = new = 0
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith('diff --git '):
            in_hunk = False
        elif line.startswith('--- '):
            old_path = line[4:].removeprefix('a/').split('\t')[0]
        elif line.startswith('+++ '):
            new_path = line[4:].removeprefix('b/').split('\t')[0]
            path = old_path if new_path == '/dev/null' else new_path
            paths[path] = (old_path, new_path)
            changes.setdefault(path, {'+': [], '-': []})
        elif line.startswith('@@ '):
            match = re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)', line)
            if match:
                old, new = map(int, match.groups())
                in_hunk = True
        elif in_hunk and line[:1] in {'+', '-', ' '}:
            sign, content = line[0], line[1:]
            if sign in '+-':
                changes[path][sign].append((new if sign == '+' else old, content))
            old += sign != '+'
            new += sign != '-'

    result = []
    merge_base = None
    for path, lines in changes.items():
        if is_test(path) and Path(path).suffix == '.py':
            if snapshots is not None:
                before, after = snapshots[path]
            else:
                if merge_base is None:
                    merge_base = subprocess.check_output(['git', 'merge-base', base_ref(), 'HEAD'], text=True).strip()
                before, after = (
                    '' if source_path == '/dev/null' else subprocess.check_output(
                        ['git', 'show', f'{rev}:{source_path}'], text=True)
                    for rev, source_path in zip((merge_base, 'HEAD'), paths[path]))
            result.extend(python_findings(path, before, after))
            continue
        if is_test(path):
            before = [(n, t) for n, t in lines['-'] if JS_ASSERT.search(t)]
            after = [(n, t) for n, t in lines['+'] if JS_ASSERT.search(t)]
            _, removed, added = match_checks(before, after, lambda item: item[1].strip())
            result.extend(Finding(path, number, 'removed assertion')
                          for number, _ in removed[:max(0, len(removed) - len(added))])
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
    return list(dict.fromkeys(result))


def unexplained(diff: str, body: str, snapshots=None) -> list[Finding]:
    # Require an exact file token and an explanation on the same marker line.
    reasons = [line.split(':', 1)[1].strip() for line in body.splitlines()
               if re.match(r'^\s*Test change reason:', line, re.I)]
    return [f for f in findings(diff, snapshots) if not any(
        re.search(r'(?<![\w./-])' + re.escape(f.path) + r'(?![\w./-])', reason)
        and reason.replace(f.path, '').strip(' `:;,-—')
        and '<explain' not in reason.lower() for reason in reasons)]


def main() -> int:
    ref = base_ref()
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
