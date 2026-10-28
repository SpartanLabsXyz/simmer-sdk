#!/usr/bin/env python3
"""Verify exact planned ClawHub versions and escalate suspicious security results.

Use --recheck to inspect current scans instead of deferring based on a pending
submission receipt. SKILLS_TO_PUBLISH is the plan's JSON list of directories.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

import check_publish_lag
from clawhub_publish_receipt import validate_receipt


CLAWHUB_VERSION = '0.23.3'


def annotate(level: str, message: str) -> None:
    escaped = message.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
    print(f'::{level}::{escaped}')


def command(args: list[str]) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=120).stdout


def classify(skill: check_publish_lag.Skill, payload: dict) -> tuple[str, str]:
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
            if validate_receipt(receipt, skill.slug, skill.version) == 'pending':
                annotate('notice', f'{identity}: Security PENDING; not-yet '
                         '(confirmed at submission; current scan not yet verified)')
                print('Use --recheck after scans settle to inspect current Security status.')
                return True
        raw = command([
            'npx', '-y', f'clawhub@{CLAWHUB_VERSION}', 'inspect', f'@simmer/{skill.slug}',
            '--version', skill.version, '--json',
        ])
        status, outcome = classify(skill, json.loads(raw))
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            subprocess.SubprocessError, check_publish_lag.PublishLagError) as exc:
        annotate('error', f'{identity}: cannot verify ClawHub Security: {exc}')
        return False
    level = {'ok': 'notice', 'not-yet': 'notice', 'needs-human': 'warning'}.get(outcome, 'error')
    annotate(level, f'{identity}: Security {status.upper()}; {outcome}')
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
                        help='Inspect current scans even if a receipt recorded pending submission.')
    args = parser.parse_args()
    try:
        planned = json.loads(os.environ['SKILLS_TO_PUBLISH'])
        if not isinstance(planned, list) or any(not isinstance(path, str) for path in planned):
            raise ValueError('expected a list of planned skill directories')
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
