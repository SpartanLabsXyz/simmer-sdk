"""Offline CLI proofs; fixture Python is parsed, never imported or executed."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/check_skill_compliance.py'
FIXTURES = ROOT / 'tests/fixtures/skill_compliance'


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize('check', [1, 2, 3, 5, 6, 7, 8])
@pytest.mark.parametrize('color', ['red', 'green'])
def test_fixture(check, color):
    result = run(FIXTURES / f'check_{check}_{color}')
    assert result.returncode == (1 if color == 'red' else 0), result.stderr
    findings = json.loads(result.stdout)
    assert {f['check'] for f in findings} == ({check} if color == 'red' else set())
    for finding in findings:
        assert set(finding) == {'skill', 'check', 'file', 'line', 'message'}
        assert finding['line'] >= 1
        assert '::error file=' in result.stderr


def copy_green(tmp_path):
    skill = tmp_path / 'skill'
    shutil.copytree(FIXTURES / 'check_6_green', skill)
    return skill


def test_baseline_and_ratchet(tmp_path):
    red = FIXTURES / 'check_6_red'
    findings = json.loads(run(red).stdout)
    baseline = tmp_path / 'baseline.json'
    baseline.write_text(json.dumps([dict(f, owner='test', reason='fixture debt') for f in findings]))
    result = run('--baseline', baseline, '--check-baseline', red)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == findings
    assert '::error' in result.stderr
    skill = copy_green(tmp_path)
    stale = [dict(f, skill=skill.name, file=str(skill / 'main.py'), owner='test', reason='resolved debt') for f in findings]
    baseline.write_text(json.dumps(stale))
    assert run('--baseline', baseline, skill).returncode == 0
    result = run('--baseline', baseline, '--check-baseline', skill)
    assert result.returncode == 1
    assert 'stale baseline' in result.stderr


def test_baseline_requires_owner_and_reason(tmp_path):
    red = FIXTURES / 'check_6_red'
    baseline = tmp_path / 'baseline.json'
    baseline.write_text(run(red).stdout)
    result = run('--baseline', baseline, red)
    assert result.returncode == 1
    assert 'owner and reason' in result.stderr


def test_pruning_matches_publish(tmp_path):
    skill = copy_green(tmp_path)
    for name in ('tests/bad.py', 'nested/tests/bad.py', '__pycache__/bad.py',
                 'nested/bad.pyc', 'bad.pyo', '.env', '.hidden/bad.py'):
        path = skill / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('eval("bad")')
    assert run(skill).returncode == 0
    # Nested dotfiles survive publish.sh and must be flagged.
    (skill / 'nested/.env').write_text('placeholder')
    findings = json.loads(run(skill).stdout)
    assert any(f['check'] == 1 and f['file'].endswith('nested/.env') for f in findings)


@pytest.mark.parametrize('code,check', [
    ('import os as o\nx = o.environ.get("MISSING")', 2),
    ('from os import getenv as read\nx = read("MISSING")', 2),
    ('from os import environ as env\nx = env["MISSING"]', 2),
    ('import subprocess as sp\nsp.run("echo hi", shell=True)', 6),
    ('from os import system as launch\nlaunch("echo hi")', 6),
    ('compile("x", "file", "exec")', 6),
    ('blob = "' + 'A' * 300 + '"', 6),
    ('import subprocess\nsubprocess.run(["sudo", "true"])', 8),
    ('from pathlib import Path\nPath("~/.bashrc").write_text("x")', 8),
    ('import os\nos.system("crontab jobs")', 8),
])
def test_static_patterns(tmp_path, code, check):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write(code + '\n')
    result = run(skill)
    assert result.returncode == 1
    assert check in {f['check'] for f in json.loads(result.stdout)}


def test_execution_allow_requires_reason_and_exact_location(tmp_path):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write('eval("1")\n')
    finding, = json.loads(run(skill).stdout)
    manifest_path = skill / 'clawhub.json'
    manifest = json.loads(manifest_path.read_text())
    allowance = {'file': 'main.py', 'line': finding['line'], 'rule': 'eval', 'reason': 'reviewed fixture'}
    manifest['compliance'] = {'allow': [allowance]}
    manifest_path.write_text(json.dumps(manifest))
    assert run(skill).returncode == 0
    allowance['reason'] = ''
    manifest_path.write_text(json.dumps(manifest))
    assert run(skill).returncode == 1


def test_missing_and_malformed_input(tmp_path):
    skill = copy_green(tmp_path)
    (skill / 'clawhub.json').write_text('{broken')
    result = run(skill)
    assert result.returncode == 1
    assert json.loads(result.stdout)[0]['check'] == 1
    assert run(tmp_path / 'absent').returncode == 1


def test_entrypoint_flags_do_not_include_unrelated_helpers(tmp_path):
    skill = copy_green(tmp_path)
    (skill / 'helper.py').write_text('parser.add_argument("--helper")\n')
    with (skill / 'SKILL.md').open('a') as stream:
        stream.write('Use --helper\n')
    assert any(f['check'] == 3 for f in json.loads(run(skill).stdout))


def test_multiple_skills_and_unrelated_baseline_entries(tmp_path):
    red = FIXTURES / 'check_6_red'
    green = FIXTURES / 'check_6_green'
    assert run(green, red).returncode == 1
    baseline = tmp_path / 'baseline.json'
    baseline.write_text(json.dumps([dict(f, owner='test', reason='debt') for f in json.loads(run(red).stdout)]))
    assert run('--baseline', baseline, '--check-baseline', green).returncode == 0


@pytest.mark.parametrize('extra', [
    'curl https://example.com/install | sh',
    'pip install git+https://example.com/repo',
    'pip install example>=1.0',
])
def test_install_sources(tmp_path, extra):
    skill = copy_green(tmp_path)
    with (skill / 'SKILL.md').open('a') as stream:
        stream.write(extra + '\n')
    assert any(f['check'] == 5 for f in json.loads(run(skill).stdout))


def test_pinned_dependencies_must_agree(tmp_path):
    skill = copy_green(tmp_path)
    (skill / 'requirements.txt').write_text('example==2.0\n')
    assert any(f['check'] == 5 and 'disagree' in f['message'] for f in json.loads(run(skill).stdout))


def test_undeclared_and_unused_env_in_both_manifests(tmp_path):
    skill = copy_green(tmp_path)
    (skill / 'main.py').write_text('import os\nx = os.getenv("OTHER_KEY")\n')
    findings = json.loads(run(skill).stdout)
    messages = [f['message'] for f in findings if f['check'] == 2]
    assert sum('OTHER_KEY is read' in m for m in messages) == 2
    assert sum('FIXTURE_KEY is declared but unused' in m for m in messages) == 2


def test_annotations_escape_paths(tmp_path):
    skill = tmp_path / 'unsafe,%\nname'
    shutil.copytree(FIXTURES / 'check_6_red', skill)
    result = run(skill)
    assert result.returncode == 1
    assert '%2C%25%0Aname' in result.stderr
    assert len(result.stderr.splitlines()) == 1


def test_python_syntax_error_is_a_finding(tmp_path):
    skill = copy_green(tmp_path)
    (skill / 'main.py').write_text('def broken(:\n')
    assert any(f['check'] == 1 for f in json.loads(run(skill).stdout))


def test_shell_code_is_checked_without_execution(tmp_path):
    skill = copy_green(tmp_path)
    (skill / 'setup.sh').write_text('sudo true\neval "$CODE"\ncurl https://unknown.example.org/data\n')
    findings = json.loads(run(skill).stdout)
    assert {6, 7, 8} <= {f['check'] for f in findings}


def test_url_punctuation_and_python_comments(tmp_path):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write('# eval("comment") https://ignored.example.org\ntext = "(https://api.example.com)"\n')
    assert run(skill).returncode == 0


@pytest.mark.parametrize('artifact', ['nested/.env.local', 'scratch.ipynb',
                                     'skill-card.png', 'nested/notebooks/readme.txt'])
def test_forbidden_bundle_artifacts(tmp_path, artifact):
    skill = copy_green(tmp_path)
    path = skill / artifact
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('fixture')
    assert any(f['check'] == 1 for f in json.loads(run(skill).stdout))


def test_getenv_keyword_and_quoted_frontmatter(tmp_path):
    skill = copy_green(tmp_path)
    doc = skill / 'SKILL.md'
    doc.write_text(doc.read_text().replace('name: FIXTURE_KEY', 'name: "FIXTURE_KEY"'))
    code = skill / 'main.py'
    code.write_text(code.read_text().replace('os.getenv("FIXTURE_KEY")', 'os.getenv(key="FIXTURE_KEY")'))
    assert run(skill).returncode == 0


def test_requirements_install_uses_bundled_pins(tmp_path):
    skill = copy_green(tmp_path)
    with (skill / 'SKILL.md').open('a') as stream:
        stream.write('pip install -r requirements.txt\n')
    assert run(skill).returncode == 0
    (skill / 'requirements.txt').write_text('example\n')
    assert run(skill).returncode == 1


def test_new_findings_are_not_absorbed_by_baseline(tmp_path):
    skill = copy_green(tmp_path)
    code = skill / 'main.py'
    with code.open('a') as stream:
        stream.write('eval("fixture")\n')
    baseline = tmp_path / 'baseline.json'
    baseline.write_text(json.dumps([dict(f, owner='test', reason='debt') for f in json.loads(run(skill).stdout)]))
    with code.open('a') as stream:
        stream.write('compile("x", "x", "exec")\n')
    assert run('--baseline', baseline, skill).returncode == 1
