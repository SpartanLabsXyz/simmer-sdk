#!/usr/bin/env python3
"""Fail if repo package versions are ahead of the public registries.

Local use:
  python3 scripts/check_publish_lag.py
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NPM_PACKAGE = "simmer-mcp"
PYPI_PACKAGE = "simmer-sdk"


class PublishLagError(Exception):
    pass


@dataclass(frozen=True)
class Semver:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str | int, ...]


SEMVER_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z.-]+))?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


def parse_semver(version: str) -> Semver:
    match = SEMVER_RE.match(version.strip())
    if not match:
        raise PublishLagError(f"Unsupported version format: {version!r}")

    prerelease: list[str | int] = []
    raw_prerelease = match.group("prerelease")
    if raw_prerelease:
        for part in raw_prerelease.split("."):
            prerelease.append(int(part) if part.isdigit() else part)

    return Semver(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        prerelease=tuple(prerelease),
    )


def compare_versions(left: str, right: str) -> int:
    left_version = parse_semver(left)
    right_version = parse_semver(right)

    left_core = (left_version.major, left_version.minor, left_version.patch)
    right_core = (right_version.major, right_version.minor, right_version.patch)
    if left_core != right_core:
        return 1 if left_core > right_core else -1

    if left_version.prerelease == right_version.prerelease:
        return 0
    if not left_version.prerelease:
        return 1
    if not right_version.prerelease:
        return -1

    for left_part, right_part in zip(left_version.prerelease, right_version.prerelease):
        if left_part == right_part:
            continue
        if isinstance(left_part, int) and isinstance(right_part, int):
            return 1 if left_part > right_part else -1
        if isinstance(left_part, int):
            return -1
        if isinstance(right_part, int):
            return 1
        return 1 if left_part > right_part else -1

    if len(left_version.prerelease) == len(right_version.prerelease):
        return 0
    return 1 if len(left_version.prerelease) > len(right_version.prerelease) else -1


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_npm_repo_version(root: Path) -> str:
    package_json = read_json(root / "mcp" / "package.json")
    return str(package_json["version"])


def read_pypi_repo_version(root: Path) -> str:
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    in_project = False
    for line in pyproject.splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if in_project and stripped.startswith("["):
            break
        if in_project and stripped.startswith("version"):
            _, value = stripped.split("=", 1)
            return value.strip().strip('"').strip("'")
    raise PublishLagError("Could not find [project].version in pyproject.toml")


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "simmer-publish-lag-check"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def fetch_npm_latest(package_name: str) -> str:
    payload = fetch_json(f"https://registry.npmjs.org/{package_name}")
    return str(payload["dist-tags"]["latest"])


def fetch_pypi_latest(package_name: str) -> str:
    payload = fetch_json(f"https://pypi.org/pypi/{package_name}/json")
    return str(payload["info"]["version"])


def check_package(label: str, repo_version: str, published_version: str) -> bool:
    comparison = compare_versions(repo_version, published_version)
    if comparison > 0:
        print(
            f"::error::{label} repo version {repo_version} is ahead of "
            f"published version {published_version}. Publish the package before merging."
        )
        return False
    if comparison < 0:
        print(
            f"{label}: repo version {repo_version} is behind published version "
            f"{published_version}; treating as already published/newer registry state."
        )
        return True

    print(f"{label}: repo version {repo_version} matches published version {published_version}.")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--npm-published-version", help="Override npm latest version for tests.")
    parser.add_argument("--pypi-published-version", help="Override PyPI latest version for tests.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()

    npm_repo_version = read_npm_repo_version(root)
    pypi_repo_version = read_pypi_repo_version(root)
    npm_published_version = args.npm_published_version or fetch_npm_latest(NPM_PACKAGE)
    pypi_published_version = args.pypi_published_version or fetch_pypi_latest(PYPI_PACKAGE)

    ok = True
    ok &= check_package(NPM_PACKAGE, npm_repo_version, npm_published_version)
    ok &= check_package(PYPI_PACKAGE, pypi_repo_version, pypi_published_version)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishLagError as exc:
        print(f"::error::{exc}")
        raise SystemExit(1)
