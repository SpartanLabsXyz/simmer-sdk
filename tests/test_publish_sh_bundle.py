import os
from pathlib import Path
import shutil
import subprocess

import pytest

from scripts.check_skill_compliance import stage_bundle


BASH = shutil.which("bash")
ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")


def bundle_contents(directory):
    return {
        path.relative_to(directory): (
            ("symlink", os.readlink(path)) if path.is_symlink() else
            ("directory",) if path.is_dir() else ("file", path.read_bytes())
        )
        for path in directory.rglob("*")
    }


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
        "sub/.pyc": "bytecode",
        "sub/.pyo": "bytecode",
        "sub/tests/test_nested.py": "def test_nested(): pass\n",
        ".env": "fixture",
        ".hidden/config": "fixture",
        "sub/.kept": "nested dotfile",
    }
    for name, content in files.items():
        path = skill / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    (skill / "helper-link").symlink_to("sub/helper.py")
    (skill / "sub-link").symlink_to("sub", target_is_directory=True)
    (skill / "dangling-link").symlink_to("missing")
    original = bundle_contents(skill)
    expected_bundle = tmp_path / "expected"
    stage_bundle(skill, expected_bundle)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npx = bin_dir / "npx"
    npx.write_text(
        '#!/bin/sh\n'
        'case "$2" in\n'
        '  inspect) exit 0;;\n'
        '  publish) cp -R "$3" "$BUNDLE_COPY" && cd "$3" && find . -type f > "$BUNDLE_FILES";;\n'
        '  *) exit 99;;\n'
        'esac\n'
    )
    npx.chmod(0o755)
    bundle_files = tmp_path / "bundle-files"
    bundle_copy = tmp_path / "published"
    result = subprocess.run(
        [BASH, str(ROOT / "scripts" / "publish.sh"), str(skill)],
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.defpath}",
            "HOME": str(tmp_path),
            "PAPERCLIP_RUN_SCRATCH_DIR": str(tmp_path / "scratch"),
            "BUNDLE_FILES": str(bundle_files),
            "BUNDLE_COPY": str(bundle_copy),
        },
        cwd=tmp_path,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    bundled = {Path(name) for name in bundle_files.read_text().splitlines()}
    assert Path("SKILL.md") in bundled
    assert Path("copytrading_trader.py") in bundled
    assert Path("sub/helper.py") in bundled
    assert not any("tests" in path.parts for path in bundled), bundled
    assert not any("__pycache__" in path.parts for path in bundled), bundled
    assert Path("sub/.pyc") not in bundled
    assert Path("sub/.pyo") not in bundled
    assert not any(path.name.endswith((".pyc", ".pyo")) for path in bundled), bundled
    assert all((skill / name).exists() for name in files)
    assert bundle_contents(bundle_copy) == bundle_contents(expected_bundle)
    assert Path("sub/.kept") in bundled
    assert not (bundle_copy / ".env").exists()
    assert not (bundle_copy / ".hidden").exists()
    assert bundle_contents(skill) == original
    assert not (tmp_path / "scratch" / "bundle-test").exists()


def test_publish_aborts_before_npx_when_stage_helper_is_missing(tmp_path):
    # Copy only the publisher: its sibling staging helper is deliberately absent.
    script_dir = tmp_path / "scripts with spaces"
    script_dir.mkdir()
    publisher = script_dir / "publish.sh"
    shutil.copy2(ROOT / "scripts" / "publish.sh", publisher)
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: bundle-test\nmetadata:\n  version: 1.0.0\n---\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npx = bin_dir / "npx"
    npx.write_text('#!/bin/sh\ntouch "$NPX_CALLED"\nexit 0\n')
    npx.chmod(0o755)
    npx_called = tmp_path / "npx-called"
    scratch = tmp_path / "scratch"
    result = subprocess.run(
        [BASH, str(publisher), str(skill)],
        env={
            "PATH": f"{bin_dir}{os.pathsep}{os.defpath}",
            "HOME": str(tmp_path),
            "PAPERCLIP_RUN_SCRATCH_DIR": str(scratch),
            "NPX_CALLED": str(npx_called),
        },
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "skill_bundle.py" in result.stderr
    assert not npx_called.exists()
    assert not (scratch / "bundle-test").exists()
