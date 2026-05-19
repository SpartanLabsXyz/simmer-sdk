/**
 * Tests for SimmerApi.checkPro() and SimmerApi.backtest() BackendError behavior.
 * Task 21 (SIM-2056): api.ts refactor — throw BackendError on 4xx/5xx.
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

describe("SimmerApi.checkPro", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  it("resolves when API returns 200", async () => {
    mockFetch(async () => new Response(JSON.stringify({ last_experiment_number: 0 }), { status: 200 }));
    const api = new SimmerApi("sk_test", "https://api.simmer.markets", "2.3.0");
    await assert.doesNotReject(() => api.checkPro());
  });

  it("throws BackendError(403) when API returns 403 — Pro required", async () => {
    mockFetch(async () =>
      new Response(JSON.stringify({ detail: "Autoresearch requires a Pro plan." }), { status: 403 })
    );
    const api = new SimmerApi("sk_test", "https://api.simmer.markets", "2.3.0");
    await assert.rejects(
      () => api.checkPro(),
      (err: unknown) => {
        assert.ok(err instanceof BackendError, `Expected BackendError, got ${err}`);
        assert.equal(err.statusCode, 403);
        assert.match(err.body, /Pro/i);
        assert.ok(err.upgradeUrl, "upgradeUrl should be set for 403");
        return true;
      },
    );
  });

  it("does not throw on network error (non-blocking — API unreachable)", async () => {
    mockFetch(async () => { throw new Error("Network error"); });
    const api = new SimmerApi("sk_test", "https://api.simmer.markets", "2.3.0");
    // Network failure must not block run_experiment — gracefully skips the check
    await assert.doesNotReject(() => api.checkPro());
  });

  it("does not throw on non-403 API error (server down, 500)", async () => {
    mockFetch(async () => new Response("Internal Server Error", { status: 500 }));
    const api = new SimmerApi("sk_test", "https://api.simmer.markets", "2.3.0");
    // Only 403 is meaningful for the Pro gate; other errors are transient
    await assert.doesNotReject(() => api.checkPro());
  });
});

describe("SimmerApi.backtest — throws BackendError on 4xx/5xx", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  it("returns BacktestResult on success", async () => {
    const mockResult = {
      trades_total: 10, trades_included: 8, trades_excluded: 2,
      simulated_pnl: 42.5, original_pnl: 38.0, win_rate: 0.75, improvement_pct: 11.8,
    };
    mockFetch(async () => new Response(JSON.stringify(mockResult), { status: 200 }));
    const api = new SimmerApi("sk_test", "https://api.simmer.markets", "2.3.0");
    const result = await api.backtest({ skill_slug: "test", config: {} });
    assert.equal(result.trades_total, 10);
    assert.equal(result.simulated_pnl, 42.5);
  });

  it("throws BackendError(403) when user is not Pro", async () => {
    mockFetch(async () =>
      new Response(JSON.stringify({ detail: "Autoresearch requires a Pro plan." }), { status: 403 })
    );
    const api = new SimmerApi("sk_test", "https://api.simmer.markets", "2.3.0");
    await assert.rejects(
      () => api.backtest({ skill_slug: "test", config: {} }),
      (err: unknown) => {
        assert.ok(err instanceof BackendError, `Expected BackendError, got ${err}`);
        assert.equal(err.statusCode, 403);
        assert.ok(err.upgradeUrl, "upgradeUrl should be set for 403");
        return true;
      },
    );
  });

  it("throws BackendError(401) on invalid API key", async () => {
    mockFetch(async () =>
      new Response(JSON.stringify({ detail: "Invalid API key" }), { status: 401 })
    );
    const api = new SimmerApi("sk_test", "https://api.simmer.markets", "2.3.0");
    await assert.rejects(
      () => api.backtest({ skill_slug: "test", config: {} }),
      (err: unknown) => {
        assert.ok(err instanceof BackendError);
        assert.equal(err.statusCode, 401);
        return true;
      },
    );
  });

  it("throws BackendError on 500 server error", async () => {
    mockFetch(async () => new Response(JSON.stringify({ detail: "Internal server error" }), { status: 500 }));
    const api = new SimmerApi("sk_test", "https://api.simmer.markets", "2.3.0");
    await assert.rejects(
      () => api.backtest({ skill_slug: "test", config: {} }),
      (err: unknown) => {
        assert.ok(err instanceof BackendError);
        assert.equal(err.statusCode, 500);
        return true;
      },
    );
  });
});
