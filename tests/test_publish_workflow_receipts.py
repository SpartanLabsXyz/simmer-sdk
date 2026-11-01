import os
from pathlib import Path
import re
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[1]


def test_failed_publish_with_no_receipts_still_has_uploadable_artifact(tmp_path):
    workflow = (ROOT / '.github/workflows/publish-packages.yml').read_text()
    steps = dict(re.findall(
        r'^      - name: ([^\n]+)\n(.*?)(?=^      - |^  \S|\Z)',
        workflow, flags=re.MULTILINE | re.DOTALL,
    ))
    prepare = steps.get('Prepare ClawHub publish receipts')
    assert prepare is not None, 'No receipts preparation; an empty upload creates no artifact'
    upload = steps['Save ClawHub publish receipts']
    assert '        if: always()\n' in prepare
    assert '        if: always()\n' in upload
    assert workflow.index('name: Prepare ClawHub publish receipts') < workflow.index(
        'name: Save ClawHub publish receipts'
    )
    assert 'name: clawhub-publish-receipts\n' in upload
    assert 'path: ${{ runner.temp }}/clawhub-receipts\n' in upload
    assert 'CLAWHUB_RECEIPT_DIR: ${{ runner.temp }}/clawhub-receipts\n' in prepare

    directory = tmp_path / 'clawhub receipts'
    command = textwrap.dedent(prepare.split('        run: |\n', 1)[1])
    result = subprocess.run(
        ['bash', '-euo', 'pipefail', '-c', command],
        env={**os.environ, 'CLAWHUB_RECEIPT_DIR': str(directory)},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(path.is_file() and not path.name.startswith('.') for path in directory.iterdir())
    assert not list(directory.glob('*.json'))
