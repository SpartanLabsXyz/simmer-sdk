"""Non-interactive stdin must not be able to self-authorize a public skill publish.

Regression for the Codex finding on SIM-5660 PR #396: confirm_publish.py used
plain input() on stdin, so `printf 'slug\\n' | python confirm_publish.py ...`
reached the publish subprocess with zero human involvement.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "simmer-skill-builder"
    / "scripts"
    / "confirm_publish.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("confirm_publish", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_piped_stdin_is_rejected_without_invoking_publish(tmp_path):
    # Real subprocess so stdin is genuinely a pipe, not a TTY — mocking
    # subprocess.run in-process wouldn't exercise the isatty() check at all.
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path), "--slug", "example-skill", "--version", "1.0.0"],
        input="example-skill\n",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "TTY" in result.stderr


def test_get_confirmation_returns_none_when_stdin_not_a_tty(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: False)
    assert module.get_confirmation("example-skill") is None
