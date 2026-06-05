#!/usr/bin/env python3
"""Governance checks for ClawHub skill PRs."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "skills"
APPROVAL_MARKERS = {
    "sensitive-skill-approved",
    "skill-sensitive-approved",
}
TRADING_KEYWORDS = {
    "trade",
    "trader",
    "trading",
    "copytrading",
    "market-maker",
    "sniper",
    "scaler",
    "dca",
}
WALLET_ENV_HINTS = {
    "WALLET_PRIVATE_KEY",
    "SOLANA_PRIVATE_KEY",
    "EVM_PRIVATE_KEY",
}


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def load_json(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        return json.load(fh)


def changed_skill_dirs() -> set[Path]:
    base = os.environ.get("GITHUB_BASE_REF")
    if base:
        base_ref = f"origin/{base}"
        try:
            merge_base = run_git(["merge-base", "HEAD", base_ref])
            diff = run_git(["diff", "--name-only", f"{merge_base}..HEAD"])
        except subprocess.CalledProcessError:
            diff = run_git(["diff", "--name-only", "HEAD~1..HEAD"])
    else:
        diff = run_git(["diff", "--name-only", "--cached"])
        if not diff:
            diff = run_git(["diff", "--name-only", "HEAD~1..HEAD"])

    dirs: set[Path] = set()
    for line in diff.splitlines():
        parts = Path(line).parts
        if len(parts) >= 2 and parts[0] == "skills":
            dirs.add(SKILLS_DIR / parts[1])
    return dirs


def pr_has_sensitive_approval_marker() -> bool:
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        return False

    try:
        event = load_json(Path(event_path))
    except (OSError, json.JSONDecodeError):
        return False

    pr = event.get("pull_request") or {}
    labels = {
        str(label.get("name", "")).strip().lower()
        for label in pr.get("labels", [])
    }
    if labels & APPROVAL_MARKERS:
        return True

    body = str(pr.get("body") or "").lower()
    return any(f"{marker}:" in body or f"{marker}=true" in body for marker in APPROVAL_MARKERS)


def manifest_env_names(manifest: dict[str, Any]) -> set[str]:
    names = set()
    for env_var in manifest.get("envVars") or []:
        name = env_var.get("name") if isinstance(env_var, dict) else None
        if name:
            names.add(str(name))
    for name in (manifest.get("requires") or {}).get("env") or []:
        names.add(str(name))
    return names


def has_trading_entrypoint(skill_dir: Path, manifest: dict[str, Any]) -> bool:
    entrypoint = (manifest.get("automaton") or {}).get("entrypoint")
    if not entrypoint:
        return False

    env_names = manifest_env_names(manifest)
    if env_names & WALLET_ENV_HINTS:
        return True

    haystack = " ".join(
        str(value).lower()
        for value in [
            skill_dir.name,
            entrypoint,
            manifest.get("name"),
            manifest.get("description"),
            manifest.get("category"),
            " ".join(manifest.get("tags") or []),
        ]
        if value
    )
    return any(keyword in haystack for keyword in TRADING_KEYWORDS)


def validate_skill(skill_dir: Path, pr_approved: bool) -> list[str]:
    errors: list[str] = []
    manifest_path = skill_dir / "clawhub.json"
    if not manifest_path.exists():
        return errors

    try:
        manifest = load_json(manifest_path)
    except json.JSONDecodeError as exc:
        return [f"{manifest_path.relative_to(ROOT)} is invalid JSON: {exc}"]

    sensitivity = manifest.get("sensitivity", "standard")
    if sensitivity not in {"standard", "sensitive"}:
        errors.append(
            f"{manifest_path.relative_to(ROOT)} sensitivity must be 'standard' or 'sensitive'"
        )

    if sensitivity == "sensitive":
        if not str(manifest.get("sensitivity_reason") or "").strip():
            errors.append(
                f"{manifest_path.relative_to(ROOT)} is sensitive but lacks sensitivity_reason"
            )
        if manifest.get("sensitivity_approved") is not True and not pr_approved:
            errors.append(
                f"{skill_dir.relative_to(ROOT)} is sensitivity=sensitive; add the "
                "'sensitive-skill-approved' PR label or set sensitivity_approved=true "
                "after Adrian/CTO approval"
            )

    if has_trading_entrypoint(skill_dir, manifest) and not (skill_dir / "DISCLAIMER.md").exists():
        errors.append(
            f"{skill_dir.relative_to(ROOT)} has a trading entrypoint but no DISCLAIMER.md"
        )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="check every skills/*/clawhub.json")
    args = parser.parse_args()

    skill_dirs = (
        {path.parent for path in SKILLS_DIR.glob("*/clawhub.json")}
        if args.all
        else changed_skill_dirs()
    )
    pr_approved = pr_has_sensitive_approval_marker()

    errors: list[str] = []
    for skill_dir in sorted(skill_dirs):
        errors.extend(validate_skill(skill_dir, pr_approved))

    if errors:
        print("Skill governance check failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    checked = ", ".join(str(path.relative_to(ROOT)) for path in sorted(skill_dirs)) or "none"
    print(f"Skill governance check passed: {checked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
