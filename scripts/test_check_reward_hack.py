"""Self-contained pattern and real git/event integration tests."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from check_reward_hack import findings, unexplained


def diff(path, removed='', added=''):
    return (f'diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n'
            '@@ -1,1 +1,1 @@\n' +
            ''.join('-' + line + '\n' for line in removed.splitlines()) +
            ''.join('+' + line + '\n' for line in added.splitlines()))


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
    assert unexplained(patch, '')
    assert not unexplained(patch, f'Test change reason: {path} — obsolete coverage')
    assert unexplained(patch, f'Test change reason: {path}')
    assert unexplained(patch, f'Test change reason: {path}.other — obsolete coverage')
    assert all(f.line > 0 and path in f.instruction for f in findings(patch))


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
    assert not findings('')
    assert not findings(diff('src/demo.py', 'value = 1', 'value = 2'))
    assert not findings(diff('tests/test_demo.py', 'def test_kept():', 'def test_kept():'))
    assert not findings(diff('mcp/tests/demo.test.ts', 'it("kept", () => {', 'it("kept", () => {'))
    assert not findings(diff('tests/test_demo.py', 'assert x == y', 'assert x == y'))


def test_real_git_and_event(tmp_path):
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
               for f in findings(diff('tests/test_demo.py', old, new)))
    assert not any(f.reason.startswith('widened')
                   for f in findings(diff('tests/test_demo.py', new, old)))


@pytest.mark.parametrize('marker', [
    'pytest.xfail("reason")',
    'pytestmark = pytest.mark.skip(reason="reason")',
    'pytestmark = [pytest.mark.xfail(reason="reason")]',
])
def test_runtime_and_module_markers(marker):
    check_reason('tests/test_demo.py', '', marker)


def test_multiline_approx_tolerance():
    patch = diff('tests/test_demo.py', '    abs=0.01,', '    abs=0.1,')
    assert any(f.reason == 'widened abs tolerance' for f in findings(patch))


def test_removed_duplicate_assertion():
    patch = diff('tests/test_demo.py', 'assert x == y\nassert x == y', 'assert x == y')
    assert sum(f.reason == 'removed assertion' for f in findings(patch)) == 1


def test_unrelated_approx_tolerances():
    patch = diff('tests/test_demo.py',
                 'pytest.approx(x, abs=0.01)\npytest.approx(y, abs=0.1)',
                 'pytest.approx(x, abs=0.001)\npytest.approx(y, abs=0.05)')
    assert not findings(patch)


def test_approx_tolerances_in_separate_hunks():
    patch = diff('tests/test_demo.py', 'pytest.approx(x, abs=0.01)',
                 'pytest.approx(x, abs=0.001)')
    patch += '@@ -10 +10 @@\n-pytest.approx(y, abs=1)\n+pytest.approx(y, abs=0.1)\n'
    assert not findings(patch)
