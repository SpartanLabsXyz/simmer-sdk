#!/usr/bin/env python3
"""Plan npm/PyPI publishes for the release workflow.

The CI publish-lag gate fails when a repo version is ahead of the public
registry. This helper uses the same registry readers and semver comparator, but
turns that state into GitHub Actions outputs so the release workflow can publish
only the packages that need it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import check_publish_lag

CLAWHUB_SKILL_URL = "https://clawhub.ai/api/v1/skills/{slug}"


@dataclass(frozen=True)
class Skill:
    slug: str
    path: str
    version: str
    published: bool
    publish_reason: str | None


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


def read_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(?P<body>.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise check_publish_lag.PublishLagError(f"{path} has no YAML frontmatter")

    values: dict[str, str] = {}
    block: str | None = None
    for line in match.group("body").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        is_top_level = line[0] not in " \t"
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if is_top_level:
            block = key if not value else None
            if value:
                values[key] = value
        elif block == "metadata" and key == "version":
            values[key] = value
    return values


def discover_skills(root: Path) -> list[Skill]:
    skills_dir = root / "skills"
    if not skills_dir.exists():
        return []

    skills: list[Skill] = []
    for clawhub_json in sorted(skills_dir.glob("*/clawhub.json")):
        skill_dir = clawhub_json.parent
        metadata = read_frontmatter(skill_dir / "SKILL.md")
        config = check_publish_lag.read_json(clawhub_json)
        skills.append(
            Skill(
                slug=metadata.get("name") or skill_dir.name,
                path=str(skill_dir.relative_to(root)),
                version=metadata["version"],
                published=(
                    metadata.get("published", "true").lower() != "false"
                    and config.get("published", True) is not False
                    and config.get("publish", True) is not False
                ),
                publish_reason=config.get("publish_reason"),
            )
        )
    return skills


def fetch_clawhub_latest(slug: str) -> str | None:
    request = urllib.request.Request(
        CLAWHUB_SKILL_URL.format(slug=slug),
        headers={"User-Agent": "simmer-publish-plan"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return str(payload["skill"]["tags"]["latest"])


def plan_skill(skill: Skill, published_version: str | None) -> dict[str, str] | None:
    if not skill.published:
        reason = f" ({skill.publish_reason})" if skill.publish_reason else ""
        print(f"{skill.path}: publish skipped; hold flag set{reason}")
        return None

    if published_version is None:
        print(f"{skill.path}: publish needed ({skill.version}; not found on ClawHub)")
        return {
            "slug": skill.slug,
            "path": skill.path,
            "version": skill.version,
            "published_version": "",
        }

    comparison = check_publish_lag.compare_versions(skill.version, published_version)
    if comparison > 0:
        print(f"{skill.path}: publish needed ({skill.version} > {published_version})")
        return {
            "slug": skill.slug,
            "path": skill.path,
            "version": skill.version,
            "published_version": published_version,
        }
    if comparison < 0:
        print(
            f"{skill.path}: ClawHub is newer ({published_version} > {skill.version}); no publish"
        )
        return None

    print(f"{skill.path}: already published at {skill.version}")
    return None


def plan_skills(root: Path) -> list[dict[str, str]]:
    publish_list: list[dict[str, str]] = []
    for skill in discover_skills(root):
        published_version = None if not skill.published else fetch_clawhub_latest(skill.slug)
        planned = plan_skill(skill, published_version)
        if planned:
            publish_list.append(planned)
            print(
                "Dry-run command: "
                f"npx -y clawhub skill publish <tmp> --owner simmer "
                f"--slug {skill.slug} --version {skill.version}"
            )
    return publish_list


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
    clawhub_publish_list = plan_skills(root)
    clawhub_publish_matrix = {"include": clawhub_publish_list}

    emit("npm_repo_version", npm_repo_version)
    emit("npm_published_version", npm_published_version)
    emit("npm_publish_needed", github_bool(npm_publish_needed))
    emit("pypi_repo_version", pypi_repo_version)
    emit("pypi_published_version", pypi_published_version)
    emit("pypi_publish_needed", github_bool(pypi_publish_needed))
    emit("clawhub_publish_needed", github_bool(bool(clawhub_publish_list)))
    emit("clawhub_publish_matrix", json.dumps(clawhub_publish_matrix, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except check_publish_lag.PublishLagError as exc:
        print(f"::error::{exc}")
        raise SystemExit(1)
