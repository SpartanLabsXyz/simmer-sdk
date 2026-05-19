/**
 * runSkillProcess — Node subprocess wrapper for Python skill scripts.
 * Uses argv-shaped invocation (no shell), custom env, and configurable output cap.
 * Separate from autoresearch's runCommand (which uses bash -c <string>).
 */

import { spawn } from "node:child_process";

export interface SkillRunOptions {
  file: string;
  args: string[];
  env: Record<string, string>;
  timeoutMs: number;
  maxOutputBytes?: number;
}

export interface SkillRunResult {
  exitCode: number | null;
  signal: NodeJS.Signals | null;
  stdout: string;
  stderr: string;
  durationMs: number;
  timedOut: boolean;
}

const DEFAULT_MAX_BYTES = 1 * 1024 * 1024;

export function runSkillProcess(opts: SkillRunOptions): Promise<SkillRunResult> {
  const maxBytes = opts.maxOutputBytes ?? DEFAULT_MAX_BYTES;
  const t0 = Date.now();
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    let stdoutBytes = 0;
    let stderrBytes = 0;

    const child = spawn(opts.file, opts.args, {
      env: opts.env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    const timer = setTimeout(() => {
      timedOut = true;
      try { child.kill("SIGTERM"); } catch { /* */ }
      setTimeout(() => { try { child.kill("SIGKILL"); } catch { /* */ } }, 5000);
    }, opts.timeoutMs);

    child.stdout.on("data", (chunk: Buffer) => {
      stdoutBytes += chunk.length;
      if (stdoutBytes <= maxBytes) stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderrBytes += chunk.length;
      if (stderrBytes <= maxBytes) stderr += chunk.toString();
    });

    child.on("close", (code, signal) => {
      clearTimeout(timer);
      resolve({
        exitCode: code,
        signal,
        stdout,
        stderr,
        durationMs: Date.now() - t0,
        timedOut,
      });
    });

    child.on("error", (err) => {
      clearTimeout(timer);
      resolve({
        exitCode: null,
        signal: null,
        stdout,
        stderr: stderr + "\n" + err.message,
        durationMs: Date.now() - t0,
        timedOut,
      });
    });
  });
}
