#!/usr/bin/env python3
"""Gate `npx clawhub publish` behind an explicit typed confirmation.

Public ClawHub publishing runs arbitrary remote npm code (`npx clawhub@<pin>`)
and makes a skill folder installable by anyone. This script refuses to shell
out to `npx` unless the caller has typed the exact skill slug back — a `-y` or
a bare "yes" does not count, since those get echoed through pipelines without
a human ever reading the prompt.

Usage:
    python scripts/confirm_publish.py <skill_path> --slug <slug> --version <version> [--clawhub-version X.Y.Z]

Confirmation source (checked in order):
    1. $SIMMER_SKILL_BUILDER_CONFIRM env var, if set, must equal <slug> exactly.
    2. Otherwise prompts on stdin: "Type the skill slug to publish it publicly: ".

Exit codes:
    0 — published (npx clawhub publish invoked)
    2 — confirmation withheld or did not match; no publish attempted
"""
import argparse
import os
import subprocess
import sys

DEFAULT_CLAWHUB_VERSION = "0.23.3"


def get_confirmation(slug):
    env_confirm = os.environ.get("SIMMER_SKILL_BUILDER_CONFIRM")
    if env_confirm is not None:
        return env_confirm
    try:
        return input(f"Type the skill slug to publish it publicly ({slug}): ")
    except EOFError:
        return ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("skill_path")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--clawhub-version", default=DEFAULT_CLAWHUB_VERSION)
    args = parser.parse_args()

    confirmation = get_confirmation(args.slug)
    if confirmation.strip() != args.slug:
        print(
            f"ABORT: confirmation did not match slug '{args.slug}' — no publish attempted.",
            file=sys.stderr,
        )
        return 2

    cmd = [
        "npx",
        f"clawhub@{args.clawhub_version}",
        "publish",
        args.skill_path,
        "--slug",
        args.slug,
        "--version",
        args.version,
    ]
    print(f"Confirmed. Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
