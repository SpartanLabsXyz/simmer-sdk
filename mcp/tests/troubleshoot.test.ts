/**
 * Tests for troubleshoot_error — live API + local pattern fallback.
 * Task 23 (SIM-2056): port from Python simmer-mcp v0.3.0.
 */
import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { troubleshootError } from "../dist/troubleshoot.js";

type FetchFn = typeof global.fetch;
let savedFetch: FetchFn;

function mockFetch(fn: FetchFn) {
  // @ts-expect-error global override for testing
  global.fetch = fn;
}

describe("troubleshootError — local fallback patterns", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  it("matches insufficient balance error", async () => {
    mockFetch(async () => { throw new Error("Network error"); });
    const r = await troubleshootError("not enough balance");
    assert.equal(r.matched, true);
    assert.ok(r.fix.length > 10, "fix should be non-trivial");
  });

  it("matches rate limit error", async () => {
    mockFetch(async () => { throw new Error("Network error"); });
    const r = await troubleshootError("429 too many requests");
    assert.equal(r.matched, true);
    assert.match(r.fix, /briefing/i);
  });

  it("matches nonce error", async () => {
    mockFetch(async () => { throw new Error("Network error"); });
    const r = await troubleshootError("nonce too low");
    assert.equal(r.matched, true);
  });

  it("returns matched=false for unknown error", async () => {
    mockFetch(async () => { throw new Error("Network error"); });
    const r = await troubleshootError("some totally unknown error xyz123");
    assert.equal(r.matched, false);
    assert.ok(r.fix.includes("docs.simmer.markets"), "should point to docs");
  });

  it("is case-insensitive", async () => {
    mockFetch(async () => { throw new Error("Network error"); });
    const r = await troubleshootError("INSUFFICIENT BALANCE in your wallet");
    assert.equal(r.matched, true);
  });
});

describe("troubleshootError — live API path", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  it("returns API response when API succeeds", async () => {
    const apiResult = { matched: true, fix: "Fix from API", source: "llm" };
    mockFetch(async () => new Response(JSON.stringify(apiResult), { status: 200 }));
    const r = await troubleshootError("some error", "https://api.simmer.markets");
    assert.equal(r.matched, true);
    assert.equal(r.fix, "Fix from API");
  });

  it("falls back to local patterns when API returns non-200", async () => {
    mockFetch(async () => new Response("", { status: 503 }));
    const r = await troubleshootError("nonce already used");
    assert.equal(r.matched, true);
  });

  it("falls back to local patterns on network timeout", async () => {
    mockFetch(async () => { throw new Error("AbortError"); });
    const r = await troubleshootError("rate limit exceeded");
    assert.equal(r.matched, true);
  });
});
