import importlib.util
import sys
import time
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_publish_lag", ROOT / "scripts" / "check_publish_lag.py"
)
assert SPEC is not None
assert SPEC.loader is not None
check_publish_lag = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_publish_lag
SPEC.loader.exec_module(check_publish_lag)


def write_package_files(root: Path, npm_version: str, pypi_version: str) -> None:
    (root / "mcp").mkdir()
    (root / "mcp" / "package.json").write_text(
        f'{{"name": "simmer-mcp", "version": "{npm_version}"}}',
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "simmer-sdk"\nversion = "{pypi_version}"\n',
        encoding="utf-8",
    )


def patch_git_for_mcp_bump_check(
    monkeypatch: pytest.MonkeyPatch,
    *,
    changed: str,
    previous_npm_version: str,
) -> None:
    def fake_diff_base(root):
        return "origin/main"

    def fake_changed_paths(root):
        return [changed]

    def fake_git_file_at_ref(root, ref, path):
        assert ref == "origin/main"
        if path == check_publish_lag.MCP_PACKAGE_JSON:
            return f'{{"name": "simmer-mcp", "version": "{previous_npm_version}"}}'
        return None

    monkeypatch.setattr(check_publish_lag, "diff_base_ref", fake_diff_base)
    monkeypatch.setattr(check_publish_lag, "changed_paths", fake_changed_paths)
    monkeypatch.setattr(check_publish_lag, "git_file_at_ref", fake_git_file_at_ref)


def test_compare_versions_is_semver_numeric() -> None:
    assert check_publish_lag.compare_versions("3.4.10", "3.4.9") > 0
    assert check_publish_lag.compare_versions("3.4.9", "3.4.10") < 0
    assert check_publish_lag.compare_versions("3.4.10", "3.4.10") == 0


def test_compare_versions_handles_prereleases() -> None:
    assert check_publish_lag.compare_versions("1.0.0", "1.0.0-rc.1") > 0
    assert check_publish_lag.compare_versions("1.0.0-beta.2", "1.0.0-beta.10") < 0


@pytest.mark.parametrize(
    ("repo_version", "published_version", "expected"),
    [
        ("3.4.4", "3.4.4", True),
        ("3.4.4", "3.4.5", True),
        ("3.4.10", "3.4.9", False),
    ],
)
def test_check_package_outcomes(repo_version: str, published_version: str, expected: bool) -> None:
    assert check_publish_lag.check_package("simmer-mcp", repo_version, published_version) is expected


def make_args(**kwargs):
    defaults = {
        "root": None,
        "npm_published_version": None,
        "pypi_published_version": None,
        "retry_npm": False,
        "retry_pypi": False,
    }
    defaults.update(kwargs)
    return type("Args", (), defaults)()


def test_main_fails_when_either_registry_lags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_package_files(tmp_path, npm_version="3.4.10", pypi_version="0.20.0")
    monkeypatch.setattr(
        check_publish_lag,
        "parse_args",
        lambda: make_args(root=tmp_path, npm_published_version="3.4.9", pypi_published_version="0.20.0"),
    )

    assert check_publish_lag.main() == 1


def test_main_passes_when_repo_matches_or_is_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_package_files(tmp_path, npm_version="3.4.4", pypi_version="0.20.0")
    monkeypatch.setattr(
        check_publish_lag,
        "parse_args",
        lambda: make_args(root=tmp_path, npm_published_version="3.4.4", pypi_published_version="0.20.1"),
    )

    assert check_publish_lag.main() == 0


def test_retry_succeeds_once_registry_propagates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry loop should pass once the registry returns the expected version."""
    write_package_files(tmp_path, npm_version="3.4.10", pypi_version="0.20.0")

    call_count = 0

    def fake_fetch_npm(pkg):
        nonlocal call_count
        call_count += 1
        return "3.4.10" if call_count >= 2 else "3.4.9"

    monkeypatch.setattr(check_publish_lag, "fetch_npm_latest", fake_fetch_npm)
    monkeypatch.setattr(check_publish_lag, "fetch_pypi_latest", lambda pkg: "0.20.0")
    monkeypatch.setattr(
        check_publish_lag,
        "time",
        types.SimpleNamespace(monotonic=time.monotonic, sleep=lambda s: None),
    )
    monkeypatch.setattr(
        check_publish_lag,
        "parse_args",
        lambda: make_args(root=tmp_path, retry_npm=True, retry_pypi=False),
    )

    assert check_publish_lag.main() == 0
    assert call_count == 2


def test_retry_fails_after_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry loop should give up and fail if registry never propagates within deadline."""
    write_package_files(tmp_path, npm_version="3.4.10", pypi_version="0.20.0")

    # Make time.monotonic() advance past the deadline on the second call.
    start = time.monotonic()
    tick = 0

    def fake_monotonic():
        nonlocal tick
        tick += 1
        # First call: sets deadline. Subsequent calls: already past deadline.
        return start if tick == 1 else start + check_publish_lag.RETRY_MAX_SECS + 1

    monkeypatch.setattr(check_publish_lag, "fetch_npm_latest", lambda pkg: "3.4.9")
    monkeypatch.setattr(check_publish_lag, "fetch_pypi_latest", lambda pkg: "0.20.0")
    monkeypatch.setattr(
        check_publish_lag,
        "time",
        types.SimpleNamespace(monotonic=fake_monotonic, sleep=lambda s: None),
    )
    monkeypatch.setattr(
        check_publish_lag,
        "parse_args",
        lambda: make_args(root=tmp_path, retry_npm=True, retry_pypi=False),
    )

    assert check_publish_lag.main() == 1


def test_no_retry_reads_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without retry flag, registry is read exactly once regardless of result."""
    write_package_files(tmp_path, npm_version="3.4.10", pypi_version="0.20.0")

    fetch_count = 0

    def fake_fetch_npm(pkg):
        nonlocal fetch_count
        fetch_count += 1
        return "3.4.9"  # stale — would need retry to pass

    monkeypatch.setattr(check_publish_lag, "fetch_npm_latest", fake_fetch_npm)
    monkeypatch.setattr(check_publish_lag, "fetch_pypi_latest", lambda pkg: "0.20.0")
    monkeypatch.setattr(
        check_publish_lag,
        "parse_args",
        lambda: make_args(root=tmp_path, retry_npm=False, retry_pypi=False),
    )

    result = check_publish_lag.main()
    assert fetch_count == 1
    assert result == 1


def test_mcp_source_change_requires_npm_version_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_package_files(tmp_path, npm_version="3.5.1", pypi_version="0.20.0")
    patch_git_for_mcp_bump_check(
        monkeypatch,
        changed="mcp/src/tool-registry.ts",
        previous_npm_version="3.5.1",
    )
    monkeypatch.setattr(
        check_publish_lag,
        "parse_args",
        lambda: make_args(root=tmp_path, npm_published_version="3.5.1", pypi_published_version="0.20.0"),
    )

    assert check_publish_lag.main() == 1


def test_mcp_source_change_allows_npm_version_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_package_files(tmp_path, npm_version="3.5.2", pypi_version="0.20.0")
    patch_git_for_mcp_bump_check(
        monkeypatch,
        changed="mcp/src/tool-registry.ts",
        previous_npm_version="3.5.1",
    )
    monkeypatch.setattr(
        check_publish_lag,
        "parse_args",
        lambda: make_args(root=tmp_path, npm_published_version="3.5.2", pypi_published_version="0.20.0"),
    )

    assert check_publish_lag.main() == 0


def test_non_mcp_package_input_does_not_require_npm_version_bump(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_package_files(tmp_path, npm_version="3.5.1", pypi_version="0.20.0")
    patch_git_for_mcp_bump_check(
        monkeypatch,
        changed="mcp/tests/mcp-protocol.test.ts",
        previous_npm_version="3.5.1",
    )
    monkeypatch.setattr(
        check_publish_lag,
        "parse_args",
        lambda: make_args(root=tmp_path, npm_published_version="3.5.1", pypi_published_version="0.20.0"),
    )

    assert check_publish_lag.main() == 0
