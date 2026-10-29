import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")
VALID = {"ok": True, "status": "would-publish", "slug": "preflight-test", "version": "1.0.0"}


def run_publish(tmp_path, response, exit_code=0):
    skill = tmp_path / "source with spaces"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: preflight-test\nmetadata:\n  version: 1.0.0\n---\n"
    )
    (skill / "tests").mkdir()
    (skill / "tests" / "excluded.py").write_text("fixture\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npx = bin_dir / "npx"
    npx.write_text('''#!/usr/bin/python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as handle:
    handle.write(json.dumps(args) + "\\n")
if args[1] == "inspect":
    sys.exit(0)
if args[1] != "publish":
    sys.exit(99)
bundle = Path(args[2])
assert (bundle / "SKILL.md").is_file()
assert not (bundle / "tests").exists()
if "--dry-run" in args:
    print(os.environ["PREFLIGHT_RESPONSE"])
    sys.exit(int(os.environ["PREFLIGHT_EXIT"]))
Path(os.environ["PUBLISH_MARKER"]).touch()
''')
    npx.chmod(0o755)
    calls_file = tmp_path / "calls"
    result = subprocess.run(
        [BASH, str(ROOT / "scripts" / "publish.sh"), str(skill)],
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.defpath}",
            "HOME": str(tmp_path),
            "PAPERCLIP_RUN_SCRATCH_DIR": str(tmp_path / "scratch"),
            "CALLS": str(calls_file),
            "PUBLISH_MARKER": str(tmp_path / "published"),
            "PREFLIGHT_RESPONSE": response,
            "PREFLIGHT_EXIT": str(exit_code),
        },
        capture_output=True, text=True, timeout=10,
    )
    calls = [json.loads(line) for line in calls_file.read_text().splitlines()]
    assert not (tmp_path / "scratch" / "preflight-test").exists()
    return result, calls


def test_preflight_validates_same_bundle_before_publish(tmp_path):
    result, calls = run_publish(tmp_path, json.dumps(VALID))
    assert result.returncode == 0, result.stdout + result.stderr
    publishes = [args for args in calls if args[1] == "publish"]
    assert len(publishes) == 2
    preview, publish = publishes
    assert preview == publish + ["--dry-run", "--json"]
    assert publish == [
        "clawhub@0.23.3", "publish", str(tmp_path / "scratch" / "preflight-test"),
        "--version", "1.0.0", "--owner", "simmer", "--slug", "preflight-test",
    ]
    assert (tmp_path / "published").exists()


@pytest.mark.parametrize("response,exit_code", [
    (json.dumps(VALID), 1),
    ("not JSON", 0),
    ("", 0),
    ("null", 0),
    ("[]", 0),
    ("{}", 0),
    (json.dumps(dict(VALID, ok=False)), 0),
    (json.dumps(dict(VALID, ok=1)), 0),
    (json.dumps(dict(VALID, status="published")), 0),
    (json.dumps(dict(VALID, slug="wrong")), 0),
    (json.dumps(dict(VALID, version="0.9.0")), 0),
])
def test_failed_preflight_stops_publish(tmp_path, response, exit_code):
    result, calls = run_publish(tmp_path, response, exit_code)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "::error::" in result.stdout + result.stderr
    assert not (tmp_path / "published").exists()
    publishes = [args for args in calls if args[1] == "publish"]
    assert len(publishes) == 1
    assert "--dry-run" in publishes[0]
