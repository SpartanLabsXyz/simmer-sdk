import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def fixture_repo(root):
    (root / 'mcp').mkdir()
    (root / 'mcp/package.json').write_text('{"version":"1.0.0"}')
    (root / 'pyproject.toml').write_text('[project]\nversion="1.0.0"\n')
    (root / 'scripts').mkdir()
    (root / 'scripts/skill-compliance-baseline.json').write_text('[]')
    for name in ('clean', 'known', 'new-finding'):
        skill = root / 'skills' / name
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text(
            f'---\nname: {name}\nmetadata:\n  version: 1.1.0\n---\n'
        )
        (skill / 'clawhub.json').write_text('{}')
        if name != 'clean':
            (skill / 'entry.py').write_text('eval("1")\n')
    versions = root / 'versions.json'
    versions.write_text(json.dumps(dict.fromkeys(('clean', 'known', 'new-finding'), '1.0.0')))
    return versions


def run_plan(root, versions):
    return subprocess.run([
        sys.executable, str(ROOT / 'scripts/plan_package_publish.py'), '--root', str(root),
        '--npm-published-version', '1.0.0', '--pypi-published-version', '1.0.0',
        '--clawhub-versions-json', str(versions),
    ], cwd=root, capture_output=True, text=True, timeout=15, env={'PATH': '/usr/bin:/bin'})


def test_plan_filters_new_findings_and_preserves_baselined_skill(tmp_path):
    versions = fixture_repo(tmp_path)
    baseline = subprocess.run([
        sys.executable, str(ROOT / 'scripts/check_skill_compliance.py'), 'skills/known',
        '--write-baseline', 'scripts/skill-compliance-baseline.json',
        '--owner', 'fixture', '--reason', 'existing finding',
    ], cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert baseline.returncode == 0, baseline.stderr
    result = run_plan(tmp_path, versions)
    assert result.returncode == 0, result.stdout + result.stderr
    outputs = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    assert json.loads(outputs['skills_to_publish']) == ['skills/clean', 'skills/known']
    assert outputs['skills_publish_needed'] == 'true'
    assert '::warning::skills/new-finding' in result.stdout
    assert 'forbidden execution: eval' in result.stderr


@pytest.mark.parametrize('baseline', [None, '{', '{}'])
def test_invalid_baseline_never_allows_publish(tmp_path, baseline):
    versions = fixture_repo(tmp_path)
    path = tmp_path / 'scripts/skill-compliance-baseline.json'
    if baseline is None:
        path.unlink()
    else:
        path.write_text(baseline)
    result = run_plan(tmp_path, versions)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'skills_to_publish=[]' in result.stdout
    assert 'skills_publish_needed=false' in result.stdout
    assert 'invalid baseline' in result.stderr
