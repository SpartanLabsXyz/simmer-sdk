// Synced with pi-autoresearch @ 5a29db08 (2026-04-14)

import { runCommand } from "./runner.js";

export interface GitCommitResult {
  committed: boolean;
  message: string;
  newSha: string | null;
}

export async function gitAutoCommit(
  cwd: string,
  description: string,
  metricName: string,
  metric: number,
  secondaryMetrics: Record<string, number>,
): Promise<GitCommitResult> {
  const resultData: Record<string, unknown> = {
    status: "keep",
    [metricName || "metric"]: metric,
    ...secondaryMetrics,
  };
  const trailerJson = JSON.stringify(resultData);
  const commitMsg = `${description}\n\nResult: ${trailerJson}`;

  const result = await runCommand(
    `git add -A && git diff --cached --quiet && echo "NOTHING_TO_COMMIT" || git commit -m ${JSON.stringify(commitMsg)}`,
    { timeoutMs: 10000, cwd },
  );

  const output = (result.stdout + result.stderr).trim();

  if (output.includes("NOTHING_TO_COMMIT")) {
    return { committed: false, message: "nothing to commit", newSha: null };
  }

  if (!result.passed) {
    return { committed: false, message: `commit failed: ${output.slice(0, 200)}`, newSha: null };
  }

  let newSha: string | null = null;
  try {
    const shaResult = await runCommand("git rev-parse --short=7 HEAD", {
      timeoutMs: 5000,
      cwd,
    });
    const sha = shaResult.stdout.trim();
    if (sha && sha.length >= 7) newSha = sha;
  } catch {
    // Keep null
  }

  const firstLine = output.split("\n")[0] || "";
  return { committed: true, message: firstLine, newSha };
}

export async function gitRevert(cwd: string): Promise<string> {
  // Revert tracked files but preserve autoresearch state files
  const result = await runCommand(
    "git diff --name-only | grep -v '^autoresearch\\.' | xargs -r git checkout --",
    { timeoutMs: 10000, cwd },
  );
  // Also discard staged changes (same filter)
  const staged = await runCommand(
    "git diff --cached --name-only | grep -v '^autoresearch\\.' | xargs -r git checkout HEAD --",
    { timeoutMs: 10000, cwd },
  );
  return result.passed || staged.passed ? "reverted" : `revert failed: ${result.stderr.slice(0, 200)}`;
}
