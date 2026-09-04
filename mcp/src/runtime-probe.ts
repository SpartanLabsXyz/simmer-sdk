import { spawn } from "node:child_process";

/**
 * Minimum simmer-sdk the bundled `preflight` skill needs — it calls APIs that do not
 * exist below this. Keep in sync with `bundled-skills/preflight/clawhub.json`.
 */
export const SDK_MIN_VERSION = "0.17.13";
const SDK_INSTALL_HINT = `Install: pip install 'simmer-sdk>=${SDK_MIN_VERSION}'`;

/**
 * True when `version` is >= SDK_MIN_VERSION.
 *
 * Fails OPEN on anything unparseable (empty, a dev build, a git describe string): an
 * SDK that is actually present should not be reported missing just because we could not
 * read its version. Only a version we can read AND that is genuinely below the floor
 * gets rejected.
 */
export function meetsSdkFloor(version: string, floor = SDK_MIN_VERSION): boolean {
  const numeric = (v: string) => v.trim().match(/^\d+(?:\.\d+)*/)?.[0] ?? "";
  const got = numeric(version);
  if (got === "") return true;
  const a = got.split(".").map(Number);
  const b = numeric(floor).split(".").map(Number);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    if (x !== y) return x > y;
  }
  return true;
}

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

  let simmerSdk: ProbeResult = { detected: false, installHint: SDK_INSTALL_HINT };
  if (python3.detected) {
    const sdk = await runQuick(pythonBin, ["-c", "import simmer_sdk; print(simmer_sdk.__version__)"]);
    if (sdk.exitCode === 0) {
      // Import success is not enough: `preflight` calls APIs added in SDK_MIN_VERSION and
      // only catches ImportError, so an older-but-importable SDK crashes with a traceback
      // instead of reporting the install hint. Treat below-floor as not usable.
      simmerSdk = meetsSdkFloor(sdk.stdout)
        ? { detected: true, version: sdk.stdout }
        : { detected: false, version: sdk.stdout, installHint: SDK_INSTALL_HINT };
    }
  }

  const g = await runQuick("git", ["--version"]);
  const git: ProbeResult = g.exitCode === 0
    ? { detected: true, version: g.stdout.replace(/^git version /, "") }
    : { detected: false, installHint: "Install: brew install git or apt install git" };

  return { python3, simmerSdk, git };
}
