"""Offline behavioral tests for the PR money-trace gate."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).with_name("check_money_trace.py")
ROOT = SCRIPT.parent.parent
SNAPSHOT = "proof/money-trace.md"
REQUIRED_LINE = "Money-trace: <repo-relative path>"


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True).strip()

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    (tmp_path / "README.md").write_text("base\n")
    (tmp_path / "proof").mkdir()
    (tmp_path / SNAPSHOT).write_text("before\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")

    def run(paths, body):
        for path in paths:
            target = tmp_path / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("after\n")
        git("add", ".")
        git("commit", "-qm", "head")
        event = tmp_path / "event.json"
        event.write_text(json.dumps({"pull_request": {"body": body}}))
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--base", base, "--event-path", str(event)],
            cwd=tmp_path, capture_output=True, text=True,
        )

    return run


def test_no_money_path(repo):
    result = repo(["README.md"], "")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "No money paths changed; money-trace proof not required."


def test_valid_snapshot(repo):
    result = repo(["simmer_sdk/client.py", SNAPSHOT], f"## Proof\nMoney-trace: {SNAPSHOT}\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert SNAPSHOT in result.stdout


@pytest.mark.parametrize("body", ["## Proof\nTests pass.\n", f"## Summary\nMoney-trace: {SNAPSHOT}\n## Proof\nTests pass.\n", f"## Proof\nTests pass.\n## Risks\nMoney-trace: {SNAPSHOT}\n"])
def test_missing_line_in_proof(repo, body):
    result = repo(["simmer_sdk/client.py", SNAPSHOT], body)
    assert result.returncode == 1
    assert "simmer_sdk/client.py" in result.stdout
    assert REQUIRED_LINE in result.stdout


def test_snapshot_not_in_pr(repo):
    result = repo(["simmer_sdk/signing.py"], f"## Proof\nMoney-trace: {SNAPSHOT}\n")
    assert result.returncode == 1
    assert "simmer_sdk/signing.py" in result.stdout
    assert REQUIRED_LINE in result.stdout


def test_na_with_reason(repo):
    result = repo(["simmer_sdk/dw_redeem.py"], "## Proof\nMoney-trace: n/a - comments only\n")
    assert result.returncode == 0, result.stderr
    assert "::warning::" in result.stdout
    assert "comments only" in result.stdout


@pytest.mark.parametrize("value", ["n/a", "n/a -", "n/a -   "])
def test_bare_na(repo, value):
    result = repo(["simmer_sdk/ows_utils.py"], f"## Proof\nMoney-trace: {value}\n")
    assert result.returncode == 1
    assert REQUIRED_LINE in result.stdout


@pytest.mark.parametrize("path", ["missing.md", "../outside.md", "/tmp/outside.md", "proof"])
def test_invalid_snapshot(repo, path):
    result = repo(["simmer_sdk/client.py", SNAPSHOT], f"## Proof\nMoney-trace: {path}\n")
    assert result.returncode == 1
    assert REQUIRED_LINE in result.stdout


def test_deleted_snapshot(repo, tmp_path):
    (tmp_path / SNAPSHOT).unlink()
    result = repo(["simmer_sdk/client.py"], f"## Proof\nMoney-trace: {SNAPSHOT}\n")
    assert result.returncode == 1
    assert REQUIRED_LINE in result.stdout


def test_trading_skill_directory(repo):
    path = "skills/polymarket-copytrading/SKILL.md"
    result = repo([path], "## Proof\n")
    assert result.returncode == 1
    assert path in result.stdout
    assert REQUIRED_LINE in result.stdout


def test_skill_prefix_is_not_money_path(repo):
    result = repo(["skills/polymarket-copytrading-not-a-skill/notes.md"], "")
    assert result.returncode == 0, result.stderr


def test_money_paths_match_codeowners_and_governance():
    import check_money_trace as check

    spec = importlib.util.spec_from_file_location("governance", ROOT / "scripts/check-skill-governance.py")
    governance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(governance)
    trading_dirs = {
        path.parent.relative_to(ROOT).as_posix()
        for path in (ROOT / "skills").glob("*/clawhub.json")
        if governance.has_trading_entrypoint(path.parent, governance.load_json(path))
    }
    assert set(check.MONEY_SKILL_DIRS) == trading_dirs
    owned_modules = {
        line.split()[0].lstrip("/")
        for line in (ROOT / ".github/CODEOWNERS").read_text().splitlines()
        if line.startswith("/simmer_sdk/")
    }
    assert set(check.MONEY_FILES) == owned_modules | {"simmer_sdk/client.py"}
