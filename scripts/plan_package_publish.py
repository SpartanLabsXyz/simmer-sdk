#!/usr/bin/env python3
"""Plan npm/PyPI publishes for the release workflow.

The CI publish-lag gate fails when a repo version is ahead of the public
registry. This helper uses the same registry readers and semver comparator, but
turns that state into GitHub Actions outputs so the release workflow can publish
only the packages that need it.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import check_publish_lag


def github_bool(value: bool) -> str:
    return "true" if value else "false"


def emit(name: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    line = f"{name}={value}\n"
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(line)
    else:
        print(line, end="")


def plan_package(label: str, repo_version: str, published_version: str) -> bool:
    comparison = check_publish_lag.compare_versions(repo_version, published_version)
    if comparison > 0:
        print(f"{label}: publish needed ({repo_version} > {published_version})")
        return True
    if comparison < 0:
        print(f"{label}: registry is newer ({published_version} > {repo_version}); no publish")
        return False
    print(f"{label}: already published at {repo_version}")
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=check_publish_lag.ROOT)
    parser.add_argument("--npm-published-version", help="Override npm latest version for tests.")
    parser.add_argument("--pypi-published-version", help="Override PyPI latest version for tests.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()

    npm_repo_version = check_publish_lag.read_npm_repo_version(root)
    pypi_repo_version = check_publish_lag.read_pypi_repo_version(root)
    npm_published_version = args.npm_published_version or check_publish_lag.fetch_npm_latest(
        check_publish_lag.NPM_PACKAGE
    )
    pypi_published_version = args.pypi_published_version or check_publish_lag.fetch_pypi_latest(
        check_publish_lag.PYPI_PACKAGE
    )

    npm_publish_needed = plan_package(
        check_publish_lag.NPM_PACKAGE, npm_repo_version, npm_published_version
    )
    pypi_publish_needed = plan_package(
        check_publish_lag.PYPI_PACKAGE, pypi_repo_version, pypi_published_version
    )

    emit("npm_repo_version", npm_repo_version)
    emit("npm_published_version", npm_published_version)
    emit("npm_publish_needed", github_bool(npm_publish_needed))
    emit("pypi_repo_version", pypi_repo_version)
    emit("pypi_published_version", pypi_published_version)
    emit("pypi_publish_needed", github_bool(pypi_publish_needed))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except check_publish_lag.PublishLagError as exc:
        print(f"::error::{exc}")
        raise SystemExit(1)
