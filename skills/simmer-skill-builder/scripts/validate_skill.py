#!/usr/bin/env python3
"""
Validate a generated Simmer trading skill.

Checks structure, imports, required patterns, and SKILL.md frontmatter.

Usage:
    python validate_skill.py /path/to/skill-folder
"""

import os
import re
import sys
import json


def check(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    icon = "+" if passed else "x"
    msg = f"  [{icon}] {status}: {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)
    return passed


def warn(name, detail=""):
    msg = f"  [!] WARN: {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def validate_skill(skill_path):
    """Validate a skill folder. Returns True if all checks pass."""
    skill_path = os.path.abspath(skill_path)
    skill_name = os.path.basename(skill_path)
    print(f"\nValidating skill: {skill_name}")
    print(f"Path: {skill_path}")
    print("=" * 50)

    if not os.path.isdir(skill_path):
        print(f"  Error: {skill_path} is not a directory")
        return False

    all_passed = True

    # --- SKILL.md checks ---
    skill_md_path = os.path.join(skill_path, "SKILL.md")
    has_skill_md = os.path.isfile(skill_md_path)
    all_passed &= check("SKILL.md exists", has_skill_md)

    frontmatter = {}
    metadata = {}
    if has_skill_md:
        with open(skill_md_path) as f:
            content = f.read()

        # Parse YAML frontmatter
        fm_match = re.match(r'^---\s*\n(.*?)\n---', content, re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(1)
            # Use regex per-field to handle colons in JSON values
            for field in ["name", "displayName", "description", "version", "published"]:
                m = re.search(rf'^{field}:\s*(.+)$', fm_text, re.MULTILINE)
                if m:
                    frontmatter[field] = m.group(1).strip().strip('"').strip("'")
            # metadata is special — JSON value contains colons
            meta_match = re.search(r'^metadata:\s*(.+)$', fm_text, re.MULTILINE)
            if meta_match:
                frontmatter["metadata"] = meta_match.group(1).strip()

        has_name = bool(frontmatter.get("name"))
        all_passed &= check("Frontmatter has 'name'", has_name,
                            frontmatter.get("name", "missing"))

        has_desc = bool(frontmatter.get("description"))
        all_passed &= check("Frontmatter has 'description'", has_desc)

        # Parse metadata JSON
        meta_str = frontmatter.get("metadata", "")
        if meta_str:
            meta_str = meta_str.strip('"').strip("'")
            try:
                metadata = json.loads(meta_str)
            except json.JSONDecodeError:
                pass

        clawdbot = metadata.get("clawdbot", {})
        automaton = clawdbot.get("automaton", {})
        has_automaton = bool(automaton.get("entrypoint"))
        all_passed &= check("Automaton metadata with entrypoint", has_automaton,
                            automaton.get("entrypoint", "missing"))

        requires = clawdbot.get("requires", {})
        has_sdk_dep = "simmer-sdk" in requires.get("pip", [])
        all_passed &= check("Requires simmer-sdk", has_sdk_dep)

        has_api_key = "SIMMER_API_KEY" in requires.get("env", [])
        all_passed &= check("Requires SIMMER_API_KEY", has_api_key)

    # --- Entrypoint script checks ---
    entrypoint = metadata.get("clawdbot", {}).get("automaton", {}).get("entrypoint", "")
    if entrypoint:
        script_path = os.path.join(skill_path, entrypoint)
        has_script = os.path.isfile(script_path)
        all_passed &= check(f"Entrypoint '{entrypoint}' exists", has_script)

        if has_script:
            with open(script_path) as f:
                script = f.read()

            # Check imports
            has_sdk_import = bool(re.search(
                r'from\s+simmer_sdk\s+import|import\s+simmer_sdk', script))
            all_passed &= check("Imports simmer_sdk", has_sdk_import)

            # Check for bypass imports
            has_bypass = bool(re.search(
                r'from\s+py_clob_client|import\s+py_clob_client|'
                r'from\s+polymarket|import\s+polymarket', script))
            if has_bypass:
                all_passed &= check("No Polymarket bypass imports", False,
                                    "Remove py_clob_client/polymarket imports — use SimmerClient")
            else:
                check("No Polymarket bypass imports", True)

            # Check get_client pattern
            has_get_client = "def get_client" in script
            all_passed &= check("Has get_client() function", has_get_client)

            # Check TRADE_SOURCE
            has_source = bool(re.search(r'TRADE_SOURCE\s*=\s*["\']sdk:', script))
            all_passed &= check("Has TRADE_SOURCE = 'sdk:...'", has_source)

            # Check SIMMER_API_KEY reference
            has_api_key_ref = "SIMMER_API_KEY" in script
            all_passed &= check("References SIMMER_API_KEY", has_api_key_ref)

            # Check dry-run default
            has_live_flag = "--live" in script
            all_passed &= check("Has --live flag (dry-run default)", has_live_flag)

            # Check line buffering
            has_line_buffer = "line_buffering=True" in script
            all_passed &= check("Has line-buffered stdout", has_line_buffer)

            # Warnings (non-blocking)
            if "reasoning" not in script:
                warn("No 'reasoning' parameter in trades",
                     "Consider including trade reasoning for reputation")

            if "source=" not in script and "TRADE_SOURCE" not in script:
                warn("No source tagging found",
                     "Trades should include source= for portfolio tracking")

            if "--smart-sizing" not in script:
                warn("No --smart-sizing flag",
                     "Consider adding portfolio-based position sizing")

    # --- Summary ---
    print("\n" + "=" * 50)
    if all_passed:
        print(f"  All checks passed for '{skill_name}'")
    else:
        print(f"  Some checks FAILED for '{skill_name}'")

    return all_passed


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python validate_skill.py <path/to/skill-folder>")
        sys.exit(1)

    path = sys.argv[1]
    success = validate_skill(path)
    sys.exit(0 if success else 1)
