// Synced with pi-autoresearch @ 5a29db08 (2026-04-14)

import { spawn } from "node:child_process";
import type { RunResult } from "./types.js";

const MAX_OUTPUT_LINES = 80;

export function runCommand(
  command: string,
  opts: { timeoutMs: number; cwd: string },
): Promise<RunResult> {
  return new Promise((resolve) => {
    const t0 = Date.now();
    let stdout = "";
    let stderr = "";
    let killed = false;

    const proc = spawn("bash", ["-c", command], {
      cwd: opts.cwd,
      env: process.env,
      stdio: ["ignore", "pipe", "pipe"],
    });

    const timer = setTimeout(() => {
      killed = true;
      proc.kill("SIGTERM");
      setTimeout(() => {
        try { proc.kill("SIGKILL"); } catch { /* already dead */ }
      }, 5000);
    }, opts.timeoutMs);

    proc.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
    proc.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });

    proc.on("close", (code) => {
      clearTimeout(timer);
      const durationSeconds = (Date.now() - t0) / 1000;
      const timedOut = killed;
      const passed = code === 0 && !killed;

      resolve({
        exitCode: code,
        stdout: stdout.split("\n").slice(-MAX_OUTPUT_LINES).join("\n"),
        stderr: stderr.split("\n").slice(-MAX_OUTPUT_LINES).join("\n"),
        durationSeconds,
        passed,
        timedOut,
        killed,
      });
    });

    proc.on("error", (err) => {
      clearTimeout(timer);
      const durationSeconds = (Date.now() - t0) / 1000;
      resolve({
        exitCode: null,
        stdout,
        stderr: err.message,
        durationSeconds,
        passed: false,
        timedOut: false,
        killed: false,
      });
    });
  });
}
