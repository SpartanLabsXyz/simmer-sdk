#!/usr/bin/env python3
"""Validate a ClawHub publish preview before uploading the staged bundle."""

import argparse
import json
import subprocess


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle")
    parser.add_argument("slug")
    parser.add_argument("version")
    parser.add_argument("clawhub_version")
    args = parser.parse_args()
    try:
        result = subprocess.run(
            ["npx", f"clawhub@{args.clawhub_version}", "publish", args.bundle,
             "--version", args.version, "--owner", "simmer", "--slug", args.slug,
             "--dry-run", "--json"],
            check=True, stdout=subprocess.PIPE, text=True, timeout=120,
        )
        preview = json.loads(result.stdout)
        if (not isinstance(preview, dict) or preview.get("ok") is not True
                or preview.get("status") != "would-publish"
                or preview.get("slug") != args.slug or preview.get("version") != args.version):
            raise ValueError("unexpected preview result or slug/version mismatch")
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        message = str(exc).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error::ClawHub dry-run preflight failed: {message}")
        return 1
    print(f"ClawHub dry-run passed for {args.slug}@{args.version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
