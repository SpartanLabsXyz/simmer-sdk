import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def inspection(status='clean', version='1.1.0', latest='1.1.0'):
    return {
        'skill': {'slug': 'example', 'tags': {'latest': latest}},
        'owner': {'handle': 'simmer'},
        'latestVersion': {'version': latest} if latest else None,
        'version': {'version': version, 'security': {'status': status, 'hasWarnings': False}},
        'moderation': {'isSuspicious': False},
    }


@pytest.fixture
def runner(tmp_path):
    skill = tmp_path / 'skills/example'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: example\nmetadata:\n  version: 1.1.0\n---\n')
    (skill / 'clawhub.json').write_text('{}')
    binary = tmp_path / 'bin'
    binary.mkdir()
    stub = f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
with open(os.environ['CALLS'], 'a') as log:
    log.write(json.dumps([Path(sys.argv[0]).name, *sys.argv[1:]]) + '\\n')
if Path(sys.argv[0]).name == 'npx':
    print(os.environ['INSPECT_RESULT'])
    sys.exit(int(os.environ.get('INSPECT_EXIT', '0')))
if sys.argv[1:3] == ['issue', 'list']:
    print(os.environ.get('ISSUES', '[]'))
    sys.exit(int(os.environ.get('ISSUE_LIST_EXIT', '0')))
if sys.argv[1:3] == ['label', 'list']:
    print(os.environ.get('LABELS', '[{{"name":"needs-human"}}]'))
    sys.exit(0)
if sys.argv[1:3] == ['label', 'create']:
    sys.exit(int(os.environ.get('LABEL_CREATE_EXIT', '0')))
if sys.argv[1:3] == ['issue', 'create']:
    assert '--label' in sys.argv and 'needs-human' in sys.argv
    body = Path(sys.argv[sys.argv.index('--body-file') + 1]).read_text()
    Path(os.environ['ISSUE_BODY']).write_text(body)
    print('https://example.invalid/issues/1')
    sys.exit(int(os.environ.get('ISSUE_CREATE_EXIT', '0')))
sys.exit(99)
'''
    for name in ('npx', 'gh'):
        path = binary / name
        path.write_text(stub)
        path.chmod(0o755)
    calls = tmp_path / 'calls.jsonl'
    body = tmp_path / 'issue-body'

    def run(payload, **overrides):
        calls.write_text('')
        args = []
        if 'RECEIPT' in overrides:
            directory = tmp_path / 'receipts'
            content = overrides.pop('RECEIPT')
            if content != 'missing':
                directory.mkdir(exist_ok=True)
                (directory / 'example-1.1.0.json').write_text(content)
            args += ['--receipt-dir', str(directory)]
        if overrides.pop('RECHECK', None):
            args += ['--recheck']
        env = {
            'PATH': f'{binary}{os.pathsep}{os.defpath}', 'HOME': str(tmp_path),
            'INSPECT_RESULT': json.dumps(payload), 'CALLS': str(calls),
            'ISSUE_BODY': str(body), 'GITHUB_REPOSITORY': 'fixture/repo',
            'GH_TOKEN': 'fixture', 'SKILLS_TO_PUBLISH': '["skills/example"]',
            **overrides,
        }
        result = subprocess.run([
            sys.executable, str(ROOT / 'scripts/verify_skill_publish.py'), '--root', str(tmp_path),
            *args,
        ], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=15)
        return result, [json.loads(line) for line in calls.read_text().splitlines()], body
    return run


@pytest.mark.parametrize('status,latest,code,clip', [
    ('pending', '1.0.0', 0, '::notice::example@1.1.0: Security PENDING; not-yet'),
    ('pending', None, 0, '::notice::example@1.1.0: Security PENDING; not-yet'),
    ('clean', '1.1.0', 0, 'Security CLEAN; ok'),
    ('suspicious', '1.1.0', 1, 'Security SUSPICIOUS; needs-human'),
    ('clean', '1.0.0', 1, 'drift'),
    ('malicious', '1.1.0', 1, 'Security MALICIOUS'),
    ('error', '1.1.0', 1, 'Security ERROR'),
])
def test_security_states(runner, status, latest, code, clip):
    result, calls, body = runner(inspection(status, latest=latest))
    assert result.returncode == code, result.stdout + result.stderr
    assert clip in result.stdout
    assert calls[0] == ['npx', '-y', 'clawhub@0.23.3', 'inspect', '@simmer/example', '--version', '1.1.0', '--json']
    if status == 'suspicious':
        assert [call[1:3] for call in calls[1:]] == [
            ['issue', 'list'], ['label', 'list'], ['issue', 'create'],
        ]
        assert '<!-- clawhub-security:example@1.1.0 -->' in body.read_text()
    else:
        assert len(calls) == 1


@pytest.mark.parametrize('payload', [None, [], {}, {'version': None},
    inspection('unknown'), inspection(version='1.0.0'),
    {**inspection(), 'version': {'version': '1.1.0'}},
    {**inspection(), 'owner': {'handle': 'someone-else'}},
    {**inspection(), 'skill': {'slug': 'other'}},
    {**inspection(), 'latestVersion': {'version': 'garbage'}},
])
def test_unreadable_result_fails_closed(runner, payload):
    result, calls, _ = runner(payload)
    assert result.returncode == 1
    assert '::error::' in result.stdout
    assert len(calls) == 1


@pytest.mark.parametrize('overrides', [
    {'INSPECT_EXIT': '1'}, {'INSPECT_RESULT': 'not json'}, {'INSPECT_RESULT': ''},
])
def test_cli_failure_or_unparseable_json_fails_closed(runner, overrides):
    result, calls, _ = runner(inspection(), **overrides)
    assert result.returncode == 1
    assert '::error::' in result.stdout
    assert len(calls) == 1


def test_suspicious_dedupes_open_issue_by_exact_marker(runner):
    marker = '<!-- clawhub-security:example@1.1.0 -->'
    result, calls, body = runner(inspection('suspicious'), ISSUES=json.dumps([{'body': marker}]))
    assert result.returncode == 1
    assert [call[2] for call in calls[1:]] == ['list']
    assert '--state' in calls[1] and 'open' in calls[1]
    assert '--search' in calls[1] and '"clawhub-security:example@1.1.0" in:body' in calls[1]
    assert not body.exists()


@pytest.mark.parametrize('overrides', [
    {'ISSUE_LIST_EXIT': '1'}, {'ISSUES': 'not json'}, {'ISSUES': '{}'},
    {'ISSUE_CREATE_EXIT': '1'},
])
def test_issue_failure_is_reported(runner, overrides):
    result, _, _ = runner(inspection('suspicious'), **overrides)
    assert result.returncode == 1
    assert '::error::' in result.stdout
    assert 'needs-human issue' in result.stdout


def test_empty_plan_fails_closed_with_missing_receipt_directory(runner):
    result, calls, _ = runner(inspection(), SKILLS_TO_PUBLISH='[]', RECEIPT='missing')
    assert result.returncode == 1, result.stdout + result.stderr
    assert '::error::Cannot read skill publish plan:' in result.stdout
    assert not calls


def test_suspicious_with_invalid_latest_still_opens_issue(runner):
    result, calls, body = runner(inspection('suspicious', latest='invalid'))
    assert result.returncode == 1
    assert 'needs-human' in result.stdout
    assert body.exists()


def test_missing_label_is_created_before_issue(runner):
    result, calls, body = runner(inspection('suspicious'), LABELS='[]')
    assert result.returncode == 1
    assert [call[1:3] for call in calls[1:]] == [
        ['issue', 'list'], ['label', 'list'], ['label', 'create'], ['issue', 'create'],
    ]
    assert body.exists()


@pytest.mark.parametrize('overrides', [
    {'LABELS': 'not json'}, {'LABELS': '{}'}, {'LABELS': '[]', 'LABEL_CREATE_EXIT': '1'},
])
def test_label_failure_is_reported(runner, overrides):
    result, calls, body = runner(inspection('suspicious'), **overrides)
    assert result.returncode == 1
    assert '::error::' in result.stdout
    assert 'needs-human issue' in result.stdout
    assert not body.exists()


def receipt(status='pending-publication', publication='pending'):
    return {'ok': True, 'slug': 'example', 'version': '1.1.0',
            'status': status, 'publicationStatus': publication}


@pytest.mark.parametrize('receipt_status', ['pending-publication', 'already-submitted'])
@pytest.mark.parametrize('status,latest,code,outcome', [
    ('pending', '1.0.0', 0, 'not-yet'),
    ('pending', None, 0, 'not-yet'),
    ('clean', '1.1.0', 0, 'ok'),
    ('clean', '1.0.0', 1, 'drift'),
    ('suspicious', '1.1.0', 1, 'needs-human'),
    ('malicious', '1.1.0', 1, 'failure'),
    ('error', '1.1.0', 1, 'failure'),
])
def test_pending_receipt_checks_current_security_without_recheck(runner, status, latest, code, outcome, receipt_status):
    result, calls, body = runner(inspection(status, latest=latest), RECEIPT=json.dumps(receipt(receipt_status)))
    assert result.returncode == code, result.stdout + result.stderr
    assert f'Security {status.upper()}; {outcome}' in result.stdout
    assert calls[0] == ['npx', '-y', 'clawhub@0.23.3', 'inspect', '@simmer/example', '--version', '1.1.0', '--json']
    if status == 'suspicious':
        assert '<!-- clawhub-security:example@1.1.0 -->' in body.read_text()
        report = body.parent / 'audits/example-1.1.0.json'
        assert json.loads(report.read_text()) == inspection(status, latest=latest)
    else:
        assert not body.exists()


@pytest.mark.parametrize('receipt_status', ['pending-publication', 'already-submitted'])
def test_pending_receipt_does_not_mask_inspection_failure(runner, receipt_status):
    result, _, _ = runner(inspection(), RECEIPT=json.dumps(receipt(receipt_status)), INSPECT_EXIT='1')
    assert result.returncode == 1
    assert '::error::' in result.stdout


def test_published_receipt_still_checks_security(runner):
    result, calls, body = runner(inspection('suspicious'), RECEIPT=json.dumps(
        receipt('published', 'published')))
    assert result.returncode == 1
    assert body.exists()
    assert calls[0][0] == 'npx'


@pytest.mark.parametrize('value', ['{', '{}', 'null',
    json.dumps({**receipt(), 'version': '1.0.0'}),
    json.dumps({**receipt(), 'publicationStatus': 'published'}),
    json.dumps({**receipt('already-submitted'), 'slug': 'other'}),
    json.dumps({**receipt('already-submitted'), 'version': '1.0.0'}),
    json.dumps({**receipt('already-submitted'), 'publicationStatus': 'published'}),
])
def test_bad_receipt_fails_closed_before_inspect(runner, value):
    result, calls, _ = runner(inspection(), RECEIPT=value)
    assert result.returncode == 1
    assert '::error::' in result.stdout
    assert calls == []


def test_missing_receipt_fails_closed(runner):
    result, calls, _ = runner(inspection(), RECEIPT='missing')
    assert result.returncode == 1
    assert '::error::' in result.stdout
    assert calls == []


def test_recheck_ignores_submission_receipt_and_reads_current_security(runner):
    result, calls, body = runner(inspection('suspicious'), RECEIPT=json.dumps(receipt()), RECHECK='1')
    assert result.returncode == 1
    assert calls[0][0] == 'npx'
    assert body.exists()
