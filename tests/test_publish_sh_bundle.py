import os
from pathlib import Path
import shutil
import subprocess

import pytest


BASH = shutil.which("bash")
ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")


def test_publish_bundle_excludes_tests_and_bytecode(tmp_path):
    skill = tmp_path / "skill"
    files = {
        "SKILL.md": "---\nname: bundle-test\nmetadata:\n  version: 1.0.0\n---\n",
        "copytrading_trader.py": "print('entrypoint')\n",
        "sub/helper.py": "print('helper')\n",
        "tests/test_x.py": "def test_x(): pass\n",
        "tests/__pycache__/x.pyc": "bytecode",
        "sub/__pycache__/helper.pyc": "bytecode",
        "loose.pyc": "bytecode",
        "loose.pyo": "bytecode",
    }
    for name, content in files.items():
        path = skill / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npx = bin_dir / "npx"
    npx.write_text(
        '#!/bin/sh\n'
        'case "$2" in\n'
        '  inspect) exit 0;;\n'
        '  publish) cd "$3" && find . -type f > "$BUNDLE_FILES";;\n'
        '  *) exit 99;;\n'
        'esac\n'
    )
    npx.chmod(0o755)
    bundle_files = tmp_path / "bundle-files"
    result = subprocess.run(
        [BASH, str(ROOT / "scripts" / "publish.sh"), str(skill)],
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.defpath}",
            "HOME": str(tmp_path),
            "PAPERCLIP_RUN_SCRATCH_DIR": str(tmp_path / "scratch"),
            "BUNDLE_FILES": str(bundle_files),
        },
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    bundled = {Path(name) for name in bundle_files.read_text().splitlines()}
    assert Path("SKILL.md") in bundled
    assert Path("copytrading_trader.py") in bundled
    assert Path("sub/helper.py") in bundled
    assert not any("tests" in path.parts for path in bundled), bundled
    assert not any("__pycache__" in path.parts for path in bundled), bundled
    assert not any(path.suffix in {".pyc", ".pyo"} for path in bundled), bundled
    assert all((skill / name).exists() for name in files)
