import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")
PENDING = {
    "ok": True, "status": "pending-publication", "publicationStatus": "pending",
    "slug": "receipt-test", "version": "1.0.0",
}
PUBLISHED = dict(PENDING, status="published", publicationStatus="published")
ALREADY_SUBMITTED = dict(PENDING, status="already-submitted")
INSPECTION = {
    "skill": {"slug": "receipt-test"}, "owner": {"handle": "simmer"},
    "version": {"version": "1.0.0", "security": {"status": "pending", "hasWarnings": False}},
    "latestVersion": {"version": "0.9.0"},
}
DUPLICATE = "Error: Version 1.0.0 already exists. Increment the version number and try again."


def publish(tmp_path, response, exit_code=0, *, inspection=None, inspect_exit=1,
            diagnostic="publish diagnostic"):
    skill = tmp_path / "skills" / "receipt-test"
    skill.mkdir(parents=True)
    (skill / "clawhub.json").write_text("{}")
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
if args[0] == "-y":
    args = args[1:]
with open(os.environ["CALLS"], "a") as handle:
    handle.write(json.dumps(args) + "\\n")
if args[1] == "inspect":
    print(os.environ["INSPECT_RESPONSE"])
    sys.exit(int(os.environ["INSPECT_EXIT"]))
if args[1] != "publish":
    sys.exit(99)
if "--dry-run" in args:
    print(json.dumps({"ok": True, "status": "would-publish", "slug": "receipt-test", "version": "1.0.0"}))
    sys.exit(0)
print(os.environ["PUBLISH_DIAGNOSTIC"], file=sys.stderr)
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
            "PUBLISH_DIAGNOSTIC": diagnostic,
            "INSPECT_RESPONSE": json.dumps(inspection),
            "INSPECT_EXIT": str(inspect_exit),
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


def test_already_submitted_exact_version_skips_upload_and_records_receipt(tmp_path):
    result, calls, receipt_dir = publish(
        tmp_path, "", 1, inspection=INSPECTION, inspect_exit=0, diagnostic=DUPLICATE,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(calls) == 1
    assert calls[0] == [
        "clawhub@0.24.1", "inspect", "@simmer/receipt-test",
        "--version", "1.0.0", "--json",
    ]
    assert "::notice::receipt-test@1.0.0 already submitted; skipping upload" in result.stdout
    assert json.loads((receipt_dir / "receipt-test-1.0.0.json").read_text()) == ALREADY_SUBMITTED
    assert not (tmp_path / "sync-called").exists()
    verification = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_skill_publish.py"),
         "--root", str(tmp_path), "--receipt-dir", str(receipt_dir)],
        env={
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.defpath}", "HOME": str(tmp_path),
            "SKILLS_TO_PUBLISH": '["skills/receipt-test"]',
            "CALLS": str(tmp_path / "calls"),
            "INSPECT_RESPONSE": json.dumps(INSPECTION), "INSPECT_EXIT": "0",
        },
        capture_output=True, text=True, timeout=10,
    )
    assert verification.returncode == 0, verification.stdout + verification.stderr
    assert "::notice::receipt-test@1.0.0: Security PENDING; not-yet" in verification.stdout


@pytest.mark.parametrize("inspection", [
    {**INSPECTION, "skill": {"slug": "wrong"}},
    {**INSPECTION, "owner": {"handle": "wrong"}},
    {**INSPECTION, "version": {"version": "0.9.0"}},
    {}, None,
])
def test_invalid_exact_inspection_fails_without_upload_or_receipt(tmp_path, inspection):
    result, calls, receipt_dir = publish(
        tmp_path, json.dumps(PENDING), inspection=inspection, inspect_exit=0,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert all(call[1] == "inspect" for call in calls)
    assert not (receipt_dir / "receipt-test-1.0.0.json").exists()
    assert not (tmp_path / "sync-called").exists()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("suffix", ["", " (reset in 43s)", " (reset in 2s)"],
                         ids=["plain", "reset-43", "reset-2"])
def test_duplicate_publish_error_hands_exact_version_to_verification(tmp_path, stream, suffix):
    diagnostic = DUPLICATE + suffix
    result, calls, receipt_dir = publish(
        tmp_path, diagnostic if stream == "stdout" else "", 1,
        diagnostic=diagnostic if stream == "stderr" else "publish diagnostic",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already submitted; skipping upload" in result.stdout
    assert json.loads((receipt_dir / "receipt-test-1.0.0.json").read_text()) == ALREADY_SUBMITTED
    assert len([call for call in calls if call[1] == "publish" and "--dry-run" not in call]) == 1
    assert not (tmp_path / "sync-called").exists()


@pytest.mark.parametrize("diagnostic", [
    "Error: Unauthorized", "Error: Upload failed",
    DUPLICATE.replace("1.0.0", "1.0.01"),
    DUPLICATE.replace("1.0.0", "1x0x0"),
    "Additional error: " + DUPLICATE,
    DUPLICATE.replace("1.0.0", "1.0.01") + " (reset in 43s)",
    DUPLICATE.replace("1.0.0", "1x0x0") + " (reset in 43s)",
    DUPLICATE + " (unrelated)",
    DUPLICATE + " (reset in 43s) extra",
    DUPLICATE + " extra (reset in 43s)",
    DUPLICATE + " (reset in 43s) (reset in 2s)",
    DUPLICATE + " (reset in xs)",
    DUPLICATE + " (reset in s)",
])
def test_other_publish_errors_still_fail(tmp_path, diagnostic):
    result, _, receipt_dir = publish(tmp_path, "", 1, diagnostic=diagnostic)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "already submitted; skipping upload" not in result.stdout
    assert not (receipt_dir / "receipt-test-1.0.0.json").exists()
    assert not (tmp_path / "sync-called").exists()


HELD_INSPECTION = {
    "skill": {"slug": "receipt-test"},
    "owner": None,
    "latestVersion": {"version": "0.9.0"},
    "version": None,
    "versions": None,
    "file": None,
    "publication": {
        "version": "1.0.0",
        "stage": "pending_checks",
        "retrying": False,
        "updatedAt": 1761600000000,
        "checks": {"trufflehog": "pending", "clawscan": "clean"},
    },
    "nextSteps": {
        "scanReport": "clawhub scan download @simmer/receipt-test --version 1.0.0",
        "supportUrl": "https://github.com/openclaw/clawhub/issues/new",
    },
}


@pytest.mark.parametrize("owner", [None, {"handle": "simmer"}])
def test_held_inspection_skips_upload_and_records_receipt(tmp_path, owner):
    inspection = dict(HELD_INSPECTION, owner=owner)
    result, calls, receipt_dir = publish(
        tmp_path, "", 1, inspection=inspection, inspect_exit=0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(calls) == 1
    assert calls[0] == [
        "clawhub@0.24.1", "inspect", "@simmer/receipt-test",
        "--version", "1.0.0", "--json",
    ]
    assert "::notice::receipt-test@1.0.0 already submitted; skipping upload" in result.stdout
    assert json.loads((receipt_dir / "receipt-test-1.0.0.json").read_text()) == ALREADY_SUBMITTED
    assert not (tmp_path / "sync-called").exists()
    verification = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_skill_publish.py"),
         "--root", str(tmp_path), "--receipt-dir", str(receipt_dir)],
        env={
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.defpath}", "HOME": str(tmp_path),
            "SKILLS_TO_PUBLISH": '["skills/receipt-test"]',
            "CALLS": str(tmp_path / "verify-calls"),
            "INSPECT_RESPONSE": json.dumps(inspection), "INSPECT_EXIT": "0",
        },
        capture_output=True, text=True, timeout=10,
    )
    assert verification.returncode == 0, verification.stdout + verification.stderr
    assert "::notice::receipt-test@1.0.0: publication pending_checks; not-yet" in verification.stdout
    assert "publication stage: pending_checks" in verification.stdout


@pytest.mark.parametrize("inspection", [
    dict(HELD_INSPECTION, skill={"slug": "wrong"}),
    dict(HELD_INSPECTION, owner={"handle": "wrong"}),
    dict(HELD_INSPECTION, publication=dict(HELD_INSPECTION["publication"], version="0.9.0")),
    dict(HELD_INSPECTION, publication=None),
    {"skill": {"slug": "receipt-test"}, "owner": None, "version": None},
])
def test_mismatched_held_inspection_fails_without_upload_or_receipt(tmp_path, inspection):
    result, calls, receipt_dir = publish(
        tmp_path, json.dumps(PENDING), inspection=inspection, inspect_exit=0,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert all(call[1] == "inspect" for call in calls)
    assert not (receipt_dir / "receipt-test-1.0.0.json").exists()
    assert not (tmp_path / "sync-called").exists()
