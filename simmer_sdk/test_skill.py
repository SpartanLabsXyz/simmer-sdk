"""
Skill Test Harness — validates trading skills work end-to-end.

Usage:
    python -m simmer_sdk.test_skill <slug>
    python -m simmer_sdk.test_skill <slug> --stage sim
    python -m simmer_sdk.test_skill <slug> --stage paper
    python -m simmer_sdk.test_skill <slug> --stage live --budget 5 --approve-live
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def find_skills_root():
    """Find the skills/ directory. Checks CWD, then walks up to find simmer-sdk root."""
    cwd = Path.cwd()
    # Check if we're in the simmer-sdk repo
    for parent in [cwd] + list(cwd.parents):
        skills_dir = parent / "skills"
        if skills_dir.is_dir() and (parent / "simmer_sdk").is_dir():
            return skills_dir
    # Fallback: check if skills/ exists in cwd
    if (cwd / "skills").is_dir():
        return cwd / "skills"
    raise FileNotFoundError("Cannot find skills/ directory. Run from simmer-sdk repo root.")


def discover_skill(skill_dir: Path) -> dict:
    """Read clawhub.json and validate the skill is runnable."""
    skill_dir = Path(skill_dir)
    clawhub_path = skill_dir / "clawhub.json"

    if not clawhub_path.exists():
        raise FileNotFoundError(f"clawhub.json not found in {skill_dir}")

    with open(clawhub_path) as f:
        clawhub = json.load(f)

    automaton = clawhub.get("automaton", {})
    entrypoint = automaton.get("entrypoint")
    if not entrypoint:
        raise ValueError(f"No automaton.entrypoint in {clawhub_path}")

    script_path = skill_dir / entrypoint
    if not script_path.exists():
        raise FileNotFoundError(f"{entrypoint} not found in {skill_dir}")

    return {
        "entrypoint": entrypoint,
        "script_path": script_path,
        "skill_dir": skill_dir,
        "clawhub": clawhub,
        "requires_env": clawhub.get("requires", {}).get("env", []),
    }


def run_skill_stage(skill_info: dict, stage: str, timeout: int = 60,
                    budget: float = 5.0, api_key: str = None,
                    test_api_key: str = None) -> dict:
    """
    Run a skill in a specific stage and capture results.

    Returns dict with: status, candidates_found, trades_attempted,
    trades_executed, errors, warnings, stdout, stderr, exit_code, duration_s
    """
    env = os.environ.copy()
    env["AUTOMATON_MANAGED"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    # Stage-specific env
    args = []
    if stage == "sim":
        env["TRADING_VENUE"] = "sim"
        # Don't pass --live; skill runs in dry-run on sim
    elif stage == "paper":
        env["TRADING_VENUE"] = "polymarket"
        # Don't pass --live; skill defaults to dry-run (paper mode)
    elif stage == "live":
        env["TRADING_VENUE"] = "polymarket"
        args.append("--live")
        # Use test API key for live stage
        if test_api_key:
            env["SIMMER_API_KEY"] = test_api_key
        # Cap budget
        env["AUTOMATON_MAX_BET"] = str(budget)
        env["SIMMER_NEH_DAILY_BUDGET_USD"] = str(budget)
        env["SIMMER_NEH_MAX_TRADES_PER_RUN"] = "1"

    # Ensure API key is set
    if api_key and "SIMMER_API_KEY" not in env:
        env["SIMMER_API_KEY"] = api_key
    elif api_key and stage != "live":
        env["SIMMER_API_KEY"] = api_key

    cmd = [sys.executable, str(skill_info["script_path"])] + args

    result = {
        "stage": stage,
        "status": "fail",
        "candidates_found": 0,
        "trades_attempted": 0,
        "trades_executed": 0,
        "amount_usd": 0.0,
        "errors": [],
        "warnings": [],
        "stdout": "",
        "stderr": "",
        "exit_code": -1,
        "duration_s": 0.0,
    }

    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(skill_info["skill_dir"]),
            env=env,
        )
        result["stdout"] = proc.stdout
        result["stderr"] = proc.stderr
        result["exit_code"] = proc.returncode
    except subprocess.TimeoutExpired:
        result["errors"].append(f"Timeout after {timeout}s")
        result["duration_s"] = timeout
        return result
    except Exception as e:
        result["errors"].append(str(e))
        return result

    result["duration_s"] = round(time.monotonic() - start, 1)

    # Parse automaton JSON from stdout
    automaton_data = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith('{"automaton"'):
            try:
                parsed = json.loads(line)
                automaton_data = parsed.get("automaton", {})
            except json.JSONDecodeError:
                pass

    if automaton_data:
        result["candidates_found"] = automaton_data.get("signals", 0)
        result["trades_attempted"] = automaton_data.get("trades_attempted", 0)
        result["trades_executed"] = automaton_data.get("trades_executed", 0)
        result["amount_usd"] = automaton_data.get("amount_usd", 0.0)
        if automaton_data.get("execution_errors"):
            result["errors"].extend(automaton_data["execution_errors"])
        if automaton_data.get("skip_reason"):
            result["warnings"].append(automaton_data["skip_reason"])

    # Determine pass/fail
    if proc.returncode != 0:
        result["errors"].append(f"Exit code {proc.returncode}")
        if proc.stderr.strip():
            # Extract last few lines of stderr for error context
            stderr_lines = proc.stderr.strip().splitlines()[-5:]
            result["errors"].append("\n".join(stderr_lines))
        return result

    if stage == "sim":
        # Sim: pass if no crash. Warn if no candidates/trades.
        result["status"] = "pass"
        if result["candidates_found"] == 0:
            result["warnings"].append("no candidates found on sim venue — market catalog may not include target markets")
        if result["trades_attempted"] == 0:
            result["warnings"].append("no trades attempted on sim venue")
    elif stage in ("paper", "live"):
        # Paper/live: require candidates and trades
        if result["candidates_found"] == 0:
            result["errors"].append("no candidates found — skill discovery logic may be broken")
        elif result["trades_executed"] == 0:
            result["errors"].append("no trades executed — trade logic may be broken")
        else:
            result["status"] = "pass"

    return result


def format_report(skill_slug: str, stage_results: dict) -> dict:
    """Build the final structured report."""
    has_fail = any(r["status"] == "fail" for r in stage_results.values())
    has_warn = any(r.get("warnings") for r in stage_results.values())
    has_skip = any(r["status"] == "skip" for r in stage_results.values())

    if has_fail:
        recommendation = "FAIL — fix errors before publishing"
    elif has_skip:
        recommendation = "INCOMPLETE — live stage not yet run"
    elif has_warn:
        recommendation = "PASS with warnings"
    else:
        recommendation = "PASS — ready for ClawHub publish"

    report = {
        "skill": skill_slug,
        "stages": {},
        "recommendation": recommendation,
    }

    for stage_name, r in stage_results.items():
        report["stages"][stage_name] = {
            "status": r["status"],
            "candidates_found": r["candidates_found"],
            "trades_attempted": r["trades_attempted"],
            "trades_executed": r["trades_executed"],
            "duration_s": r["duration_s"],
            "errors": r["errors"],
        }
        if r.get("warnings"):
            report["stages"][stage_name]["warnings"] = r["warnings"]

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Skill Test Harness — validate trading skills before publishing",
        prog="python -m simmer_sdk.test_skill",
    )
    parser.add_argument("slug", help="Skill slug (directory name under skills/)")
    parser.add_argument("--stage", action="append", choices=["sim", "paper", "live"],
                        help="Run specific stage(s). Default: sim + paper")
    parser.add_argument("--budget", type=float, default=5.0,
                        help="Budget cap for live stage in USDC (default: 5)")
    parser.add_argument("--approve-live", action="store_true",
                        help="Required flag to run live stage")
    parser.add_argument("--timeout", type=int, default=60,
                        help="Timeout per stage in seconds (default: 60)")
    parser.add_argument("--json", action="store_true",
                        help="Output results as JSON only")
    parser.add_argument("--skills-dir", type=str, default=None,
                        help="Override skills directory path")
    args = parser.parse_args()

    # Resolve skill directory
    if args.skills_dir:
        skills_root = Path(args.skills_dir)
    else:
        skills_root = find_skills_root()

    skill_dir = skills_root / args.slug
    if not skill_dir.is_dir():
        print(f"Error: skill '{args.slug}' not found in {skills_root}", file=sys.stderr)
        sys.exit(1)

    # Discover skill
    try:
        skill_info = discover_skill(skill_dir)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Determine stages
    stages = args.stage or ["sim", "paper"]
    if "live" in stages and not args.approve_live:
        print("Error: --approve-live flag required for live stage", file=sys.stderr)
        sys.exit(1)

    # Check API key
    api_key = os.environ.get("SIMMER_API_KEY")
    test_api_key = os.environ.get("SIMMER_TEST_API_KEY")
    if not api_key:
        print("Error: SIMMER_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)
    if "live" in stages and not test_api_key:
        print("Error: SIMMER_TEST_API_KEY environment variable not set for live stage", file=sys.stderr)
        sys.exit(1)

    # Run stages
    stage_results = {}
    if not args.json:
        print(f"\n{'='*50}")
        print(f"  Skill Test Harness: {args.slug}")
        print(f"{'='*50}\n")

    for stage in stages:
        if not args.json:
            print(f"  Stage: {stage}")
            print(f"  {'-'*40}")

        timeout = max(args.timeout, 120) if stage == "live" else args.timeout

        r = run_skill_stage(
            skill_info, stage,
            timeout=timeout,
            budget=args.budget,
            api_key=api_key,
            test_api_key=test_api_key,
        )
        stage_results[stage] = r

        if not args.json:
            icon = "PASS" if r["status"] == "pass" else "FAIL"
            print(f"  [{icon}] {r['status'].upper()} ({r['duration_s']}s)")
            print(f"     candidates={r['candidates_found']} attempted={r['trades_attempted']} executed={r['trades_executed']}")
            if r["warnings"]:
                for w in r["warnings"]:
                    print(f"     WARNING: {w}")
            if r["errors"]:
                for e in r["errors"]:
                    print(f"     ERROR: {e}")
            print()

        # Stop on failure (don't run paper if sim crashes)
        if r["status"] == "fail":
            if not args.json:
                print(f"  Stopping — {stage} stage failed.\n")
            break

    # Add skipped stages
    for stage in stages:
        if stage not in stage_results:
            stage_results[stage] = {"status": "skip", "reason": "prior stage failed",
                                     "candidates_found": 0, "trades_attempted": 0,
                                     "trades_executed": 0, "duration_s": 0, "errors": []}

    report = format_report(args.slug, stage_results)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"  Recommendation: {report['recommendation']}")
        print()

    # Exit code: 0 for pass, 1 for fail
    sys.exit(0 if "FAIL" not in report["recommendation"] else 1)


if __name__ == "__main__":
    main()
