#!/usr/bin/env python3
"""Require money-trace snapshot proof for money-path PRs (FLEET 5)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


# Signing/redeem fence: .github/CODEOWNERS, plus its documented client.py gap.
MONEY_FILES = {
    "simmer_sdk/signing.py",
    "simmer_sdk/ows_utils.py",
    "simmer_sdk/solana_signing.py",
    "simmer_sdk/hyperliquid_signing.py",
    "simmer_sdk/dw_redeem.py",
    "simmer_sdk/client.py",
}
# Directories classified by check-skill-governance.py:has_trading_entrypoint.
# test_money_paths_match_codeowners_and_governance keeps this list in sync.
MONEY_SKILL_DIRS = {
    "skills/kalshi-weather-trader",
    "skills/polymarket-ai-divergence",
    "skills/polymarket-btc-up-down-trader",
    "skills/polymarket-combo-builder",
    "skills/polymarket-copytrading",
    "skills/polymarket-dca-eval-trader",
    "skills/polymarket-elon-tweets",
    "skills/polymarket-fast-loop",
    "skills/polymarket-fast-scaler",
    "skills/polymarket-market-maker",
    "skills/polymarket-mert-sniper",
    "skills/polymarket-nothing-ever-happens",
    "skills/polymarket-signal-sniper",
    "skills/polymarket-soccer-shock-ladder",
    "skills/polymarket-weather-trader",
    "skills/polymarket-worldcup-copytrader",
}
REQUIRED_LINE = "Money-trace: <repo-relative path>"


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], text=True)


def diff_base_ref() -> str:
    # Same merge-base selection as check-skill-governance.py; fail closed if absent.
    base = os.environ.get("GITHUB_BASE_REF")
    if base:
        return run_git(["merge-base", "HEAD", f"origin/{base}"]).strip()
    return "HEAD~1"


def changed_paths(base: str, head: str) -> list[str]:
    diff = run_git(["diff", "--name-only", "--no-renames", "-z", f"{base}...{head}"])
    return [path for path in diff.split("\0") if path]


def current_pull_request(event_path: str | None) -> dict:
    # Read the event payload directly, as in check-skill-governance.py.
    if not event_path:
        raise ValueError("GITHUB_EVENT_PATH or --event-path is required")
    with Path(event_path).open() as fh:
        event = json.load(fh)
    pr = event.get("pull_request")
    if not isinstance(pr, dict):
        raise ValueError("event payload must contain pull_request")
    return pr


def proof_money_trace(body: str) -> str | None:
    in_proof = False
    in_fence = False
    fence = ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            if not in_fence:
                in_fence, fence = True, stripped[:3]
            elif stripped.startswith(fence):
                in_fence = False
            continue
        if in_fence:
            continue
        if re.fullmatch(r"## Proof\s*#*\s*", line):
            in_proof = True
        elif re.match(r"#{1,2}(?:\s|$)", line):
            in_proof = False
        elif in_proof and line.startswith("Money-trace:"):
            return line.removeprefix("Money-trace:").strip()
    return None


def annotation(level: str, message: str) -> None:
    # Keep PR-controlled strings from introducing extra workflow commands.
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{level}::{escaped}")


def check(paths: list[str], body: str, head: str) -> int:
    money_paths = sorted(
        path for path in paths
        if path in MONEY_FILES or any(path.startswith(f"{directory}/") for directory in MONEY_SKILL_DIRS)
    )
    if not money_paths:
        print("No money paths changed; money-trace proof not required.")
        return 0

    value = proof_money_trace(body)
    waiver = re.fullmatch(r"n/a\s+-\s+(\S.*)", value or "")
    if waiver:
        annotation("warning", f"Money-trace waived; reviewer must read reason: {waiver.group(1)}")
        return 0

    if value:
        path = PurePosixPath(value)
        relative = not path.is_absolute() and ".." not in path.parts and path.as_posix() == value
        if relative and value in paths:
            result = subprocess.run(["git", "cat-file", "-t", f"{head}:{value}"], capture_output=True, text=True)
            if result.returncode == 0 and result.stdout.strip() == "blob":
                print(f"Money-trace snapshot: {value}")
                return 0

    annotation("error", "Money paths touched: " + ", ".join(money_paths))
    annotation("error", f"In ## Proof add this exact line, replacing the placeholder with a file added or changed in this PR and present at its head: {REQUIRED_LINE}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="diff base (defaults to the PR merge base)")
    parser.add_argument("--head", default="HEAD", help="PR head revision")
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH"))
    args = parser.parse_args()
    try:
        paths = changed_paths(args.base or diff_base_ref(), args.head)
        pr = current_pull_request(args.event_path)
        return check(paths, str(pr.get("body") or ""), args.head)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        annotation("error", f"Cannot check money-trace proof: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
