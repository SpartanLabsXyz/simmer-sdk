"""Reviewer probes through the production CLIs; fixture code is never executed."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'tests/fixtures/gate_bypass'


def run(command, cwd, env):
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=30)


def write_files(root, files):
    for name, text in files.items():
        if name == 'SKILL.md' and not text.startswith('---\n'):
            text = '---\nname: probe\n---\n' + text
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


@pytest.mark.parametrize(('fixture', 'case'), [
    pytest.param(path, case, id='/'.join(path.parts[-3:-1]), marks=(
        pytest.mark.xfail(strict=True, raises=AssertionError, reason=case['known_hole'])
        if 'known_hole' in case else ()))
    for path in sorted(CORPUS.glob('*/*/case.json'))
    for case in [json.loads(path.read_text())]
])
def test_gate_bypass_corpus(fixture, case, tmp_path):
    gate = fixture.parents[1].name
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('GIT_', 'GITHUB_'))}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null')
    script = ROOT / 'scripts' / f'check_{gate}.py'
    command = [sys.executable, str(script)]
    if gate == 'reward_hack':
        def git(*args):
            result = run(['git', '-c', 'core.hooksPath=/dev/null',
                          '-c', 'commit.gpgsign=false', *args], tmp_path, env)
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()

        git('init', '-b', 'main')
        git('config', 'user.email', 'fixture@example.com')
        git('config', 'user.name', 'Corpus fixture')
        write_files(tmp_path, case['before'])
        git('add', '.')
        git('commit', '--allow-empty', '-m', 'fixture base')
        git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        for name in case['before']:
            (tmp_path / name).unlink()
        write_files(tmp_path, case['after'])
        git('add', '.')
        git('commit', '--allow-empty', '-m', 'fixture probe')
        event = tmp_path / 'event.json'
        event.write_text(json.dumps({'pull_request': {'body': case['body']}}))
        env.update(GITHUB_BASE_REF='main', GITHUB_EVENT_PATH=str(event))
    else:
        assert gate == 'skill_compliance'
        bundle = tmp_path / 'probe'
        write_files(bundle, {'main.py': '', 'requirements.txt': '',
                             'clawhub.json': '{}',
                             'SKILL.md': 'Network hosts: api.example.com\n'})
        if 'before_files' in case:
            write_files(bundle, case['before_files'])
            baseline = tmp_path / 'baseline.json'
            seeded = run([*command, str(bundle), '--write-baseline', str(baseline),
                          '--owner', 'fixture', '--reason', 'previously reviewed'], tmp_path, env)
            assert seeded.returncode == 0, seeded.stderr
            command.extend(['--baseline', str(baseline), '--check-baseline'])
        write_files(bundle, case['files'])
        for name, target in case.get('symlinks', {}).items():
            (bundle / name).symlink_to(target)
        command.append(str(bundle))
    result = run(command, tmp_path, env)
    diagnostic = f"{case['source']}\n{result.stdout}\n{result.stderr}"
    assert result.returncode == bool(case['expected']), diagnostic
    if gate == 'skill_compliance':
        messages = [finding['message'] for finding in json.loads(result.stdout)]
        if 'before_files' in case:
            known = {tuple(f[k] for k in ('check', 'file', 'line', 'message'))
                     for f in json.loads(baseline.read_text())}
            messages = [f['message'] for f in json.loads(result.stdout)
                        if tuple(f[k] for k in ('check', 'file', 'line', 'message')) not in known]
    else:
        messages = [line.split(': ', 1)[1] for line in result.stdout.splitlines()
                    if line.startswith(tuple(case['after']) + tuple(case['before']))]
    assert all(any(expected in message for message in messages)
               for expected in case['expected']), diagnostic
    assert all(any(expected in message for expected in case['expected'])
               for message in messages), diagnostic
