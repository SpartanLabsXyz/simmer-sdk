/**
 * Tests for raw trade primitive handlers and SimmerApi methods.
 *
 * After the registerTool middleware refactor (SIM-2383):
 *   - executeTrade accepts ctx: { live: boolean; allowLive: boolean } instead of processEnv.
 *   - ctx.allowLive drives the resolveVenue gate (live vs paper).
 *   - Safety gating (blocking when SIMMER_MCP_ALLOW_LIVE is not set) is tested in
 *     register-tool.test.ts, not here.
 */
import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { executeTrade, executeCancelOrder } from "../dist/trade-primitives.js";
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

// Helpers for constructing ctx
const ctxPaper = { live: false, allowLive: false };
const ctxLive = { live: true, allowLive: true };
const ctxAllowedButPaper = { live: false, allowLive: true };

// ---------------------------------------------------------------------------
// executeTrade — action field threading
// ---------------------------------------------------------------------------

describe("executeTrade — action field threading", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  const api = new SimmerApi("sk_live_test", "https://api.simmer.markets", "3.3.0");

  it("threads action='buy' + amount into POST body", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (_url, init) => {
      captured.push(JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>);
      return okJson({ status: "ok" });
    });

    await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "buy", amount: 10, venue: "sim", dry_run: true },
      ctxPaper,
    );

    assert.equal(captured[0].action, "buy", "action='buy' must be in POST body");
    assert.equal(captured[0].amount, 10);
  });

  it("threads action='sell' + shares into POST body", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (_url, init) => {
      captured.push(JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>);
      return okJson({ status: "ok" });
    });

    await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "sell", shares: 50, venue: "sim", dry_run: true },
      ctxPaper,
    );

    assert.equal(captured[0].action, "sell", "action='sell' must be in POST body (not 'buy')");
    assert.equal(captured[0].shares, 50);
    assert.equal(captured[0].amount, undefined, "amount should not be present for sells");
  });
});

// ---------------------------------------------------------------------------
// executeTrade — resolveVenue coercion gate
// ---------------------------------------------------------------------------

describe("executeTrade — resolveVenue coercion gate", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  const api = new SimmerApi("sk_live_test", "https://api.simmer.markets", "3.3.0");

  it("coerces to sim and warns when dry_run=false + live venue but !allowLive", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (_url, init) => {
      captured.push(JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>);
      return okJson({ status: "ok" });
    });

    const result = await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "buy", amount: 10, venue: "polymarket", dry_run: false },
      ctxPaper, // allowLive=false
    );

    assert.ok(!result.isError, "should not be isError — coercion produces a warning, not an error");
    assert.ok(result.content[0].text.includes("SIMMER_MCP_ALLOW_LIVE"), "should warn about env var");
    assert.equal(captured[0].venue, "sim", "venue coerced to sim");
    assert.equal(captured[0].dry_run, true, "dry_run coerced to true");
  });

  it("allows live trade when dry_run=false + live venue + allowLive=true", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (_url, init) => {
      captured.push(JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>);
      return okJson({ status: "executed", venue: "polymarket" });
    });

    const result = await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "buy", amount: 10, venue: "polymarket", dry_run: false },
      ctxLive, // allowLive=true
    );

    assert.ok(!result.isError);
    assert.equal(captured[0].venue, "polymarket", "venue NOT coerced when live allowed");
    assert.equal(captured[0].dry_run, false, "dry_run NOT coerced when live allowed");
    assert.ok(result.content[0].text.includes("Trade result"), "label reflects live trade");
  });

  it("paper trade on sim venue passes through without coercion even without allowLive", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (_url, init) => {
      captured.push(JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>);
      return okJson({ status: "ok", dry_run: true });
    });

    const result = await executeTrade(
      api,
      { market_id: "m1", side: "no", action: "buy", amount: 5, venue: "sim", dry_run: true },
      ctxPaper,
    );

    assert.ok(!result.isError);
    assert.equal(captured[0].venue, "sim");
    assert.equal(captured[0].dry_run, true);
    assert.ok(!result.content[0].text.includes("coerced"), "no coercion warning for paper/sim");
    assert.ok(result.content[0].text.includes("Dry-run result"));
  });

  it("unknown venue coerced to sim with warning", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (_url, init) => {
      captured.push(JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>);
      return okJson({ status: "ok" });
    });

    const result = await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "buy", amount: 5, venue: "invalid_venue", dry_run: true },
      ctxAllowedButPaper,
    );

    assert.ok(!result.isError);
    assert.equal(captured[0].venue, "sim", "unknown venue coerced to sim");
    assert.ok(result.content[0].text.includes("Unknown venue"), "warns about unknown venue");
  });
});

// ---------------------------------------------------------------------------
// executeTrade — handler behaviour (error paths)
// ---------------------------------------------------------------------------

describe("executeTrade — error handling", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  const api = new SimmerApi("sk_live_test", "https://api.simmer.markets", "3.3.0");

  it("passes dry_run and venue through to the API unchanged (dry_run=true)", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (_url, init) => {
      captured.push(JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>);
      return okJson({ status: "ok", venue: "polymarket", dry_run: true });
    });

    const result = await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "buy", amount: 5, venue: "polymarket", dry_run: true },
      ctxAllowedButPaper, // allowLive=true but dry_run=true → paper
    );

    assert.ok(!result.isError, "should not be error");
    assert.equal(captured[0].dry_run, true, "dry_run passed through");
    assert.equal(captured[0].venue, "polymarket", "venue passed through");
    assert.ok(result.content[0].text.includes("Dry-run result"), "label reflects dry_run");
  });

  it("forwards optional args (reasoning, source)", async () => {
    const captured: Record<string, unknown>[] = [];
    mockFetch(async (_url, init) => {
      captured.push(JSON.parse((init?.body as string) ?? "{}") as Record<string, unknown>);
      return okJson({ status: "ok" });
    });

    await executeTrade(
      api,
      {
        market_id: "m2", side: "no", action: "sell",
        shares: 50, venue: "sim", dry_run: true,
        reasoning: "test reason", source: "sdk:test",
      },
      ctxPaper,
    );

    assert.equal(captured[0].shares, 50);
    assert.equal(captured[0].reasoning, "test reason");
    assert.equal(captured[0].source, "sdk:test");
    assert.equal(captured[0].amount, undefined, "amount should not be present");
  });

  it("returns BackendError response on 4xx", async () => {
    mockFetch(async () => errorJson(401, "Invalid API key"));

    const result = await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "buy", amount: 10, venue: "sim", dry_run: true },
      ctxPaper,
    );

    assert.ok(result.isError);
    assert.ok(result.content[0].text.includes("Invalid API key"));
  });

  it("returns error text on network failure", async () => {
    mockFetch(async () => { throw new Error("Network timeout"); });

    const result = await executeTrade(
      api,
      { market_id: "m1", side: "yes", action: "buy", amount: 10, venue: "sim", dry_run: true },
      ctxPaper,
    );

    assert.ok(result.isError);
    assert.ok(result.content[0].text.includes("Network timeout"));
  });
});

// ---------------------------------------------------------------------------
// executeCancelOrder — handler behaviour
// ---------------------------------------------------------------------------

describe("executeCancelOrder — handler behaviour", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  const api = new SimmerApi("sk_live_test", "https://api.simmer.markets", "3.3.0");

  it("sends DELETE to correct endpoint and returns cancelled message", async () => {
    const methods: string[] = [];
    const urls: string[] = [];
    mockFetch(async (url, init) => {
      methods.push(init?.method ?? "GET");
      urls.push(url.toString());
      return okJson({ cancelled: true, order_id: "o1" });
    });

    const result = await executeCancelOrder(api, { order_id: "o1" }, ctxLive);

    assert.ok(!result.isError, "should not be error");
    assert.equal(methods[0], "DELETE");
    assert.ok(urls[0].endsWith("/api/sdk/orders/o1"));
    assert.ok(result.content[0].text.includes("Order cancelled"));
    assert.ok(result.content[0].text.includes('"cancelled": true'));
  });

  it("URL-encodes order ID with special characters", async () => {
    const urls: string[] = [];
    mockFetch(async (url) => {
      urls.push(url.toString());
      return okJson({ cancelled: true });
    });

    await executeCancelOrder(api, { order_id: "ord/special" }, ctxLive);
    assert.ok(urls[0].includes("ord%2Fspecial"), "order ID should be URL-encoded");
  });

  it("returns BackendError response on 4xx", async () => {
    mockFetch(async () => errorJson(404, "Order not found"));

    const result = await executeCancelOrder(api, { order_id: "bad" }, ctxLive);

    assert.ok(result.isError);
    assert.ok(result.content[0].text.includes("Order not found"));
  });

  it("returns error text on network failure", async () => {
    mockFetch(async () => { throw new Error("Connection refused"); });

    const result = await executeCancelOrder(api, { order_id: "o2" }, ctxLive);

    assert.ok(result.isError);
    assert.ok(result.content[0].text.includes("Connection refused"));
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
