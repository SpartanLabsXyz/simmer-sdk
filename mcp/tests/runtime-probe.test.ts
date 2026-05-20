import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { probeRuntime, resolvePythonBin } from "../src/runtime-probe.ts";

// Reset module-level cache between resolution tests via the exported function
// by passing a fresh processEnv each time (cache key is implicit — reset by
// reimporting; instead we test determinism of each resolution path directly).

test("probeRuntime detects python3 on this machine", async () => {
  const r = await probeRuntime();
  // python3 is required for the SDK — if missing the test environment is broken
  assert.ok(r.python3.detected, `python3 not found at ${r.python3.path}: ${r.python3.installHint}`);
  assert.ok(r.python3.version, "python3.version should be set");
  assert.ok(r.python3.path, "python3.path should be set");
});

test("probeRuntime returns ProbeResult shape for all three tools", async () => {
  const r = await probeRuntime();
  for (const key of ["python3", "simmerSdk", "git"] as const) {
    assert.equal(typeof r[key].detected, "boolean", `${key}.detected should be boolean`);
  }
});

test("resolvePythonBin honours SIMMER_MCP_PYTHON env override", async () => {
  // Reset cache by directly testing the env-var path with a fake env object.
  // We bypass the module cache by calling with an explicit processEnv.
  const fakeEnv = { SIMMER_MCP_PYTHON: "/custom/venv/bin/python" };
  // resolvePythonBin caches in module scope, so we test the env branch
  // by verifying: if SIMMER_MCP_PYTHON is set, it is returned verbatim.
  // (Integration-level: the function is pure for the env case before any which call.)
  const result = await resolvePythonBin(fakeEnv as Record<string, string | undefined>);
  assert.equal(result, "/custom/venv/bin/python", "SIMMER_MCP_PYTHON override not honoured");
});
