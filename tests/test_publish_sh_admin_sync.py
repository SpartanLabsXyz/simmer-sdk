import os
from pathlib import Path
import shutil
import subprocess

import pytest


BASH = shutil.which("bash")
ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")


@pytest.mark.parametrize("has_key,http_code,curl_exit,expected_exit", [
    (False, "200", 0, 0),
    (True, "200", 0, 0),
    (True, "202", 0, 0),
    (True, "409", 0, 0),
    (True, "500", 0, 1),
    (True, "000", 0, 1),
    (True, "000", 7, 1),
])
def test_admin_sync(tmp_path, has_key, http_code, curl_exit, expected_exit):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: sync-test\nmetadata:\n  version: 1.0.0\n---\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stubs = {
        "npx": (
            '#!/bin/sh\n'
            'case " $* " in *" --dry-run "*)\n'
            '  echo \'{"ok":true,"status":"would-publish","slug":"sync-test","version":"1.0.0"}\'; exit 0;; esac\n'
            'case "$2" in inspect|publish) exit 0;; *) exit 99;; esac\n'
        ),
        "curl": '#!/bin/sh\ntouch "$CURL_CALLED"\nprintf "%s" "$HTTP_CODE"\nexit "$CURL_EXIT"\n',
    }
    for name, source in stubs.items():
        executable = bin_dir / name
        executable.write_text(source)
        executable.chmod(0o755)
    scratch = tmp_path / "scratch"
    curl_called = tmp_path / "curl-called"
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.defpath}",
        "HOME": str(tmp_path),
        "PAPERCLIP_RUN_SCRATCH_DIR": str(scratch),
        "CURL_CALLED": str(curl_called),
        "HTTP_CODE": http_code,
        "CURL_EXIT": str(curl_exit),
    }
    if has_key:
        env["SIMMER_ADMIN_API_KEY"] = "test-admin-key"
    result = subprocess.run(
        [BASH, str(ROOT / "scripts" / "publish.sh"), str(skill)],
        env=env, capture_output=True, text=True, timeout=10,
    )
    output = result.stdout + result.stderr
    assert "test-admin-key" not in output
    assert result.returncode == expected_exit, output
    assert curl_called.exists() == has_key
    assert not (scratch / "sync-test").exists()
    if not has_key:
        assert output.count("Skipping backend skill registry sync: SIMMER_ADMIN_API_KEY not set") == 1
        assert "curl -X POST" in output
    elif expected_exit:
        assert "::error::" in output
        assert f"HTTP {http_code}" in output
        assert "curl -X POST" in output
        assert "$SIMMER_ADMIN_API_KEY" in output
