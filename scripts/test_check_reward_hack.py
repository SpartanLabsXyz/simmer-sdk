"""Self-contained pattern and real git/event integration tests."""
import difflib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap

import pytest

import check_reward_hack as checker


def python_source(fragment):
    """Make the existing small examples complete test-file snapshots."""
    fragment = textwrap.dedent(fragment)
    if fragment.startswith('def '):
        return fragment + '\n    pass\n'
    if fragment.startswith('@'):
        return fragment + '\ndef test_demo():\n    pass\n'
    if fragment.startswith('pytestmark ='):
        return fragment + '\ndef test_demo():\n    pass\n'
    return 'def test_demo():\n' + textwrap.indent(fragment or 'pass', '    ') + '\n'


def diff(path, removed='', added='', *, full=False):
    snapshots = {}
    if path.endswith('.py'):
        if not full:
            removed, added = python_source(removed), python_source(added)
        snapshots[path] = (removed, added)
    patch = ''.join(difflib.unified_diff([line + '\n' for line in removed.splitlines()],
                                       [line + '\n' for line in added.splitlines()],
                                       fromfile=f'a/{path}', tofile=f'b/{path}', n=0))
    return patch, snapshots


def check_findings(patch):
    return checker.findings(patch[0], snapshots=patch[1])


def check_unexplained(patch, body):
    return checker.unexplained(patch[0], body, snapshots=patch[1])


@pytest.fixture
def git_repo():
    with tempfile.TemporaryDirectory(prefix='reward-hack-', dir=os.environ.get('TMPDIR', '/tmp')) as directory:
        yield Path(directory)


@pytest.mark.parametrize('marker', [
    '@pytest.mark.skip', '@pytest.mark.skipif(True)', '@pytest.mark.xfail',
    'pytest.skip("reason")', '@unittest.skip("reason")', 'self.skipTest("reason")',
])
def test_python_markers(marker):
    check_reason('tests/test_demo.py', '', marker)


@pytest.mark.parametrize('marker', ['it.skip', 'describe.skip', 'test.skip',
                                         'it.todo', 'test.todo', 'test.only', 'describe.only'])
def test_js_markers(marker):
    check_reason('mcp/tests/demo.test.ts', '', marker + '("demo", () => {})')


def check_reason(path, removed, added):
    patch = diff(path, removed, added)
    assert check_unexplained(patch, '')
    assert not check_unexplained(patch, f'Test change reason: {path} — obsolete coverage')
    assert check_unexplained(patch, f'Test change reason: {path}')
    assert check_unexplained(patch, f'Test change reason: {path}.other — obsolete coverage')
    assert all(f.line > 0 and path in f.instruction for f in check_findings(patch))


@pytest.mark.parametrize(('path', 'removed', 'added'), [
    ('tests/test_demo.py', 'def test_old():', ''),
    ('mcp/tests/demo.test.ts', 'it("old", () => {', ''),
    ('mcp/tests/demo.test.ts', 'test("old", () => {', ''),
    ('tests/test_demo.py', '    assert actual == expected', ''),
    ('tests/test_demo.py', '    self.assertEqual(actual, expected)', ''),
    ('tests/test_demo.py', 'assert x == pytest.approx(y, abs=0.01)', 'assert x == pytest.approx(y, abs=0.1)'),
    ('tests/test_demo.py', 'assert x == pytest.approx(y, rel=1e-6)', 'assert x == pytest.approx(y, rel=1e-3)'),
    ('tests/test_demo.py', 'self.assertAlmostEqual(x, y, places=7)', 'self.assertAlmostEqual(x, y, places=3)'),
    ('tests/test_demo.py', 'self.assertAlmostEqual(x, y, delta=0.01)', 'self.assertAlmostEqual(x, y, delta=0.1)'),
    ('.github/workflows/ci.yml', '      - run: pytest tests/', ''),
    ('.github/workflows/ci.yml', '      - run: npm test', ''),
])
def test_weakened_checks(path, removed, added):
    check_reason(path, removed, added)


def test_clean_diff_and_readded_test():
    assert not checker.findings('')
    assert not check_findings(diff('src/demo.py', 'value = 1', 'value = 2'))
    assert not check_findings(diff('tests/test_demo.py', 'def test_kept():', 'def test_kept():'))
    assert not check_findings(diff('mcp/tests/demo.test.ts', 'it("kept", () => {', 'it("kept", () => {'))
    assert not check_findings(diff('tests/test_demo.py', 'assert x == y', 'assert x == y'))


def test_real_git_and_event(git_repo):
    tmp_path = git_repo
    script = Path(__file__).with_name('check_reward_hack.py').resolve()

    def git(*args):
        return subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True, text=True)

    git('init', '-b', 'main')
    git('config', 'user.email', 'test@example.com')
    git('config', 'user.name', 'Test')
    test_file = tmp_path / 'test_demo.py'
    test_file.write_text('def test_demo():\n    assert True\n')
    git('add', '.')
    git('commit', '-m', 'base')
    git('update-ref', 'refs/remotes/origin/main', 'HEAD')
    test_file.write_text('import pytest\n@pytest.mark.skip\ndef test_demo():\n    assert True\n')
    git('add', '.')
    git('commit', '-m', 'add skip')
    event = tmp_path / 'event.json'
    env = dict(os.environ, GITHUB_BASE_REF='main', GITHUB_EVENT_PATH=str(event))
    for body, code in [('', 1), ('Test change reason: test_demo.py — obsolete coverage', 0)]:
        event.write_text(json.dumps({'pull_request': {'body': body}}))
        result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, env=env, capture_output=True, text=True)
        assert result.returncode == code, result.stderr
        if code:
            assert 'test_demo.py:2:' in result.stdout
            assert 'Test change reason: test_demo.py' in result.stdout
            assert 'not mergeable' in result.stdout
        else:
            assert 'Reward-hack check passed' in result.stdout
        print(f'body={body!r}; exit={result.returncode}\n{result.stdout}')


@pytest.mark.parametrize(('kind', 'before', 'after'), [
    ('abs', '.01', '.1'), ('rel', '1e-6', '1e-3'),
    ('places', '7', '3'), ('delta', '0.01', '0.1'),
])
def test_tolerance_direction(kind, before, after):
    call = 'pytest.approx' if kind in {'abs', 'rel'} else 'self.assertAlmostEqual'
    old = f'{call}(x, {kind}={before})'
    new = f'{call}(x, {kind}={after})'
    assert any(f.reason == f'widened {kind} tolerance'
               for f in check_findings(diff('tests/test_demo.py', old, new)))
    assert not any(f.reason.startswith('widened')
                   for f in check_findings(diff('tests/test_demo.py', new, old)))


@pytest.mark.parametrize('marker', [
    'pytest.xfail("reason")',
    'pytestmark = pytest.mark.skip(reason="reason")',
    'pytestmark = [pytest.mark.xfail(reason="reason")]',
])
def test_runtime_and_module_markers(marker):
    check_reason('tests/test_demo.py', '', marker)


def test_multiline_approx_tolerance():
    patch = diff('tests/test_demo.py',
                 'assert x == pytest.approx(\n    y,\n    abs=0.01,\n)',
                 'assert x == pytest.approx(\n    y,\n    abs=0.1,\n)')
    assert any(f.reason == 'widened abs tolerance' for f in check_findings(patch))


def test_removed_duplicate_assertion():
    patch = diff('tests/test_demo.py', 'assert x == y\nassert x == y', 'assert x == y')
    assert sum(f.reason == 'removed assertion' for f in check_findings(patch)) == 1


def test_unrelated_approx_tolerances():
    patch = diff('tests/test_demo.py',
                 'pytest.approx(x, abs=0.01)\npytest.approx(y, abs=0.1)',
                 'pytest.approx(x, abs=0.001)\npytest.approx(y, abs=0.05)')
    assert not check_findings(patch)


def test_approx_tolerances_in_separate_hunks():
    patch = diff('tests/test_demo.py',
                 'pytest.approx(x, abs=0.01)\nprepare()\npytest.approx(y, abs=1)',
                 'pytest.approx(x, abs=0.001)\nprepare()\npytest.approx(y, abs=0.1)')
    assert patch[0].count('@@ -') == 2
    assert not check_findings(patch)


def test_reordered_tolerance_keywords():
    patch = diff('tests/test_demo.py',
                 'assert x == pytest.approx(y, abs=.01, rel=1e-6)',
                 'assert x == pytest.approx(y, rel=1e-6, abs=.1)')
    assert [f.reason for f in check_findings(patch)] == ['widened abs tolerance']


def test_tightened_assertions_across_calls():
    patch = diff('tests/test_demo.py',
                 'assert x == pytest.approx(y, abs=.1)\nassert z == pytest.approx(w, abs=1)',
                 'assert x == pytest.approx(y, abs=.01)\nassert z == pytest.approx(w, abs=.1)')
    assert not check_findings(patch)


@pytest.mark.parametrize('path', ['README.md', 'src/demo.py'])
def test_skip_prose_and_non_test_files(path):
    assert not check_findings(diff(path, '', 'Use pytest.mark.skip to skip a test.'))


def test_moved_skip_marker():
    patch = diff('tests/test_demo.py',
                 '@pytest.mark.skip\ndef test_a():\n    pass\n\ndef test_b():\n    pass\n',
                 'def test_a():\n    pass\n\n@pytest.mark.skip\ndef test_b():\n    pass\n', full=True)
    assert not check_findings(patch)
    assert len(check_findings(diff('tests/test_demo.py', '@pytest.mark.skip',
                             '@pytest.mark.skip\n@pytest.mark.skip'))) == 1


def test_workflow_skip_marker():
    check_reason('.github/workflows/ci.yml', '', 'run: pytest.skip("reason")')


@pytest.mark.parametrize('opener', ['assert (', 'assert x == pytest.approx('])
def test_multiline_assert_continuation(opener):
    patch = diff('tests/test_demo.py', opener + '\n    expected\n)',
                 opener + '\n    other\n)')
    assert ' ' + opener not in patch[0]
    assert [f.reason for f in check_findings(patch)] == ['modified assertion']


def test_multiline_tolerance_keyword_reordering():
    patch = diff('tests/test_demo.py',
                 'assert x == pytest.approx(\n    y,\n    abs=.01,\n    rel=1e-6,\n)',
                 'assert x == pytest.approx(\n    y,\n    rel=1e-6,\n    abs=.1,\n)')
    assert [f.reason for f in check_findings(patch)] == ['widened abs tolerance']


@pytest.mark.parametrize('reason', ['', '—', '— <explain why this change is needed>',
                                    '— rationale <EXPLAIN more>'])
def test_empty_and_placeholder_reasons(reason):
    patch = diff('tests/test_demo.py', 'assert True', '')
    assert check_unexplained(patch, f'Test change reason: tests/test_demo.py {reason}')


def test_multiple_approx_calls_on_one_line():
    patch = diff('tests/test_demo.py',
                 'assert (pytest.approx(x, abs=.1), pytest.approx(y, abs=1))',
                 'assert (pytest.approx(x, abs=.01), pytest.approx(y, abs=.1))')
    assert not check_findings(patch)


def test_modified_assertion_with_another_removed():
    patch = diff('tests/test_demo.py', 'assert x == y\nassert a == b',
                 'assert x == z')
    assert [f.reason for f in check_findings(patch)] == ['modified assertion', 'removed assertion']


@pytest.mark.parametrize(('before', 'after', 'expected'), [
    (
        'def test_value():\n    assert actual == pytest.approx(\n        expected,\n        abs=.01,\n        rel=1e-6,\n    )\n',
        'def test_value():\n    assert actual == pytest.approx(\n        expected,\n        rel=1e-6,\n        abs=.1,\n    )\n',
        ['widened abs tolerance'],
    ),
    (
        'def test_value():\n    assert (\n        actual == expected\n    )\n',
        'def test_value():\n    assert (\n        True\n    )\n',
        ['modified assertion'],
    ),
    (
        'def test_first():\n    assert actual == expected\n    prepare()\n\ndef test_second():\n    prepare()\n',
        'def test_first():\n    prepare()\n\ndef test_second():\n    prepare()\n    assert actual == expected\n',
        [],
    ),
], ids=['reordered-widening', 'multiline-true', 'moved-assertion'])
def test_ci_zero_context_regressions(monkeypatch, capsys, before, after, expected):
    """Exercise the CLI with the zero-context patch and complete revision snapshots."""
    path = 'tests/test_demo.py'
    patch = ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                       fromfile=f'a/{path}', tofile=f'b/{path}', n=0))

    def git_output(command, **kwargs):
        if command[:2] == ['git', 'diff']:
            assert '--unified=0' in command
            return patch
        if command[:2] == ['git', 'merge-base']:
            return 'base-sha\n'
        if command[:2] == ['git', 'show']:
            assert command[2] in {f'base-sha:{path}', f'HEAD:{path}'}
            return before if command[2].startswith('base-sha:') else after
        raise AssertionError(command)

    monkeypatch.setattr(checker.subprocess, 'check_output', git_output)
    monkeypatch.setenv('GITHUB_BASE_REF', 'main')
    monkeypatch.delenv('GITHUB_EVENT_PATH', raising=False)
    assert checker.main() == bool(expected)
    output = capsys.readouterr().out
    if expected:
        for reason in expected:
            assert reason in output
        assert f'Test change reason: {path} — <explain why this change is needed>' in output
    else:
        assert 'Reward-hack check passed' in output


def test_equal_expected_values_do_not_hide_widening():
    patch = diff('tests/test_demo.py',
                 'assert a == pytest.approx(x, abs=.01)\nassert b == pytest.approx(x, abs=.1)',
                 'assert a == pytest.approx(x, abs=.1)\nassert b == pytest.approx(x, abs=.001)')
    assert [f.reason for f in check_findings(patch)] == ['widened abs tolerance']


def test_same_operands_in_one_assertion_keep_their_positions():
    patch = diff('tests/test_demo.py',
                 'assert (a, b) == (pytest.approx(x, abs=.01), pytest.approx(x, abs=.1))',
                 'assert (a, b) == (pytest.approx(x, abs=.1), pytest.approx(x, abs=.001))')
    assert [f.reason for f in check_findings(patch)] == ['widened abs tolerance']


@pytest.mark.parametrize(('before', 'after', 'expected'), [
    ('class TestValues:\n    def test_value(self):\n        self.assertAlmostEqual(x, y, 7)\n',
     'class TestValues:\n    def test_value(self):\n        self.assertAlmostEqual(x, y, places=3)\n',
     ['widened places tolerance']),
    ('class TestValues:\n    def test_value(self):\n        self.assertAlmostEqual(x, y, delta=.1)\n',
     'class TestValues:\n    def test_value(self):\n        self.assertAlmostEqual(x, y, delta=.01)\n', []),
    ('class TestValues:\n    def test_value(self):\n        assert value\n',
     '@pytest.mark.skip(reason="reason")\nclass TestValues:\n    def test_value(self):\n        assert value\n',
     ['added skip, xfail, todo, or only marker']),
    ('class TestA:\n    def test_value(self):\n        pass\n\nclass TestB:\n    def test_value(self):\n        pass\n',
     'class TestB:\n    def test_value(self):\n        pass\n', ['removed test']),
    ('class TestA:\n    def test_value(self):\n        assert value\n',
     'class TestB:\n    def test_value(self):\n        assert value\n', []),
    ('def test_a():\n    assert value\n\ndef test_b():\n    assert value\n',
     'def test_a():\n    pass\n\ndef test_b():\n    assert value\n', ['removed assertion']),
    ('async def test_value():\n    assert actual == expected\n',
     'async def test_value():\n    assert True\n', ['modified assertion']),
    ('def test_value():\n    assert first and second\n',
     'def test_value():\n    assert first or second\n', ['modified assertion']),
    ('def test_value():\n    assert value\n',
     'def test_value():\n    """Use pytest.mark.skip for temporary skips."""\n    assert value\n', []),
    ('def test_value():\n    assert value\n',
     'def test_value():\n    assert (\n', ['cannot parse Python test snapshot']),
])
def test_python_snapshot_structure(before, after, expected):
    patch = diff('tests/test_demo.py', before, after, full=True)
    assert [f.reason for f in check_findings(patch)] == expected


@pytest.mark.parametrize(('before', 'after', 'expected'), [
    ('pytest.approx(x, abs=.01)', 'pytest.approx(y, abs=.1)', ['modified assertion']),
    ('pytest.approx(x, abs=.01)', 'pytest.approx(x, abs=tolerance)', ['modified abs tolerance']),
    ('pytest.approx(x, abs=.01)', 'pytest.approx(x)', ['modified abs tolerance']),
    ('assert a == pytest.approx(x, abs=.01)\nassert b == pytest.approx(y, abs=.1)',
     'assert b == pytest.approx(y, abs=.01)\nassert a == pytest.approx(x, abs=.001)', []),
    ('assert check(a=pytest.approx(x, abs=.01), b=pytest.approx(y, abs=.1))',
     'assert check(b=pytest.approx(y, abs=.01), a=pytest.approx(x, abs=.1))',
     ['widened abs tolerance']),
])
def test_tolerance_call_identity(before, after, expected):
    patch = diff('tests/test_demo.py', before, after)
    assert [f.reason for f in check_findings(patch)] == expected


@pytest.mark.parametrize('deleted', [False, True])
def test_snapshot_loading_for_added_and_deleted_files(monkeypatch, deleted):
    path = 'tests/test_demo.py'
    source = 'def test_value():\n    assert True\n'
    before, after = (source, '') if deleted else ('', source)
    patch, _ = diff(path, before, after, full=True)
    patch = patch.replace(f'+++ b/{path}', '+++ /dev/null') if deleted else patch.replace(
        f'--- a/{path}', '--- /dev/null')
    commands = []

    def git_output(command, **kwargs):
        commands.append(command)
        if command[:2] == ['git', 'merge-base']:
            return 'base-sha\n'
        assert command == ['git', 'show', f'{"base-sha" if deleted else "HEAD"}:{path}']
        return source

    monkeypatch.setattr(checker.subprocess, 'check_output', git_output)
    result = checker.findings(patch)
    assert [f.reason for f in result] == (['removed test', 'removed assertion'] if deleted else [])
    assert len(commands) == 2


def test_assertion_removal_in_python_test_helper():
    patch = diff('tests/test_demo.py',
                 'def check_value():\n    assert value\n',
                 'def check_value():\n    pass\n', full=True)
    assert [f.reason for f in check_findings(patch)] == ['removed assertion']


def test_js_assertion_removal_and_move():
    assert [f.reason for f in check_findings(diff('mcp/tests/demo.test.ts', 'assert(value);', ''))] == [
        'removed assertion']
    assert not check_findings(diff('mcp/tests/demo.test.ts',
                                   'assert(value);\nprepare();', 'prepare();\nassert(value);'))
