#!/usr/bin/env python3
"""Verify exact planned ClawHub versions and escalate suspicious security results.

Always inspect current scans. Use --recheck to skip submission receipt validation.
SKILLS_TO_PUBLISH is the plan's non-empty JSON list of directories.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import check_publish_lag
from clawhub_publish_receipt import validate_receipt


CLAWHUB_VERSION = '0.24.1'
PUBLIC_SECURITY = {'clean', 'pending', 'suspicious', 'malicious', 'error'}
HELD_NOT_YET = {'pending_checks', 'ready_to_finalize', 'finalizing'}
HELD_RETRY = {'checks_failed', 'checks_incomplete'}
HELD_FAILURE = {'blocked', 'failed', 'expired', 'unavailable'}
CHECK_NAMES = {'trufflehog', 'clawscan'}
CHECK_VALUES = {'pending', 'clean', 'blocked', 'failed'}


def annotate(level: str, message: str) -> None:
    escaped = message.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    print(f'::{level}::{escaped}')


# Same shapes as ClawHub CLI redactSensitiveText (openclaw/clawhub 5f3b6e9),
# plus CLAWHUB_TOKEN= assignments. Applied to the full stderr before the
# 300-character cut so a sliced token tail cannot reach the annotation.
_SENSITIVE_TEXT = (
    (re.compile(
        r'(["\']?(?:authorization|set-cookie|cookie|api[_-]?key|secret|token)["\']?'
        r'\s*:\s*["\'])[^"\'\n\r]+',
        re.IGNORECASE,
    ), r'\1[redacted]'),
    (re.compile(r'(authorization:\s*bearer\s+)[^\s,;]+', re.IGNORECASE), r'\1[redacted]'),
    (re.compile(
        r'(authorization:\s*)(?!bearer\s+)(?:[^\s,;]+\s+)?[^\s,;]+',
        re.IGNORECASE,
    ), r'\1[redacted]'),
    (re.compile(r'(cookie:\s*)[^\n\r]+', re.IGNORECASE), r'\1[redacted]'),
    (re.compile(r'((?:api[_-]?key|secret|token):\s*)[^\s,;]+', re.IGNORECASE), r'\1[redacted]'),
    (re.compile(
        r'(CLAWHUB_TOKEN\s*=\s*)(?:"[^"\n\r]*"|\'[^\'\n\r]*\'|\S+)',
        re.IGNORECASE,
    ), r'\1[redacted]'),
    (re.compile(r'clh_[A-Za-z0-9._-]+'), '[redacted]'),
    (re.compile(
        r'([?&](?:access_token|api[_-]?key|auth|authorization|callback|callback_url|'
        r'code|key|redirect_uri|return_to|secret|state|token)=)[^&#\s]+',
        re.IGNORECASE,
    ), r'\1[redacted]'),
    (re.compile(r'://[^/\s@]+@'), '://[redacted]@'),
)


def redact_sensitive(value: str) -> str:
    for pattern, replacement in _SENSITIVE_TEXT:
        value = pattern.sub(replacement, value)
    return value


def stderr_excerpt(stderr: str | None) -> str:
    redacted = redact_sensitive(stderr or '')
    excerpt = ' '.join(redacted.splitlines())[-300:]
    return excerpt if excerpt.strip() else ''


def command(args: list[str]) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120).stdout


def format_updated_at(updated_at: object) -> str:
    if isinstance(updated_at, bool) or not isinstance(updated_at, int) or updated_at < 0:
        raise ValueError('invalid publication updatedAt')
    seconds, millis = divmod(updated_at, 1000)
    try:
        moment = datetime.fromtimestamp(seconds, timezone.utc)
    except (OSError, OverflowError, ValueError) as exc:
        raise ValueError('invalid publication updatedAt') from exc
    return moment.strftime('%Y-%m-%dT%H:%M:%S') + f'.{millis:03d}Z'


def log_held(payload: dict, updated_at: str, checks: dict) -> None:
    latest = payload.get('latestVersion')
    latest_version = 'none' if latest is None else latest['version']
    publication = payload['publication']
    next_steps = payload['nextSteps']
    print(f'publication stage: {publication["stage"]}')
    print(f'publication retrying: {str(publication["retrying"]).lower()}')
    print(f'publication updatedAt: {updated_at}')
    print(f'publication trufflehog: {checks.get("trufflehog", "absent")}')
    print(f'publication clawscan: {checks.get("clawscan", "absent")}')
    print(f'publication latestVersion: {latest_version}')
    print(f'publication scanReport: {next_steps["scanReport"]}')
    print(f'publication supportUrl: {next_steps["supportUrl"]}')


def classify_held(skill: check_publish_lag.Skill, payload: dict) -> tuple[str, str]:
    skill_info = payload.get('skill')
    publication = payload.get('publication')
    if (not isinstance(skill_info, dict) or skill_info.get('slug') != skill.slug
            or not isinstance(publication, dict)
            or publication.get('version') != skill.version):
        raise ValueError('ClawHub slug, owner, or requested version mismatch')
    owner = payload.get('owner')
    if owner is not None and (not isinstance(owner, dict) or owner.get('handle') != 'simmer'):
        raise ValueError('ClawHub slug, owner, or requested version mismatch')
    stage = publication.get('stage')
    retrying = publication.get('retrying')
    if not isinstance(stage, str) or not isinstance(retrying, bool):
        raise ValueError('invalid publication stage')
    updated_at = format_updated_at(publication.get('updatedAt'))
    checks = publication.get('checks')
    if checks is None:
        checks = {}
    if (not isinstance(checks, dict) or any(name not in CHECK_NAMES for name in checks)
            or any(value not in CHECK_VALUES for value in checks.values())):
        raise ValueError('unrecognized publication check')
    latest = payload.get('latestVersion')
    if latest is not None and (
        not isinstance(latest, dict) or not isinstance(latest.get('version'), str)
    ):
        raise ValueError('invalid publication latestVersion')
    next_steps = payload.get('nextSteps')
    if (not isinstance(next_steps, dict) or not isinstance(next_steps.get('scanReport'), str)
            or not isinstance(next_steps.get('supportUrl'), str)):
        raise ValueError('invalid publication nextSteps')
    log_held(payload, updated_at, checks)
    if stage == 'published':
        raise ValueError('publication stage published without a version object')
    if stage not in HELD_NOT_YET | HELD_RETRY | HELD_FAILURE:
        raise ValueError('unrecognized publication stage')
    if any(value == 'blocked' for value in checks.values()) or stage in HELD_FAILURE:
        return stage, 'failure'
    if stage in HELD_NOT_YET:
        return stage, 'not-yet'
    return stage, 'not-yet' if retrying else 'failure'


def classify(skill: check_publish_lag.Skill, payload: dict) -> tuple[str, str]:
    if isinstance(payload, dict) and payload.get('version') is None and 'publication' in payload:
        return classify_held(skill, payload)
    if (payload['skill']['slug'] != skill.slug
            or payload['owner']['handle'] != 'simmer'
            or payload['version']['version'] != skill.version):
        raise ValueError('ClawHub slug, owner, or requested version mismatch')
    security = payload['version']['security']
    status = security['status']
    if status not in {'clean', 'pending', 'suspicious', 'malicious', 'error'}:
        raise ValueError('unrecognized Security status')
    if not isinstance(security['hasWarnings'], bool):
        raise ValueError('invalid Security result')
    if status == 'suspicious':
        return status, 'needs-human'
    latest = payload['latestVersion']
    comparison = None
    if latest is not None:
        comparison = check_publish_lag.compare_versions(skill.version, latest['version'])
    if status == 'pending':
        return status, 'not-yet'
    if status != 'clean':
        return status, 'failure'
    return status, 'ok' if comparison is not None and comparison <= 0 else 'drift'


def ensure_issue(skill: check_publish_lag.Skill) -> None:
    repository = os.environ['GITHUB_REPOSITORY']
    key = f'clawhub-security:{skill.slug}@{skill.version}'
    marker = f'<!-- {key} -->'
    issues = json.loads(command([
        'gh', 'issue', 'list', '--repo', repository, '--state', 'open',
        '--search', f'"{key}" in:body', '--json', 'body', '--limit', '100',
    ]))
    if not isinstance(issues, list) or any(
        not isinstance(issue, dict) or not isinstance(issue.get('body'), str) for issue in issues
    ):
        raise ValueError('invalid open-issue search result')
    if any(marker in issue['body'] for issue in issues):
        print(f'{skill.slug}@{skill.version}: open needs-human issue already exists')
        return
    labels = json.loads(command([
        'gh', 'label', 'list', '--repo', repository, '--search', 'needs-human',
        '--json', 'name', '--limit', '100',
    ]))
    if not isinstance(labels, list) or any(
        not isinstance(label, dict) or not isinstance(label.get('name'), str) for label in labels
    ):
        raise ValueError('invalid label search result')
    if not any(label['name'] == 'needs-human' for label in labels):
        command(['gh', 'label', 'create', 'needs-human', '--repo', repository,
                 '--color', 'D93F0B', '--description', 'Requires maintainer review'])
    run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repository}"
               f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
    body = (
        f'{marker}\n\nClawHub reports Security SUSPICIOUS for '
        f'`@simmer/{skill.slug}` version `{skill.version}`.\n\n'
        'A maintainer needs to review the security report and decide the next step. '
        'This status alone does not establish malicious behavior.\n\n'
        f'[Workflow run and security report artifact]({run_url}).\n'
    )
    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', encoding='utf-8') as handle:
        handle.write(body)
        handle.flush()
        command([
            'gh', 'issue', 'create', '--repo', repository, '--label', 'needs-human',
            '--title', f'[needs-human] ClawHub Security SUSPICIOUS: {skill.slug}@{skill.version}',
            '--body-file', handle.name,
        ])


def verify(skill: check_publish_lag.Skill, report_dir: Path, receipt_dir: Path | None) -> bool:
    identity = f'{skill.slug}@{skill.version}'
    try:
        if receipt_dir is not None:
            receipt = json.loads((receipt_dir / f'{skill.slug}-{skill.version}.json').read_text())
            validate_receipt(receipt, skill.slug, skill.version)
        raw = command([
            'npx', '-y', f'clawhub@{CLAWHUB_VERSION}', 'inspect', f'@simmer/{skill.slug}',
            '--version', skill.version, '--json',
        ])
        status, outcome = classify(skill, json.loads(raw))
    except subprocess.CalledProcessError as exc:
        detail = redact_sensitive(str(exc))
        excerpt = stderr_excerpt(exc.stderr)
        if excerpt:
            detail = redact_sensitive(f'{detail} {excerpt}')
        annotate('error', f'{identity}: cannot verify ClawHub Security: {detail}')
        return False
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            subprocess.SubprocessError, check_publish_lag.PublishLagError) as exc:
        annotate('error', f'{identity}: cannot verify ClawHub Security: {exc}')
        return False
    level = {'ok': 'notice', 'not-yet': 'notice', 'needs-human': 'warning'}.get(outcome, 'error')
    if status in PUBLIC_SECURITY:
        annotate(level, f'{identity}: Security {status.upper()}; {outcome}')
    else:
        annotate(level, f'{identity}: publication {status}; {outcome}')
    if outcome == 'not-yet':
        print('Pending scan; publication is not yet confirmed. Re-run verification after scans settle.')
    if outcome == 'needs-human':
        try:
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / f'{skill.slug}-{skill.version}.json').write_text(raw, encoding='utf-8')
            ensure_issue(skill)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            annotate('error', f'{identity}: could not save report or open needs-human issue: {exc}')
    return outcome in {'ok', 'not-yet'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=check_publish_lag.ROOT)
    parser.add_argument('--report-dir', type=Path, default=Path('audits'))
    parser.add_argument('--receipt-dir', type=Path)
    parser.add_argument('--recheck', action='store_true',
                        help='Inspect current scans without validating submission receipts.')
    args = parser.parse_args()
    try:
        planned = json.loads(os.environ['SKILLS_TO_PUBLISH'])
        if not isinstance(planned, list) or any(not isinstance(path, str) for path in planned):
            raise ValueError('expected a list of planned skill directories')
        if not planned:
            raise ValueError('expected at least one planned skill directory')
        skills, errors = check_publish_lag.discover_skills(args.root)
        if errors:
            raise ValueError('; '.join(errors))
        selected = {skill.path: skill for skill in skills}
        if any(path not in selected or not selected[path].published for path in planned):
            raise ValueError('plan contains unknown or held skills')
    except (OSError, ValueError, KeyError, TypeError, check_publish_lag.PublishLagError) as exc:
        annotate('error', f'Cannot read skill publish plan: {exc}')
        return 1
    receipt_dir = None if args.recheck else args.receipt_dir
    results = [verify(selected[path], args.report_dir, receipt_dir) for path in planned]
    return int(not all(results))


if __name__ == '__main__':
    raise SystemExit(main())
