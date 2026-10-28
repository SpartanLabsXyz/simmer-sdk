import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")
PENDING = {
    "ok": True, "status": "pending-publication", "publicationStatus": "pending",
    "slug": "receipt-test", "version": "1.0.0",
}
PUBLISHED = dict(PENDING, status="published", publicationStatus="published")


def publish(tmp_path, response, exit_code=0):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: receipt-test\nmetadata:\n  version: 1.0.0\n---\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npx = bin_dir / "npx"
    npx.write_text('''#!/usr/bin/python3
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as handle:
    handle.write(json.dumps(args) + "\\n")
if args[1] == "inspect":
    sys.exit(0)
if args[1] != "publish":
    sys.exit(99)
if "--dry-run" in args:
    print(json.dumps({"ok": True, "status": "would-publish", "slug": "receipt-test", "version": "1.0.0"}))
    sys.exit(0)
print("publish diagnostic", file=sys.stderr)
print(os.environ["PUBLISH_RESPONSE"])
sys.exit(int(os.environ["PUBLISH_EXIT"]))
''')
    npx.chmod(0o755)
    curl = bin_dir / "curl"
    curl.write_text('#!/bin/sh\ntouch "$SYNC_CALLED"\nprintf 200\n')
    curl.chmod(0o755)
    receipt_dir = tmp_path / "receipts with spaces"
    calls_file = tmp_path / "calls"
    result = subprocess.run(
        [BASH, str(ROOT / "scripts" / "publish.sh"), str(skill)],
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.defpath}",
            "HOME": str(tmp_path),
            "PAPERCLIP_RUN_SCRATCH_DIR": str(tmp_path / "scratch"),
            "CLAWHUB_RECEIPT_DIR": str(receipt_dir),
            "CALLS": str(calls_file),
            "PUBLISH_RESPONSE": response,
            "PUBLISH_EXIT": str(exit_code),
            "SIMMER_ADMIN_API_KEY": "test-placeholder",
            "SYNC_CALLED": str(tmp_path / "sync-called"),
        },
        capture_output=True, text=True, timeout=10,
    )
    calls = [json.loads(line) for line in calls_file.read_text().splitlines()]
    assert not (tmp_path / "scratch" / "receipt-test").exists()
    return result, calls, receipt_dir


@pytest.mark.parametrize("payload", [PENDING, PUBLISHED])
def test_publish_records_exact_version_receipt(tmp_path, payload):
    result, calls, receipt_dir = publish(tmp_path, json.dumps(payload))
    assert result.returncode == 0, result.stdout + result.stderr
    assert calls[-1][-1] == "--json"
    assert "--dry-run" not in calls[-1]
    assert json.loads((receipt_dir / "receipt-test-1.0.0.json").read_text()) == payload
    assert len(list(receipt_dir.iterdir())) == 1
    assert (tmp_path / "sync-called").exists()


@pytest.mark.parametrize("response,exit_code", [
    (json.dumps(PENDING), 1),
    ("", 0),
    ("unparseable", 0),
    ("null", 0),
    ("[]", 0),
    ("{}", 0),
    (json.dumps(dict(PENDING, ok=False)), 0),
    (json.dumps(dict(PENDING, ok=1)), 0),
    (json.dumps(dict(PENDING, status="submitted")), 0),
    (json.dumps(dict(PENDING, publicationStatus="published")), 0),
    (json.dumps(dict(PUBLISHED, publicationStatus="pending")), 0),
    (json.dumps(dict(PENDING, slug="wrong")), 0),
    (json.dumps(dict(PENDING, version="0.9.0")), 0),
])
def test_invalid_publish_result_does_not_create_receipt_or_sync(tmp_path, response, exit_code):
    result, calls, receipt_dir = publish(tmp_path, response, exit_code)
    assert result.returncode != 0, result.stdout + result.stderr
    assert calls[-1][-1] == "--json"
    assert not (receipt_dir / "receipt-test-1.0.0.json").exists()
    assert not (tmp_path / "sync-called").exists()
