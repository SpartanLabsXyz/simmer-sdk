#!/usr/bin/env python3
"""Check ClawHub skill publish parity for skills/ bundles.

Local use:
  python3 scripts/check_skill_publish_parity.py
"""

from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from check_publish_lag import compare_versions


ROOT = Path(__file__).resolve().parents[1]
CLAWHUB_SKILL_URL = "https://clawhub.ai/api/v1/skills/{slug}"


@dataclass(frozen=True)
class Skill:
    slug: str
    path: str
    version: str
    published: bool
    publish_reason: str | None


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(?P<body>.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise ValueError(f"{path} has no YAML frontmatter")

    values: dict[str, str] = {}
    block: str | None = None
    for line in match.group("body").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        is_top_level = line[0] not in " \t"
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if is_top_level:
            block = key if not value else None
            if value:
                values[key] = value
        elif block == "metadata" and key == "version" and "version" not in values:
            values[key] = value
    return values


def discover_skills(root: Path) -> list[Skill]:
    skills: list[Skill] = []
    for clawhub_json in sorted((root / "skills").glob("*/clawhub.json")):
        skill_dir = clawhub_json.parent
        metadata = read_frontmatter(skill_dir / "SKILL.md")
        config = read_json(clawhub_json)
        skills.append(
            Skill(
                slug=metadata.get("name") or skill_dir.name,
                path=str(skill_dir.relative_to(root)),
                version=metadata["version"],
                published=config.get("published", True) is not False,
                publish_reason=config.get("publish_reason"),
            )
        )
    return skills


def fetch_clawhub_latest(slug: str) -> str | None:
    request = urllib.request.Request(
        CLAWHUB_SKILL_URL.format(slug=slug),
        headers={"User-Agent": "simmer-skill-publish-parity-check"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return str(payload["skill"]["tags"]["latest"])


def check_skills(skills: list[Skill]) -> list[str]:
    errors: list[str] = []
    for skill in skills:
        if not skill.published:
            reason = f" ({skill.publish_reason})" if skill.publish_reason else ""
            print(f"{skill.path}: skipped; marked published=false{reason}")
            continue

        published_version = fetch_clawhub_latest(skill.slug)
        if published_version is None:
            print(
                f"::error::{skill.path}: ClawHub slug {skill.slug!r} returned 404 "
                f"for repo version {skill.version}"
            )
            errors.append(f"{skill.path} ({skill.slug}): not found on ClawHub")
            continue

        comparison = compare_versions(skill.version, published_version)
        if comparison > 0:
            print(
                f"::error::{skill.path} ({skill.slug}): repo version {skill.version} is ahead of "
                f"ClawHub version {published_version}"
            )
            errors.append(
                f"{skill.path} ({skill.slug}): repo {skill.version} > ClawHub {published_version}"
            )
        elif comparison < 0:
            print(
                f"::error::{skill.path} ({skill.slug}): ClawHub version {published_version} "
                f"is ahead of repo version {skill.version}"
            )
            errors.append(
                f"{skill.path} ({skill.slug}): ClawHub {published_version} > repo {skill.version}"
            )
        else:
            print(f"{skill.path} ({skill.slug}): repo version {skill.version} matches ClawHub")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors = check_skills(discover_skills(args.root))
    if errors:
        print("\nSkill publish parity failures:")
        for error in errors:
            print(f"- {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
