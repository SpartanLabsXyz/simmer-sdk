import importlib.util
import sys
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


def test_main_fails_when_either_registry_lags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_package_files(tmp_path, npm_version="3.4.10", pypi_version="0.20.0")
    monkeypatch.setattr(
        check_publish_lag,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "root": tmp_path,
                "npm_published_version": "3.4.9",
                "pypi_published_version": "0.20.0",
            },
        )(),
    )

    assert check_publish_lag.main() == 1


def test_main_passes_when_repo_matches_or_is_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_package_files(tmp_path, npm_version="3.4.4", pypi_version="0.20.0")
    monkeypatch.setattr(
        check_publish_lag,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "root": tmp_path,
                "npm_published_version": "3.4.4",
                "pypi_published_version": "0.20.1",
            },
        )(),
    )

    assert check_publish_lag.main() == 0
