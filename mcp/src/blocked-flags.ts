/**
 * BLOCKED_FLAG_PATTERNS — flags that would bypass the structured dry_run safety gate.
 * Codex review 2026-05-19 CRITICAL #5: strengthened to catch =-form and space-separated forms.
 *
 * Two-pass filter handles both `=`-style (--live=true) and space-separated (--live true).
 */

const BLOCKED_FLAG_PATTERNS: RegExp[] = [
  /^--(live|no-dry-run|no-dry|real|production)(=.+)?$/i,
  /^--mode=(live|prod|production|real)$/i,
  /^--venue=.*(live|prod|real)/i,
];

const BLOCKED_KEY_AWAITING_VALUE_PATTERNS: RegExp[] = [
  /^--mode$/i,
  /^--venue$/i,
];

const LIVE_VALUES = /^(live|prod|production|real|true|1|yes)$/i;

export function isBlockedFlag(arg: string): boolean {
  if (typeof arg !== "string") return false;
  return BLOCKED_FLAG_PATTERNS.some((p) => p.test(arg));
}

export function filterBlockedFlags(args: unknown[]): string[] {
  const out: string[] = [];
  let skipNext = false;
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (skipNext) { skipNext = false; continue; }
    if (typeof a !== "string") continue;

    if (isBlockedFlag(a)) {
      const next = args[i + 1];
      if (typeof next === "string" && LIVE_VALUES.test(next)) skipNext = true;
      continue;
    }

    if (BLOCKED_KEY_AWAITING_VALUE_PATTERNS.some((p) => p.test(a))) {
      const next = args[i + 1];
      if (typeof next === "string" && LIVE_VALUES.test(next)) {
        skipNext = true;
        continue;
      }
    }

    out.push(a);
  }
  return out;
}
