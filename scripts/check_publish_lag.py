#!/usr/bin/env python3
"""Fail if repo package versions are ahead of the public registries.

Local use:
  python3 scripts/check_publish_lag.py
"""

from __future__ import annotations

import argparse
import json
import re
import time
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


RETRY_INTERVAL_SECS = 10
RETRY_MAX_SECS = 120


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


def check_package_with_retry(
    label: str,
    repo_version: str,
    fetch_fn: "Callable[[], str]",
    retry: bool,
) -> bool:
    """Check registry version, retrying if the package was just published.

    Without retry the CDN-cached registry read can return stale data for several
    seconds after a publish succeeds, producing a false "version ahead" failure.
    With retry=True, poll up to RETRY_MAX_SECS before giving up.
    """
    if not retry:
        return check_package(label, repo_version, fetch_fn())

    deadline = time.monotonic() + RETRY_MAX_SECS
    attempt = 0
    while True:
        attempt += 1
        published_version = fetch_fn()
        comparison = compare_versions(repo_version, published_version)
        if comparison <= 0:
            # registry caught up (match) or is ahead (newer release elsewhere)
            return check_package(label, repo_version, published_version)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # timed out — emit the real error
            return check_package(label, repo_version, published_version)
        wait = min(RETRY_INTERVAL_SECS, remaining)
        print(
            f"{label}: registry at {published_version}, waiting for {repo_version} to propagate"
            f" (attempt {attempt}, retrying in {int(wait)}s)…"
        )
        time.sleep(wait)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--npm-published-version", help="Override npm latest version for tests.")
    parser.add_argument("--pypi-published-version", help="Override PyPI latest version for tests.")
    parser.add_argument(
        "--retry-npm",
        action="store_true",
        default=False,
        help=(
            "Retry npm registry check until repo version appears (up to "
            f"{RETRY_MAX_SECS}s). Use when this CI run just published the npm package."
        ),
    )
    parser.add_argument(
        "--retry-pypi",
        action="store_true",
        default=False,
        help=(
            "Retry PyPI registry check until repo version appears (up to "
            f"{RETRY_MAX_SECS}s). Use when this CI run just published the PyPI package."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()

    npm_repo_version = read_npm_repo_version(root)
    pypi_repo_version = read_pypi_repo_version(root)

    retry_npm = getattr(args, "retry_npm", False)
    retry_pypi = getattr(args, "retry_pypi", False)

    if args.npm_published_version:
        npm_ok = check_package(NPM_PACKAGE, npm_repo_version, args.npm_published_version)
    else:
        npm_ok = check_package_with_retry(
            NPM_PACKAGE, npm_repo_version, lambda: fetch_npm_latest(NPM_PACKAGE), retry=retry_npm
        )

    if args.pypi_published_version:
        pypi_ok = check_package(PYPI_PACKAGE, pypi_repo_version, args.pypi_published_version)
    else:
        pypi_ok = check_package_with_retry(
            PYPI_PACKAGE,
            pypi_repo_version,
            lambda: fetch_pypi_latest(PYPI_PACKAGE),
            retry=retry_pypi,
        )

    return 0 if (npm_ok and pypi_ok) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishLagError as exc:
        print(f"::error::{exc}")
        raise SystemExit(1)
