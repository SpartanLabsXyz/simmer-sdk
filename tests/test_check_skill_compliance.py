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



def test_manifest_dependency_identity_ratchets(tmp_path):
    skill = copy_green(tmp_path)
    path = skill / 'clawhub.json'
    manifest = json.loads(path.read_text())
    manifest['requires']['pip'].append('old-unpinned-package')
    path.write_text(json.dumps(manifest))
    baseline = tmp_path / 'baseline.json'
    baseline.write_text(json.dumps([dict(f, owner='test', reason='debt')
                                    for f in json.loads(run(skill).stdout)]))
    manifest['requires']['pip'].append('new-unpinned-package')
    path.write_text(json.dumps(manifest))
    result = run('--baseline', baseline, skill)
    assert result.returncode == 1
    assert any('new-unpinned-package' in f['message'] for f in json.loads(result.stdout))


@pytest.mark.parametrize('kwargs', ['**{"shell": True}', '**options', '**{"shell": enabled}', '**{**options}'])
def test_subprocess_expanded_kwargs(tmp_path, kwargs):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write('import subprocess\nsubprocess.run("echo review", ' + kwargs + ')\n')
    result = run(skill)
    assert result.returncode == 1
    assert any(f['check'] == 6 for f in json.loads(result.stdout))


@pytest.mark.parametrize('code', [
    'from pathlib import Path; Path("/tmp/undeclared-review-state.json").write_text("{}")',
    'open("/tmp/undeclared-review-state.json", "w").write("{}")',
])
def test_absolute_state_write_needs_review(tmp_path, code):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write(code + '\n')
    result = run(skill)
    assert result.returncode == 1
    assert any(f['check'] == 8 and 'write' in f['message'] for f in json.loads(result.stdout))


@pytest.mark.parametrize('recipe', ['Run `sudo crontab jobs` to persist.',
                                   '```sh\nsystemctl enable service\n```',
                                   '```\nlaunchctl load jobs\n```'])
def test_documented_privilege_recipe(tmp_path, recipe):
    skill = copy_green(tmp_path)
    with (skill / 'SKILL.md').open('a') as stream:
        stream.write(recipe + '\n')
    result = run(skill)
    assert result.returncode == 1
    assert any(f['check'] == 8 for f in json.loads(result.stdout))


@pytest.mark.parametrize('code', ['env = os.environ; x = env["MISSING_KEY"]',
                                 'read = os.getenv; x = read("MISSING_KEY")',
                                 'env = os.environ; x = env.get("MISSING_KEY")'])
def test_assignment_environment_alias(tmp_path, code):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write('import os\n' + code + '\n')
    result = run(skill)
    assert result.returncode == 1
    assert any(f['check'] == 2 and 'MISSING_KEY is read' in f['message']
               for f in json.loads(result.stdout))


@pytest.mark.parametrize('location', ['SKILL.md', 'clawhub.json'])
def test_environment_declaration_requires_purpose(tmp_path, location):
    skill = copy_green(tmp_path)
    path = skill / location
    if location == 'SKILL.md':
        path.write_text(path.read_text().replace('      purpose: Fixture authentication\n', ''))
    else:
        manifest = json.loads(path.read_text())
        manifest['envVars'][0].pop('purpose', None)
        path.write_text(json.dumps(manifest))
    result = run(skill)
    assert result.returncode == 1
    assert any(f['check'] == 2 and f['file'].endswith(location)
               and 'FIXTURE_KEY' in f['message'] and 'purpose' in f['message']
               for f in json.loads(result.stdout))


@pytest.mark.parametrize('code', [
    'import http.client; http.client.HTTPSConnection("undeclared.example.org")',
    'from http.client import HTTPConnection as Connect; Connect(host="undeclared.example.org")',
    'import socket; socket.create_connection(("undeclared.example.org", 443))',
    'import socket; socket.socket().connect(("undeclared.example.org", 443))',
])
def test_literal_connection_host(tmp_path, code):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write(code + '\n')
    result = run(skill)
    assert result.returncode == 1
    assert any(f['check'] == 7 and 'undeclared.example.org' in f['message']
               for f in json.loads(result.stdout))


@pytest.mark.parametrize('recipe', ['pip -q install unpinned-package',
                                   'pip --timeout 30 install unpinned-package',
                                   'curl https://example.com/install | /bin/sh',
                                   'curl https://example.com/install | /usr/bin/env bash',
                                   'curl https://example.com/install | /usr/bin/env /bin/bash'])
def test_install_command_variants(tmp_path, recipe):
    skill = copy_green(tmp_path)
    with (skill / 'SKILL.md').open('a') as stream:
        stream.write(recipe + '\n')
    result = run(skill)
    assert result.returncode == 1
    assert any(f['check'] == 5 for f in json.loads(result.stdout))


def test_bundle_symlink_is_not_dereferenced(tmp_path):
    skill = copy_green(tmp_path)
    (skill / 'linked.py').symlink_to('main.py')
    result = run(skill)
    assert result.returncode == 1
    assert any(f['check'] == 1 and 'symlink' in f['message'] for f in json.loads(result.stdout))



def test_write_baseline_contains_only_current_findings(tmp_path):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write('eval("1")\n')
    baseline = tmp_path / 'baseline.json'
    baseline.write_text('[{"stale": true}]')
    result = run('--write-baseline', baseline, '--owner', 'test', '--reason', 'review debt', skill)
    assert result.returncode == 0, result.stderr
    assert json.loads(baseline.read_text()) == [dict(f, owner='test', reason='review debt')
                                               for f in json.loads(result.stdout)]
    assert run('--baseline', baseline, '--check-baseline', skill).returncode == 0
    assert run('--write-baseline', baseline, skill).returncode == 2


def test_review_patterns_allow_known_safe_literals(tmp_path):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write('import subprocess, http.client\n'
                     'subprocess.run(["echo", "review"], **{"shell": False})\n'
                     'http.client.HTTPSConnection("api.example.com")\n'
                     'from pathlib import Path\n'
                     'Path("/tmp/read-only.json").open("r")\n'
                     'Path("state.json").write_text("{}")\n')
    doc = skill / 'SKILL.md'
    doc.write_text(doc.read_text() + 'pip -q install example==1.2.3\n')
    assert run(skill).returncode == 0


@pytest.mark.parametrize('code', [
    'def first():\n    env = os.environ\n    return env.get("FIXTURE_KEY")\n'
    'def second():\n    env = {"LOCAL": "value"}\n    return env.get("LOCAL")\n',
    'env = os.environ\ndef second():\n    env = {}\n    return env.get("LOCAL")\n',
    'env = os.environ\nenv = {}\nx = env.get("LOCAL")\n',
    'env = os.environ\ndef second(env):\n    return env.get("LOCAL")\n',
])
def test_environment_alias_scope_and_rebinding(tmp_path, code):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write('import os\n' + code)
    assert run(skill).returncode == 0


@pytest.mark.parametrize('code,check', [
    ('import subprocess\nrun = subprocess.run\nrun("echo hi", shell=True)', 6),
    ('run = eval\nrun("1")', 6),
    ('import os\nread = os.environ.get\nread("MISSING_KEY")', 2),
    ('import os\ndef first():\n    read = os.environ.get\n    read("MISSING_KEY")', 2),
])
def test_callable_assignment_alias(tmp_path, code, check):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write(code + '\n')
    assert any(f['check'] == check for f in json.loads(run(skill).stdout))


@pytest.mark.parametrize('prohibition', ["Don't", 'Do not', 'Never run', 'Avoid', 'No need to'])
def test_documented_privilege_prohibition(tmp_path, prohibition):
    skill = copy_green(tmp_path)
    doc = skill / 'SKILL.md'
    doc.write_text(doc.read_text() + f'{prohibition} `sudo npm install`.\n')
    assert run(skill).returncode == 0
    doc.write_text(doc.read_text() + 'Never run `sudo bad`. Run `sudo good`.\n')
    findings = json.loads(run(skill).stdout)
    assert sum(f['check'] == 8 for f in findings) == 1


@pytest.mark.parametrize('host', ['api.example.com:443', 'API.EXAMPLE.COM:443',
                                 '127.0.0.1', '127.0.0.1:443', '[::1]:443'])
def test_normalized_connection_hosts(tmp_path, host):
    skill = copy_green(tmp_path)
    doc = skill / 'SKILL.md'
    doc.write_text(doc.read_text() + '\nLocal connections: 127.0.0.1 and http://[::1]:443.\n')
    with (skill / 'main.py').open('a') as stream:
        stream.write(f'import http.client\nhttp.client.HTTPSConnection({host!r})\n'
                     'import socket\nsocket.create_connection(("127.0.0.1", 443))\n')
    assert run(skill).returncode == 0


def test_documented_install_identity_ratchets(tmp_path):
    skill = copy_green(tmp_path)
    doc = skill / 'SKILL.md'
    original = doc.read_text()
    doc.write_text(original + 'pip install simmer-sdk\n')
    baseline = tmp_path / 'baseline.json'
    baseline.write_text(json.dumps([dict(f, owner='test', reason='debt')
                                    for f in json.loads(run(skill).stdout)]))
    doc.write_text(original + 'pip install simmer-sdk new-package\n')
    result = run('--baseline', baseline, skill)
    assert result.returncode == 1
    assert any('new-package' in f['message'] for f in json.loads(result.stdout))


@pytest.mark.parametrize('code', [
    'open("../outside-state.json", "w")',
    'import io; io.open("../outside-state.json", "w")',
    'import io; io.open("/tmp/outside-state.json", "w")',
    'from pathlib import Path; Path("../outside-state.json").write_text("{}")',
])
def test_escaping_state_write(tmp_path, code):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write(code + '\n')
    assert any(f['check'] == 8 and 'write' in f['message']
               for f in json.loads(run(skill).stdout))


def test_nested_relative_write_stays_in_skill(tmp_path):
    skill = copy_green(tmp_path)
    (skill / 'nested').mkdir()
    (skill / 'nested/helper.py').write_text('import io\nio.open("../state.json", "w")\n')
    # Python resolves relative writes from CWD; from the skill root this escapes.
    assert any(f['check'] == 8 and 'escaping write' in f['message']
               for f in json.loads(run(skill).stdout))


def test_nested_helper_write_escapes_skill_cwd(tmp_path):
    skill = copy_green(tmp_path)
    (skill / 'nested').mkdir()
    (skill / 'nested/helper.py').write_text('import io\nio.open("../outside-state.json", "w")\n')
    assert any(f['check'] == 8 and '../outside-state.json' in f['message']
               for f in json.loads(run(skill).stdout))


@pytest.mark.parametrize('expression', [
    'open(Path("/tmp/state.json"), "w")',
    'open(pathlib.Path("../state.json"), "w")',
    'io.open(Path("/tmp") / "state.json", "w")',
    'open(Path("..") / "state.json", "w")',
    '(Path("/tmp") / "state.json").write_text("{}")',
    '(Path("..") / "state.json").open("w")',
    'Path("/tmp/state.json").open("w")',
    'open(file=Path("/tmp", "state.json"), mode="w")',
])
def test_literal_path_write(tmp_path, expression):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write('import io\nimport pathlib\nfrom pathlib import Path\n' + expression + '\n')
    assert any(f['check'] == 8 and 'write destination' in f['message']
               for f in json.loads(run(skill).stdout))


def test_pinned_dependency_disagreement_identity_ratchets(tmp_path):
    skill = copy_green(tmp_path)
    path = skill / 'clawhub.json'
    manifest = json.loads(path.read_text())
    manifest['requires']['pip'] = ['example==2.0']
    path.write_text(json.dumps(manifest))
    baseline = tmp_path / 'baseline.json'
    assert run('--write-baseline', baseline, '--owner', 'test', '--reason', 'debt', skill).returncode == 0
    assert run('--baseline', baseline, '--check-baseline', skill).returncode == 0
    manifest['requires']['pip'].append('new-package==1.0')
    path.write_text(json.dumps(manifest))
    result = run('--baseline', baseline, '--check-baseline', skill)
    assert result.returncode == 1
    known = json.loads(baseline.read_text())
    assert any(f['check'] == 5 and 'new-package' in f['message'] and '1.0' in f['message']
               and f['message'] not in {old['message'] for old in known}
               for f in json.loads(result.stdout))


@pytest.mark.parametrize('code', [
    'ns = {"atom": "http://www.w3.org/2005/Atom"}\n'
    'for entry in root.findall(".//atom:entry", ns):\n'
    '    title = entry.findtext("atom:title", "", ns)\n'
    '    link = entry.find("atom:link", ns)\n',
    'ns = {"custom": "https://namespace.example.org/schema"}\nroot.findall("custom:entry", ns)',
    'tag = "{https://namespace.example.org/schema}entry"',
    'root.iterfind("{https://namespace.example.org/schema}entry")',
    'ET.register_namespace("custom", "https://namespace.example.org/schema")',
    'namespace = "http://purl.org/dc/elements/1.1/"',
    'namespace = "http://schemas.xmlsoap.org/soap/envelope/"',
])
def test_xml_namespace_is_not_outbound_host(tmp_path, code):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write('import xml.etree.ElementTree as ET\n' + code + '\n')
    result = run(skill)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('code', [
    'requests.get("http://www.w3.org/2005/Atom")',
    'urlopen("http://purl.org/dc/elements/1.1/")',
    'requests.get("http://schemas.xmlsoap.org/soap/envelope/")',
    'requests.post("https://other.example.org", json={"ns": "http://www.w3.org/2005/Atom"})',
])
def test_namespace_host_network_call_is_reported(tmp_path, code):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write(code + '\n')
    assert any(f['check'] == 7 and any(host in f['message'] for host in
               ('www.w3.org', 'purl.org', 'schemas.xmlsoap.org'))
               for f in json.loads(run(skill).stdout))


@pytest.mark.parametrize('option', ['-rextra.txt', '-r extra.txt',
                                    '--requirement extra.txt', '--requirement=extra.txt'])
@pytest.mark.parametrize('executable', ['pip', 'pip3.12'])
def test_documented_requirement_manifests(tmp_path, option, executable):
    skill = copy_green(tmp_path)
    doc = skill / 'SKILL.md'
    doc.write_text(doc.read_text() + f'{executable} install {option} example==1.2.3\n')
    with (skill / 'main.py').open('a') as stream:
        stream.write('p.add_argument("--requirement")\n')
    requirements = skill / 'extra.txt'
    requirements.write_text('new-unpinned-package\n')
    findings = json.loads(run(skill).stdout)
    assert any(f['check'] == 5 and f['file'].endswith('extra.txt')
               and 'new-unpinned-package' in f['message'] for f in findings)
    requirements.write_text('new-package==1.0\n')
    assert run(skill).returncode == 0
    requirements.unlink()
    assert any(f['check'] == 5 for f in json.loads(run(skill).stdout))


@pytest.mark.parametrize('method', ['Popen', 'run', 'call', 'check_call', 'check_output'])
@pytest.mark.parametrize('shell', ['True', 'enabled', 'False'])
def test_subprocess_positional_shell(tmp_path, method, shell):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write(f'import subprocess\nsubprocess.{method}("echo hi", -1, None, None, None, None, None, True, {shell})\n')
    result = run(skill)
    assert result.returncode == (0 if shell == 'False' else 1)
    assert any(f['check'] == 6 for f in json.loads(result.stdout)) == (shell != 'False')


def test_help_states_literal_audit_scope():
    result = run('--help')
    assert result.returncode == 0
    for phrase in ('common patterns', 'not a sandbox', 'dynamic attribute',
                   'getattr', 'built strings', 'human review', 'publish PRs'):
        assert phrase in result.stdout


def test_environment_item_assignment_preserves_module_alias(tmp_path):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write('os.environ["FIXTURE_KEY"] = "value"\n'
                     'os.environ.get("MISSING_KEY")\n')
    findings = json.loads(run(skill).stdout)
    assert sum(f['check'] == 2 and 'MISSING_KEY is read' in f['message']
               for f in findings) == 2


@pytest.mark.parametrize('fixture,check,messages', [
    ('namespace_network_red', 7, ['undeclared network host: www.w3.org']),
    ('qualified_open_red', 8, ['absolute write destination', 'escaping write destination']),
    ('qualified_open_green', None, []),
])
def test_namespace_and_qualified_open_fixtures(fixture, check, messages):
    result = run(FIXTURES / fixture)
    assert result.returncode == (1 if check else 0), result.stderr
    findings = json.loads(result.stdout)
    assert len(findings) == len(messages)
    assert all(f['check'] == check for f in findings)
    for message in messages:
        assert any(message in f['message'] for f in findings)


@pytest.mark.parametrize('code', [
    'import builtins as b; b.open(file="/tmp/state.json", mode="a")',
    'from builtins import open as write; write("../state.json", "r+")',
    'import os as o; o.open(path="/tmp/state.json", flags=o.O_RDWR)',
    'from os import open as write, O_TRUNC; write("../state.json", O_TRUNC)',
    'import os; os.open("/tmp/state.json", os.O_RDONLY | os.O_CREAT)',
    'import os; os.open("/tmp/state.json", os.O_RDONLY | os.O_APPEND)',
    'import os; os.open("/tmp/state.json", 1)',
])
def test_qualified_open_write_is_reported(tmp_path, code):
    skill = copy_green(tmp_path)
    with (skill / 'main.py').open('a') as stream:
        stream.write(code + '\n')
    result = run(skill)
    assert result.returncode == 1, result.stderr
    assert any(f['check'] == 8 and 'write destination' in f['message']
               for f in json.loads(result.stdout))
