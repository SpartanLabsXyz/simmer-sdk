/**
 * Tests for raw trade primitive tools:
 *   - executeTrade (safety gate, action field, coercion, success/error paths)
 *   - SimmerApi.trade, getBriefing, getMarkets, getMarketContext, cancelOrder
 */
import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { executeTrade } from "../dist/trade-primitives.js";
import { SimmerApi } from "../dist/api.js";
import { BackendError } from "../dist/errors.js";

// ---------------------------------------------------------------------------
// Fetch mock helpers
// ---------------------------------------------------------------------------

type FetchFn = typeof global.fetch;
let savedFetch: FetchFn;

function mockFetch(fn: FetchFn) {
  // @ts-expect-error global override for testing
  global.fetch = fn;
}

function okJson(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function errorJson(status: number, detail: string): Response {
  return new Response(JSON.stringify({ detail }), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// ---------------------------------------------------------------------------
// executeTrade — safety gate tests
// ---------------------------------------------------------------------------

describe("executeTrade — safety gate (SIMMER_MCP_ALLOW_LIVE)", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  const api = new SimmerApi("sk_live_test", "https://api.simmer.markets", "3.3.0");

  it("coerces to sim when SIMMER_MCP_ALLOW_LIVE is not set", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (url, init) => {
      const body = JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>;
      captured.push({ url: url.toString(), body });
      return okJson({ status: "ok", venue: "sim", dry_run: true });
    });

    const result = await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "buy", amount: 10, venue: "polymarket", dry_run: false },
      { /* no SIMMER_MCP_ALLOW_LIVE */ },
    );

    assert.ok(!result.isError, "should not be error");
    assert.ok(result.content[0].text.includes("coerced"), "should mention coercion");
    // The POST body should have venue=sim and dry_run=true
    assert.equal(captured[0].body.venue, "sim");
    assert.equal(captured[0].body.dry_run, true);
  });

  it("coerces to sim when dry_run=true even with SIMMER_MCP_ALLOW_LIVE=true", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (_url, init) => {
      captured.push(JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>);
      return okJson({ status: "ok" });
    });

    const result = await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "buy", amount: 5, venue: "polymarket", dry_run: true },
      { SIMMER_MCP_ALLOW_LIVE: "true" },
    );

    assert.ok(!result.isError);
    // dry_run stays true — no coercion warning needed (caller already asked for paper)
    assert.equal(captured[0].dry_run, true);
    assert.equal(captured[0].venue, "polymarket"); // venue passes through for dry-run context
  });

  it("allows live trade when all 3 gates pass", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (_url, init) => {
      captured.push(JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>);
      return okJson({ status: "executed", venue: "polymarket" });
    });

    const result = await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "buy", amount: 10, venue: "polymarket", dry_run: false },
      { SIMMER_MCP_ALLOW_LIVE: "true" },
    );

    assert.ok(!result.isError);
    assert.equal(captured[0].venue, "polymarket");
    assert.equal(captured[0].dry_run, false);
    assert.ok(!result.content[0].text.includes("coerced"), "should not warn about coercion");
  });

  it("fail-closes when dry_run is undefined even with ALLOW_LIVE=true (defense-in-depth)", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (_url, init) => {
      captured.push(JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>);
      return okJson({ status: "ok" });
    });

    // Bypass TS signature — simulate programmatic/malformed caller passing undefined.
    // Only literal `dry_run === false` should open the live gate; undefined must
    // coerce dry_run to true (paper). Venue may pass through (paper on real pricing).
    const result = await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "buy", amount: 10, venue: "polymarket", dry_run: undefined as unknown as boolean },
      { SIMMER_MCP_ALLOW_LIVE: "true" },
    );

    assert.ok(!result.isError);
    // Critical: dry_run MUST be true (not false, not undefined) — no live trade.
    assert.equal(captured[0].dry_run, true);
  });

  it("returns BackendError response on 4xx", async () => {
    mockFetch(async () => errorJson(401, "Invalid API key"));

    const result = await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "buy", amount: 10, venue: "sim", dry_run: true },
      {},
    );

    assert.ok(result.isError);
    assert.ok(result.content[0].text.includes("Invalid API key"));
  });

  it("returns error text on network failure", async () => {
    mockFetch(async () => { throw new Error("Network timeout"); });

    const result = await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "buy", amount: 10, venue: "sim", dry_run: true },
      {},
    );

    assert.ok(result.isError);
    assert.ok(result.content[0].text.includes("Network timeout"));
  });
});

// ---------------------------------------------------------------------------
// executeTrade — action field (buy vs sell)
// ---------------------------------------------------------------------------

describe("executeTrade — action field threading", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  const api = new SimmerApi("sk_live_test", "https://api.simmer.markets", "3.3.0");

  it("threads action='buy' into POST body", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (_url, init) => {
      captured.push(JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>);
      return okJson({ status: "ok", action: "buy" });
    });

    await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "buy", amount: 10, venue: "sim", dry_run: true },
      {},
    );

    assert.equal(captured[0].action, "buy");
    assert.equal(captured[0].amount, 10);
  });

  it("threads action='sell' + shares into POST body", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (_url, init) => {
      captured.push(JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>);
      return okJson({ status: "ok", action: "sell" });
    });

    const result = await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "sell", shares: 50, venue: "sim", dry_run: true },
      {},
    );

    assert.ok(!result.isError);
    assert.equal(captured[0].action, "sell");
    assert.equal(captured[0].shares, 50);
    assert.equal(captured[0].amount, undefined, "amount should not be sent for sells without amount");
  });

  it("venue gate still coerces a sell on live venue without SIMMER_MCP_ALLOW_LIVE", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (_url, init) => {
      captured.push(JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>);
      return okJson({ status: "ok" });
    });

    const result = await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "sell", shares: 25, venue: "polymarket", dry_run: false },
      { /* no SIMMER_MCP_ALLOW_LIVE */ },
    );

    assert.ok(!result.isError);
    assert.ok(result.content[0].text.includes("coerced"), "sell coercion warning should appear");
    assert.equal(captured[0].venue, "sim");
    assert.equal(captured[0].action, "sell"); // action still threads through correctly
    assert.equal(captured[0].dry_run, true);
  });
});

// ---------------------------------------------------------------------------
// SimmerApi raw primitive methods
// ---------------------------------------------------------------------------

describe("SimmerApi.getBriefing", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  const api = new SimmerApi("sk_live_test", "https://api.simmer.markets", "3.3.0");

  it("returns parsed briefing on 200", async () => {
    const fixture = { portfolio: { balance: 100 }, positions: [] };
    mockFetch(async () => okJson(fixture));
    const result = await api.getBriefing();
    assert.deepEqual(result, fixture);
  });

  it("appends since param when provided", async () => {
    const urls: string[] = [];
    mockFetch(async (url) => { urls.push(url.toString()); return okJson({}); });
    await api.getBriefing("2026-01-01T00:00:00Z");
    assert.ok(urls[0].includes("since="), "URL should include since param");
  });

  it("throws BackendError on 403", async () => {
    mockFetch(async () => errorJson(403, "Pro required"));
    await assert.rejects(() => api.getBriefing(), (e) => {
      assert.ok(e instanceof BackendError);
      assert.equal(e.statusCode, 403);
      return true;
    });
  });
});

describe("SimmerApi.getMarkets", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  const api = new SimmerApi("sk_live_test", "https://api.simmer.markets", "3.3.0");

  it("returns markets list on 200", async () => {
    const fixture = { markets: [{ id: "m1", question: "Will it rain?" }] };
    mockFetch(async () => okJson(fixture));
    const result = await api.getMarkets({ q: "rain" });
    assert.deepEqual(result, fixture);
  });

  it("builds query string from params", async () => {
    const urls: string[] = [];
    mockFetch(async (url) => { urls.push(url.toString()); return okJson({ markets: [] }); });
    await api.getMarkets({ q: "bitcoin", limit: 10, venue: "polymarket", status: "active" });
    const u = new URL(urls[0]);
    assert.equal(u.searchParams.get("q"), "bitcoin");
    assert.equal(u.searchParams.get("limit"), "10");
    assert.equal(u.searchParams.get("venue"), "polymarket");
    assert.equal(u.searchParams.get("status"), "active");
  });

  it("throws BackendError on non-200", async () => {
    mockFetch(async () => errorJson(500, "Internal server error"));
    await assert.rejects(() => api.getMarkets({}), (e) => {
      assert.ok(e instanceof BackendError);
      assert.equal(e.statusCode, 500);
      return true;
    });
  });
});

describe("SimmerApi.getMarketContext", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  const api = new SimmerApi("sk_live_test", "https://api.simmer.markets", "3.3.0");

  it("returns context on 200", async () => {
    const fixture = { market: { id: "m1" }, edge: { recommendation: "TRADE" } };
    mockFetch(async () => okJson(fixture));
    const result = await api.getMarketContext("m1", { my_probability: 0.8 });
    assert.deepEqual(result, fixture);
  });

  it("URL-encodes market ID", async () => {
    const urls: string[] = [];
    mockFetch(async (url) => { urls.push(url.toString()); return okJson({}); });
    await api.getMarketContext("market/with/slashes");
    assert.ok(urls[0].includes("market%2Fwith%2Fslashes"), "market ID should be URL-encoded");
  });

  it("throws BackendError on 404", async () => {
    mockFetch(async () => errorJson(404, "Market not found"));
    await assert.rejects(() => api.getMarketContext("bad-id"), (e) => {
      assert.ok(e instanceof BackendError);
      assert.equal(e.statusCode, 404);
      return true;
    });
  });
});

describe("SimmerApi.cancelOrder", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  const api = new SimmerApi("sk_live_test", "https://api.simmer.markets", "3.3.0");

  it("sends DELETE request and returns result", async () => {
    const methods: string[] = [];
    const urls: string[] = [];
    mockFetch(async (url, init) => {
      methods.push(init?.method ?? "GET");
      urls.push(url.toString());
      return okJson({ cancelled: true, order_id: "o1" });
    });
    const result = await api.cancelOrder("o1");
    assert.equal(methods[0], "DELETE");
    assert.ok(urls[0].endsWith("/api/sdk/orders/o1"));
    assert.deepEqual(result, { cancelled: true, order_id: "o1" });
  });

  it("throws BackendError on 404", async () => {
    mockFetch(async () => errorJson(404, "Order not found"));
    await assert.rejects(() => api.cancelOrder("bad-order"), (e) => {
      assert.ok(e instanceof BackendError);
      assert.equal(e.statusCode, 404);
      return true;
    });
  });
});
