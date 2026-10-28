#!/usr/bin/env python3
"""Persist validated ClawHub upload results for the verification job."""

import argparse
import json
from pathlib import Path
import sys
import tempfile


def validate_receipt(payload, slug: str, version: str) -> str:
    if (not isinstance(payload, dict) or payload.get("ok") is not True
            or payload.get("slug") != slug or payload.get("version") != version):
        raise ValueError("invalid ClawHub publish receipt or slug/version mismatch")
    publication = payload.get("publicationStatus")
    if ((payload.get("status"), publication)
            not in (("pending-publication", "pending"), ("published", "published"))):
        raise ValueError("ClawHub publish receipt has no recognized publication status")
    return publication


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("slug")
    parser.add_argument("version")
    args = parser.parse_args()
    temporary = None
    try:
        payload = json.load(sys.stdin)
        validate_receipt(payload, args.slug, args.version)
        filename = f"{args.slug}-{args.version}.json"
        if Path(filename).name != filename:
            raise ValueError("invalid receipt filename")
        args.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=args.directory, delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        temporary.replace(args.directory / filename)
    except (OSError, ValueError) as exc:
        message = str(exc).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error::Cannot record ClawHub publish receipt: {message}")
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
