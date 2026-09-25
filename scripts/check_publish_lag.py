#!/usr/bin/env python3
"""Fail if repo package versions are ahead of the public registries.

Local use:
  python3 scripts/check_publish_lag.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NPM_PACKAGE = "simmer-mcp"
PYPI_PACKAGE = "simmer-sdk"
CLAWHUB_SKILL_URL = "https://clawhub.ai/api/v1/skills/{slug}"
MCP_PACKAGE_JSON = "mcp/package.json"
MCP_VERSION_GUARD_PATHS = (
    "mcp/package.json",
    "mcp/package-lock.json",
    "mcp/src/",
    "mcp/skills/",
    "mcp/bundled-skills/",
    "mcp/scripts/",
    "mcp/CORE_BUNDLE_DECISION.md",
    "mcp/LICENSE",
    "mcp/server.json",
    "mcp/tsconfig.json",
)


class PublishLagError(Exception):
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
    path: str
    version: str
    published: bool
    first_publish: bool
    publish_reason: str | None


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


def read_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(?P<body>.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise PublishLagError(f"{path} has no YAML frontmatter")

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


def discover_skills(root: Path) -> tuple[list[Skill], list[str]]:
    skills_dir = root / "skills"
    if not skills_dir.exists():
        return [], []

    skills: list[Skill] = []
    errors: list[str] = []
    for clawhub_json in sorted(skills_dir.glob("*/clawhub.json")):
        skill_dir = clawhub_json.parent
        try:
            metadata = read_frontmatter(skill_dir / "SKILL.md")
            config = read_json(clawhub_json)
            version = metadata.get("version")
            if not version:
                raise PublishLagError(f"{skill_dir / 'SKILL.md'} has no metadata.version")
            skills.append(
                Skill(
                    slug=metadata.get("name") or skill_dir.name,
                    path=str(skill_dir.relative_to(root)),
                    version=version,
                    published=(
                        metadata.get("published", "true").lower() != "false"
                        and config.get("published", True) is not False
                        and config.get("publish", True) is not False
                    ),
                    first_publish=config.get("first_publish", False) is True,
                    publish_reason=config.get("publish_reason"),
                )
            )
        except (OSError, json.JSONDecodeError, PublishLagError, KeyError) as exc:
            errors.append(f"{skill_dir.relative_to(root)}: {exc}")
    return skills, errors


def fetch_clawhub_latest(slug: str) -> str | None:
    payload = fetch_json(CLAWHUB_SKILL_URL.format(slug=slug))
    return str(payload["skill"]["tags"]["latest"])


def read_npm_repo_version(root: Path) -> str:
    package_json = read_json(root / "mcp" / "package.json")
    return str(package_json["version"])


def read_npm_version_from_package_json(package_json_text: str) -> str:
    return str(json.loads(package_json_text)["version"])


def run_git(root: Path, args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def diff_base_ref(root: Path) -> str:
    base = os.environ.get("GITHUB_BASE_REF")
    if base:
        base_ref = f"origin/{base}"
        try:
            return run_git(root, ["merge-base", "HEAD", base_ref])
        except subprocess.CalledProcessError:
            return "HEAD~1"

    return "HEAD~1"


def changed_paths(root: Path) -> list[str]:
    base_ref = diff_base_ref(root)
    try:
        if os.environ.get("GITHUB_BASE_REF"):
            diff = run_git(root, ["diff", "--name-only", f"{base_ref}..HEAD"])
        else:
            diff = run_git(root, ["diff", "--name-only", "--cached"])
            if not diff:
                diff = run_git(root, ["diff", "--name-only", f"{base_ref}..HEAD"])
    except subprocess.CalledProcessError:
        return []

    return [line for line in diff.splitlines() if line]


def git_file_at_ref(root: Path, ref: str, path: str) -> str | None:
    try:
        return run_git(root, ["show", f"{ref}:{path}"])
    except subprocess.CalledProcessError:
        return None


def is_mcp_package_input(path: str) -> bool:
    return any(path == guarded or path.startswith(guarded) for guarded in MCP_VERSION_GUARD_PATHS)


def validate_mcp_version_bump(root: Path, paths: list[str] | None = None) -> list[str]:
    paths = changed_paths(root) if paths is None else paths
    changed_mcp_inputs = sorted(path for path in paths if is_mcp_package_input(path))
    if not changed_mcp_inputs:
        return []

    base_ref = diff_base_ref(root)
    previous_package_json = git_file_at_ref(root, base_ref, MCP_PACKAGE_JSON)
    if previous_package_json is None:
        return []

    current_version = read_npm_repo_version(root)
    previous_version = read_npm_version_from_package_json(previous_package_json)
    if compare_versions(current_version, previous_version) > 0:
        return []

    sample = ", ".join(changed_mcp_inputs[:3])
    if len(changed_mcp_inputs) > 3:
        sample += ", ..."
    return [
        f"{NPM_PACKAGE} package inputs changed ({sample}) but {MCP_PACKAGE_JSON} "
        f"version is {current_version}, which is not greater than base version "
        f"{previous_version}; bump the npm package version so CI and published "
        "installs cannot name different artifacts with the same version"
    ]


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


def is_pull_request_event() -> bool:
    return os.environ.get("GITHUB_EVENT_NAME") == "pull_request"


def check_package(
    label: str,
    repo_version: str,
    published_version: str,
    is_pull_request: bool = False,
) -> bool:
    comparison = compare_versions(repo_version, published_version)
    if comparison > 0:
        if is_pull_request:
            print(
                f"{label}: repo version {repo_version} is ahead of published version "
                f"{published_version}; publish is pending on merge to main."
            )
            return True
        print(
            f"::error::{label} repo version {repo_version} is ahead of "
            f"published version {published_version}. Publish the package before merging."
        )
        return False
    if comparison < 0:
        if is_pull_request:
            print(
                f"::error::{label} published version {published_version} is ahead of "
                f"repo version {repo_version}. The registry has a version not reflected "
                "in git — investigate an out-of-band publish before merging."
            )
            return False
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
    is_pull_request: bool = False,
) -> bool:
    """Check registry version, retrying if the package was just published.

    Without retry the CDN-cached registry read can return stale data for several
    seconds after a publish succeeds, producing a false "version ahead" failure.
    With retry=True, poll up to RETRY_MAX_SECS before giving up.
    """
    if not retry:
        return check_package(label, repo_version, fetch_fn(), is_pull_request=is_pull_request)

    deadline = time.monotonic() + RETRY_MAX_SECS
    attempt = 0
    while True:
        attempt += 1
        published_version = fetch_fn()
        comparison = compare_versions(repo_version, published_version)
        if comparison <= 0:
            # registry caught up (match) or is ahead (newer release elsewhere)
            return check_package(label, repo_version, published_version, is_pull_request=is_pull_request)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # timed out — emit the real error
            return check_package(label, repo_version, published_version, is_pull_request=is_pull_request)
        wait = min(RETRY_INTERVAL_SECS, remaining)
        print(
            f"{label}: registry at {published_version}, waiting for {repo_version} to propagate"
            f" (attempt {attempt}, retrying in {int(wait)}s)…"
        )
        time.sleep(wait)


def check_skill(skill: Skill, published_version: str | None) -> bool:
    if not skill.published:
        reason = f" ({skill.publish_reason})" if skill.publish_reason else ""
        print(f"{skill.path}: ClawHub drift check skipped; hold flag set{reason}")
        return True

    if published_version is None:
        if not skill.first_publish:
            print(
                f"::warning::{skill.path} ({skill.slug}) is not found on ClawHub; "
                "skipping drift until clawhub.json sets first_publish true."
            )
            return True
        print(
            f"::error::{skill.slug} is {skill.version} in the repo and is not on ClawHub. "
            f"Run `scripts/publish.sh {skill.path}`."
        )
        return False

    comparison = compare_versions(skill.version, published_version)
    if comparison > 0:
        print(
            f"::error::{skill.slug} is {skill.version} in the repo and "
            f"{published_version} on ClawHub. Run `scripts/publish.sh {skill.path}`."
        )
        return False
    if comparison < 0:
        print(
            f"::warning::{skill.slug} is {skill.version} in the repo and "
            f"{published_version} on ClawHub; ClawHub is newer than git."
        )
        return True

    print(f"{skill.path}: ClawHub version matches repo version {skill.version}.")
    return True


def check_clawhub_skills(root: Path) -> bool:
    skills, discovery_errors = discover_skills(root)
    ok = True
    for error in discovery_errors:
        print(f"::error::Malformed skill metadata: {error}")
        ok = False

    for skill in skills:
        published_version: str | None
        if not skill.published:
            published_version = None
        else:
            try:
                published_version = fetch_clawhub_latest(skill.slug)
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    published_version = None
                else:
                    print(
                        f"::warning::Could not fetch ClawHub version for {skill.slug}: {exc}; "
                        "skipping ClawHub drift result for this run."
                    )
                    continue
            except urllib.error.URLError as exc:
                print(
                    f"::warning::Could not fetch ClawHub version for {skill.slug}: {exc}; "
                    "skipping ClawHub drift result for this run."
                )
                continue
            except TimeoutError as exc:
                print(
                    f"::warning::Could not fetch ClawHub version for {skill.slug}: {exc}; "
                    "skipping ClawHub drift result for this run."
                )
                continue
            except (KeyError, TypeError, ValueError) as exc:
                print(
                    f"::warning::Could not parse ClawHub version for {skill.slug}: {exc}; "
                    "skipping ClawHub drift result for this run."
                )
                continue
        ok = check_skill(skill, published_version) and ok
    return ok


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
    parser.add_argument(
        "--check-clawhub",
        action="store_true",
        default=False,
        help=(
            "Also alert on skill version drift between skills/*/SKILL.md and ClawHub. "
            "Use only from the scheduled/manual lag check, not as a PR gate."
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
    is_pull_request = is_pull_request_event()

    if args.npm_published_version:
        npm_ok = check_package(
            NPM_PACKAGE, npm_repo_version, args.npm_published_version, is_pull_request=is_pull_request
        )
    else:
        npm_ok = check_package_with_retry(
            NPM_PACKAGE,
            npm_repo_version,
            lambda: fetch_npm_latest(NPM_PACKAGE),
            retry=retry_npm,
            is_pull_request=is_pull_request,
        )

    if args.pypi_published_version:
        pypi_ok = check_package(
            PYPI_PACKAGE, pypi_repo_version, args.pypi_published_version, is_pull_request=is_pull_request
        )
    else:
        pypi_ok = check_package_with_retry(
            PYPI_PACKAGE,
            pypi_repo_version,
            lambda: fetch_pypi_latest(PYPI_PACKAGE),
            retry=retry_pypi,
            is_pull_request=is_pull_request,
        )

    errors = validate_mcp_version_bump(root)
    for error in errors:
        print(f"::error::{error}")

    clawhub_ok = check_clawhub_skills(root) if args.check_clawhub else True

    return 0 if (npm_ok and pypi_ok and clawhub_ok and not errors) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishLagError as exc:
        print(f"::error::{exc}")
        raise SystemExit(1)
