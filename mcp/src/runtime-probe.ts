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

export async function probeRuntime(): Promise<RuntimeProbeResult> {
  const py = await runQuick("python3", ["--version"]);
  const python3: ProbeResult = py.exitCode === 0
    ? { detected: true, version: py.stdout.replace(/^Python /, "") }
    : { detected: false, installHint: "Install: brew install python@3.11 (macOS) or apt install python3 (Debian/Ubuntu)" };

  let simmerSdk: ProbeResult = { detected: false, installHint: "Install: pip install simmer-sdk>=0.13.0" };
  if (python3.detected) {
    const sdk = await runQuick("python3", ["-c", "import simmer_sdk; print(simmer_sdk.__version__)"]);
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
