#!/usr/bin/env python3
"""Gate `npx clawhub publish` behind an explicit typed confirmation.

Public ClawHub publishing runs arbitrary remote npm code (`npx clawhub@<pin>`)
and makes a skill folder installable by anyone. This script refuses to shell
out to `npx` unless the caller has typed the exact skill slug back — a `-y` or
a bare "yes" does not count, since those get echoed through pipelines without
a human ever reading the prompt.

Usage:
    python scripts/confirm_publish.py <skill_path> --slug <slug> --version <version> [--clawhub-version X.Y.Z]

Confirmation source:
    Prompts on stdin: "Type the skill slug to publish it publicly: ". There is no
    env var or flag bypass — an agent holding credentials cannot self-authorize a
    public publish; a human must be at the keyboard typing the slug. stdin must
    also be an interactive TTY: a piped or redirected stdin (e.g.
    `printf 'slug\n' | python confirm_publish.py ...`) is rejected outright, since
    an agent can supply that without a human ever reading the prompt.

--clawhub-version must be an exact three-part version (e.g. 0.23.3). Mutable
tags like "latest", "next", or "beta" are rejected, since accepting them would
recreate the unpinned-npx hole this script exists to close.

Exit codes:
    0 — published (npx clawhub publish invoked)
    2 — confirmation withheld or did not match; no publish attempted
    2 — --clawhub-version is not an exact pinned version; no publish attempted
"""
import argparse
import re
import subprocess
import sys

DEFAULT_CLAWHUB_VERSION = "0.23.3"
PINNED_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def get_confirmation(slug):
    if not sys.stdin.isatty():
        return None
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

    if not PINNED_VERSION_RE.match(args.clawhub_version):
        print(
            f"ABORT: --clawhub-version '{args.clawhub_version}' is not an exact pinned "
            f"version (expected X.Y.Z, e.g. '{DEFAULT_CLAWHUB_VERSION}') — no publish attempted.",
            file=sys.stderr,
        )
        return 2

    confirmation = get_confirmation(args.slug)
    if confirmation is None:
        print(
            "ABORT: stdin is not an interactive TTY — a piped confirmation cannot "
            "authorize a public publish; run this from a real terminal.",
            file=sys.stderr,
        )
        return 2
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
