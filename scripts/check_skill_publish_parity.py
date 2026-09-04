#!/usr/bin/env python3
"""Fail if publishable skills are ahead of ClawHub.

Local use:
  python3 scripts/check_skill_publish_parity.py
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLAWHUB_API = "https://clawhub.ai/api/v1"
REQUEST_TIMEOUT_SECS = 12
REQUEST_DELAY_SECS = 0.2


class SkillPublishParityError(Exception):
    pass


@dataclass(frozen=True)
class Semver:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str | int, ...]


@dataclass(frozen=True)
class Skill:
    slug: str
    version: str
    path: Path
    publishable: bool
    skip_reason: str = ""


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
        raise SkillPublishParityError(f"Unsupported version format: {version!r}")

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


def frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise SkillPublishParityError(f"{path}: missing YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SkillPublishParityError(f"{path}: unterminated YAML frontmatter")
    return parts[1]


def top_level_field(raw_frontmatter: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.*)$", raw_frontmatter, re.MULTILINE)
    if not match:
        return ""
    return match.group(1).strip().strip("\"'")


def nested_field(raw_frontmatter: str, block: str, key: str) -> str:
    in_block = False
    for line in raw_frontmatter.splitlines():
        if re.match(rf"^{re.escape(block)}:\s*$", line):
            in_block = True
            continue
        if in_block and line and not line[0].isspace():
            in_block = False
        if not in_block:
            continue
        match = re.match(rf"^\s+{re.escape(key)}:\s*(.*)$", line)
        if match:
            return match.group(1).strip().strip("\"'")
    return ""


def manifest_publishable(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return True, ""
    payload = json.loads(path.read_text(encoding="utf-8"))
    publish_value = payload.get("publish", payload.get("published", True))
    if publish_value is False:
        return False, str(payload.get("publish_reason") or "clawhub.json publish flag is false")
    return True, ""


def read_skills(root: Path) -> list[Skill]:
    skill_paths = sorted((root / "skills").glob("*/SKILL.md"))
    if not skill_paths:
        raise SkillPublishParityError(f"No skills found under {root / 'skills'}")

    skills: list[Skill] = []
    for skill_md in skill_paths:
        raw = frontmatter(skill_md)
        slug = top_level_field(raw, "name")
        version = top_level_field(raw, "version") or nested_field(raw, "metadata", "version")
        published_field = top_level_field(raw, "published").lower()
        publishable = published_field != "false"
        skip_reason = "SKILL.md published flag is false" if not publishable else ""
        manifest_ok, manifest_reason = manifest_publishable(skill_md.parent / "clawhub.json")
        if not manifest_ok:
            publishable = False
            skip_reason = manifest_reason

        if not slug:
            raise SkillPublishParityError(f"{skill_md}: missing name")
        if not version:
            raise SkillPublishParityError(f"{skill_md}: missing version or metadata.version")
        parse_semver(version)
        skills.append(
            Skill(
                slug=slug,
                version=version,
                path=skill_md.relative_to(root),
                publishable=publishable,
                skip_reason=skip_reason,
            )
        )
    return skills


def fetch_clawhub_version(slug: str, api_base: str = CLAWHUB_API) -> str | None:
    quoted_slug = urllib.parse.quote(slug)
    url = f"{api_base.rstrip('/')}/skills/{quoted_slug}"
    request = urllib.request.Request(url, headers={"User-Agent": "simmer-skill-publish-parity-check"})
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECS) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    latest = payload.get("latestVersion") or {}
    version = latest.get("version")
    return str(version) if version else None


def parse_version_overrides(raw_overrides: list[str]) -> dict[str, str | None]:
    overrides: dict[str, str | None] = {}
    for raw in raw_overrides:
        if "=" not in raw:
            raise SkillPublishParityError(f"Invalid override {raw!r}; expected slug=version or slug=missing")
        slug, value = raw.split("=", 1)
        if not slug:
            raise SkillPublishParityError(f"Invalid override {raw!r}; missing slug")
        overrides[slug] = None if value == "missing" else value
    return overrides


def check_skill(skill: Skill, published_version: str | None) -> bool:
    if not skill.publishable:
        print(f"{skill.slug}: skipped ({skill.skip_reason})")
        return True

    if not published_version:
        print(f"::error file={skill.path}::{skill.slug} repo version {skill.version} is not published on ClawHub.")
        return False

    comparison = compare_versions(skill.version, published_version)
    if comparison > 0:
        print(
            f"::error file={skill.path}::{skill.slug} repo version {skill.version} is ahead of "
            f"ClawHub version {published_version}. Publish the skill before merging."
        )
        return False
    if comparison < 0:
        print(
            f"{skill.slug}: repo version {skill.version} is behind ClawHub version "
            f"{published_version}; treating as already published/newer registry state."
        )
        return True

    print(f"{skill.slug}: repo version {skill.version} matches ClawHub version {published_version}.")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--api-base", default=CLAWHUB_API)
    parser.add_argument(
        "--published-version",
        action="append",
        default=[],
        metavar="SLUG=VERSION",
        help="Override a ClawHub version for tests. Use SLUG=missing for a 404/missing skill.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    overrides = parse_version_overrides(args.published_version)
    skills = read_skills(root)
    ok = True

    for index, skill in enumerate(skills):
        if skill.slug in overrides:
            published_version = overrides[skill.slug]
        else:
            published_version = fetch_clawhub_version(skill.slug, args.api_base)
            if index < len(skills) - 1:
                time.sleep(REQUEST_DELAY_SECS)
        ok = check_skill(skill, published_version) and ok

    return 0 if ok else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SkillPublishParityError as exc:
        print(f"::error::{exc}")
        raise SystemExit(1)
