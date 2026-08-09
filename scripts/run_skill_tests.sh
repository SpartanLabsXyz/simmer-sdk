#!/usr/bin/env bash
# Run every skill's test suite, each in its own pytest process.
#
# Why per-skill processes instead of one `pytest skills/`:
#
#   Skills are standalone bundles that get published to ClawHub and run on an
#   agent host, not an importable package tree. Their tests reflect that — they
#   stub the SDK by assigning into sys.modules so the skill module can be
#   imported without a live SIMMER_API_KEY. Several do it permanently at import
#   time rather than through a `patch.dict` context manager, e.g.
#
#       sys.modules["simmer_sdk"] = MagicMock()
#
#   In a single shared pytest process that stub leaks into every test collected
#   afterwards, so a suite needing the REAL simmer_sdk (skills/preflight) dies
#   at collection with "'simmer_sdk' is not a package". A one-process run also
#   hits basename collisions between same-named test files in different skills.
#
#   Isolating per skill fixes both without editing 17 test files across skills
#   owned by different workstreams, and it matches how skills actually execute.
#
# Usage: scripts/run_skill_tests.sh [skills_dir]
set -uo pipefail

SKILLS_DIR="${1:-skills}"

if [ ! -d "$SKILLS_DIR" ]; then
  echo "No '$SKILLS_DIR' directory — nothing to do."
  exit 0
fi

failed_suites=()
passed=0
total_tests=0

while IFS= read -r tests_dir; do
  skill="$(basename "$(dirname "$tests_dir")")"
  output="$(python -m pytest "$tests_dir" -q 2>&1)"
  status=$?
  summary="$(printf '%s\n' "$output" | tail -1)"

  if [ $status -eq 0 ]; then
    printf '  ok   %-40s %s\n' "$skill" "$summary"
    passed=$((passed + 1))
    n="$(printf '%s' "$summary" | grep -oE '[0-9]+ passed' | grep -oE '[0-9]+' || true)"
    total_tests=$((total_tests + ${n:-0}))
  else
    printf '  FAIL %-40s %s\n' "$skill" "$summary"
    printf '%s\n' "$output" | tail -40 | sed 's/^/       /'
    failed_suites+=("$skill")
  fi
done < <(find "$SKILLS_DIR" -type d -name tests | sort)

echo
if [ ${#failed_suites[@]} -gt 0 ]; then
  echo "Skill tests FAILED in ${#failed_suites[@]} suite(s): ${failed_suites[*]}"
  exit 1
fi

echo "Skill tests passed: $passed suite(s), $total_tests test(s)."
