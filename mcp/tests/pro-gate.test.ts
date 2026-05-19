/**
 * Pro-gating test: mock 403 from checkPro() relays cleanly as BackendError.
 * Task 26 (SIM-2056).
 */
import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { SimmerApi } from "../dist/api.js";
import { BackendError } from "../dist/errors.js";

type FetchFn = typeof global.fetch;
let savedFetch: FetchFn;

function mockFetch(fn: FetchFn) {
  // @ts-expect-error global override for testing
  global.fetch = fn;
}

/**
 * Simulates assertProForRunExperiment — the inline helper in mcp-server.ts.
 * Extracted here so we can test the exact relay pattern without spawning a server.
 */
async function assertProForRunExperiment(api: SimmerApi): Promise<void> {
  await api.checkPro();
}

/**
 * Simulates the run_experiment handler's Pro-gate wrapper in mcp-server.ts.
 */
async function runExperimentGate(api: SimmerApi): Promise<{ isError?: boolean; content: Array<{ type: string; text: string }> }> {
  try {
    await assertProForRunExperiment(api);
    return { content: [{ type: "text", text: "✅ Would run experiment" }] };
  } catch (e) {
    if (e instanceof BackendError) return e.toMcpResponse();
    throw e;
  }
}

describe("Pro-gate relay", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  it("returns BackendError MCP response when API returns 403", async () => {
    mockFetch(async () =>
      new Response(JSON.stringify({ detail: "Autoresearch requires a Pro plan." }), { status: 403 })
    );
    const api = new SimmerApi("sk_free", "https://api.simmer.markets", "2.3.0");
    const resp = await runExperimentGate(api);
    assert.equal(resp.isError, true, "should be isError=true");
    assert.match(resp.content[0].text, /Pro/i, "should mention Pro");
    assert.match(resp.content[0].text, /simmer\.markets\/pro/i, "should include upgrade URL");
  });

  it("passes through (no error) when API returns 200 (Pro user)", async () => {
    mockFetch(async () => new Response(JSON.stringify({ last_experiment_number: 0 }), { status: 200 }));
    const api = new SimmerApi("sk_pro", "https://api.simmer.markets", "2.3.0");
    const resp = await runExperimentGate(api);
    assert.ok(!resp.isError, "should not be error for Pro user");
    assert.match(resp.content[0].text, /Would run experiment/);
  });

  it("passes through when API is unreachable (graceful degradation)", async () => {
    mockFetch(async () => { throw new Error("ECONNREFUSED"); });
    const api = new SimmerApi("sk_unknown", "https://api.simmer.markets", "2.3.0");
    const resp = await runExperimentGate(api);
    // Network errors must not block — the Pro check is best-effort
    assert.ok(!resp.isError, "should not error when API unreachable");
  });

  it("status_code meta is set correctly on 403 response", async () => {
    mockFetch(async () =>
      new Response(JSON.stringify({ detail: "Autoresearch requires a Pro plan." }), { status: 403 })
    );
    const api = new SimmerApi("sk_free", "https://api.simmer.markets", "2.3.0");
    const resp = await runExperimentGate(api) as ReturnType<BackendError["toMcpResponse"]>;
    assert.equal(resp._meta?.status_code, 403, "status_code should be 403");
    assert.ok(resp._meta?.upgrade_url, "upgrade_url should be set");
  });
});
