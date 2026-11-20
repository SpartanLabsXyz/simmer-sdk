#!/usr/bin/env python3
"""Bundle the fee helper so held skills work with the published SDK.

Run with --check to verify the standalone copies match the canonical source.
"""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = (
    "polymarket-fast-loop", "polymarket-fast-scaler",
    "polymarket-market-maker", "polymarket-mert-sniper",
)
HEADER = "# Generated from simmer_sdk/polymarket_fees.py by scripts/sync_skill_fee_helpers.py.\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = HEADER + (ROOT / "simmer_sdk/polymarket_fees.py").read_text()
    stale = []
    for slug in SKILLS:
        target = ROOT / "skills" / slug / "_polymarket_fees.py"
        if args.check:
            if not target.exists() or target.read_text() != expected:
                stale.append(slug)
        else:
            target.write_text(expected)
    if stale:
        parser.exit(1, "Stale fee helpers: " + ", ".join(stale) + "\n")
    print(f"Fee helpers {'in sync' if args.check else 'bundled'}: {len(SKILLS)} skills")


if __name__ == "__main__":
    main()
