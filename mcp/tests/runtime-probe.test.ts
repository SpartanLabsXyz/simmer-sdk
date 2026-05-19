import { test } from "node:test";
import assert from "node:assert/strict";
import { probeRuntime } from "../src/runtime-probe.ts";

test("probeRuntime detects python3 on this machine", async () => {
  const r = await probeRuntime();
  // python3 is required for the SDK — if missing the test environment is broken
  assert.ok(r.python3.detected, `python3 not found: ${r.python3.installHint}`);
  assert.ok(r.python3.version, "python3.version should be set");
});

test("probeRuntime returns ProbeResult shape for all three tools", async () => {
  const r = await probeRuntime();
  for (const key of ["python3", "simmerSdk", "git"] as const) {
    assert.equal(typeof r[key].detected, "boolean", `${key}.detected should be boolean`);
  }
});
