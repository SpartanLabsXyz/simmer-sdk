import { spawn } from "node:child_process";

export interface ProbeResult {
  detected: boolean;
  version?: string;
  path?: string;
  installHint?: string;
}

export interface RuntimeProbeResult {
  python3: ProbeResult;
  simmerSdk: ProbeResult;
  git: ProbeResult;
}

function runQuick(file: string, args: string[]): Promise<{ exitCode: number; stdout: string }> {
  return new Promise((resolve) => {
    let stdout = "";
    const child = spawn(file, args, { stdio: ["ignore", "pipe", "pipe"] });
    child.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    child.on("close", (code) => resolve({ exitCode: code ?? -1, stdout: stdout.trim() }));
    child.on("error", () => resolve({ exitCode: -1, stdout: "" }));
    setTimeout(() => { try { child.kill(); } catch { /* */ } }, 5000);
  });
}

/**
 * Resolve the Python binary to use, in priority order:
 *   1. SIMMER_MCP_PYTHON env var (explicit override — always checked first)
 *   2. `python` on the caller's PATH (active venv, pipx, etc.)
 *   3. `python3` on the caller's PATH
 *   4. Literal `python3` fallback
 *
 * The `which`-based PATH resolution is cached after the first call.
 * SIMMER_MCP_PYTHON is always evaluated fresh so it can be overridden at runtime.
 */
let _whichResolved: string | undefined;

export async function resolvePythonBin(
  processEnv: Record<string, string | undefined> = process.env,
): Promise<string> {
  // 1. Explicit override — always wins, no caching
  const explicit = processEnv.SIMMER_MCP_PYTHON;
  if (explicit) return explicit;

  // 2/3/4. PATH resolution — cached after first call
  if (_whichResolved !== undefined) return _whichResolved;

  for (const name of ["python", "python3"]) {
    const which = await runQuick("which", [name]);
    if (which.exitCode === 0 && which.stdout) {
      _whichResolved = which.stdout;
      return _whichResolved;
    }
  }

  _whichResolved = "python3";
  return _whichResolved;
}

export async function probeRuntime(): Promise<RuntimeProbeResult> {
  const pythonBin = await resolvePythonBin();
  const py = await runQuick(pythonBin, ["--version"]);
  const python3: ProbeResult = py.exitCode === 0
    ? { detected: true, version: py.stdout.replace(/^Python /, ""), path: pythonBin }
    : { detected: false, path: pythonBin, installHint: "Install: brew install python@3.11 (macOS) or apt install python3 (Debian/Ubuntu)" };

  let simmerSdk: ProbeResult = { detected: false, installHint: "Install: pip install simmer-sdk>=0.13.0" };
  if (python3.detected) {
    const sdk = await runQuick(pythonBin, ["-c", "import simmer_sdk; print(simmer_sdk.__version__)"]);
    if (sdk.exitCode === 0) {
      simmerSdk = { detected: true, version: sdk.stdout };
    }
  }

  const g = await runQuick("git", ["--version"]);
  const git: ProbeResult = g.exitCode === 0
    ? { detected: true, version: g.stdout.replace(/^git version /, "") }
    : { detected: false, installHint: "Install: brew install git or apt install git" };

  return { python3, simmerSdk, git };
}
