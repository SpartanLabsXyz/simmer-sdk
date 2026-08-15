/**
 * Tests for the keyless public-endpoint reads.
 *
 * The property that matters most here is the NEGATIVE one: these calls must
 * never send an Authorization header. They exist so an agent with no account
 * can browse Simmer, and a stray header would 401 the very callers they serve.
 */
import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { browseMarkets, getLeaderboard, fetchPublicJson } from "../dist/public-api.js";
import { BackendError } from "../dist/errors.js";

type FetchFn = typeof global.fetch;
let savedFetch: FetchFn;

function captureFetch(status = 200, body: unknown = { markets: [] }) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  // @ts-expect-error global override for testing
  global.fetch = async (url: string, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    return new Response(JSON.stringify(body), { status });
  };
  return calls;
}

describe("public-api — no credentials leave the process", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  it("browseMarkets sends no Authorization header", async () => {
    const calls = captureFetch();
    await browseMarkets("https://api.simmer.markets", { q: "weather" });
    const headers = (calls[0].init?.headers ?? {}) as Record<string, string>;
    const keys = Object.keys(headers).map((k) => k.toLowerCase());
    assert.equal(keys.includes("authorization"), false, "must not send Authorization");
  });

  it("getLeaderboard sends no Authorization header", async () => {
    const calls = captureFetch(200, { sdk_agents: [] });
    await getLeaderboard("https://api.simmer.markets");
    const headers = (calls[0].init?.headers ?? {}) as Record<string, string>;
    const keys = Object.keys(headers).map((k) => k.toLowerCase());
    assert.equal(keys.includes("authorization"), false, "must not send Authorization");
  });
});

describe("public-api — URL construction", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  it("hits the public catalogue endpoint, not the SDK one", async () => {
    const calls = captureFetch();
    await browseMarkets("https://api.simmer.markets", {});
    assert.equal(calls[0].url, "https://api.simmer.markets/api/markets");
  });

  it("passes through search and filter params", async () => {
    const calls = captureFetch();
    await browseMarkets("https://api.simmer.markets", { q: "rain nyc", tags: "weather", status: "active" });
    const u = new URL(calls[0].url);
    assert.equal(u.searchParams.get("q"), "rain nyc");
    assert.equal(u.searchParams.get("tags"), "weather");
    assert.equal(u.searchParams.get("status"), "active");
  });

  it("caps limit at 200 for markets and 50 for the leaderboard", async () => {
    const marketCalls = captureFetch();
    await browseMarkets("https://api.simmer.markets", { limit: 9999 });
    assert.equal(new URL(marketCalls[0].url).searchParams.get("limit"), "200");

    const lbCalls = captureFetch(200, {});
    await getLeaderboard("https://api.simmer.markets", { limit: 9999 });
    assert.equal(new URL(lbCalls[0].url).searchParams.get("limit"), "50");
  });

  it("omits the query string entirely when no params are given", async () => {
    const calls = captureFetch(200, {});
    await getLeaderboard("https://api.simmer.markets");
    assert.equal(calls[0].url, "https://api.simmer.markets/api/leaderboard/all");
  });
});

describe("public-api — error surface", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  it("throws BackendError carrying the server detail on 4xx", async () => {
    captureFetch(429, { detail: "Rate limit exceeded" });
    await assert.rejects(
      () => browseMarkets("https://api.simmer.markets", {}),
      (err: unknown) => {
        assert.ok(err instanceof BackendError, `Expected BackendError, got ${err}`);
        assert.equal(err.statusCode, 429);
        assert.equal(err.body, "Rate limit exceeded");
        return true;
      },
    );
  });

  it("aborts when the body stalls after headers arrive", async () => {
    // Regression guard: clearing the abort timer as soon as fetch() resolves
    // leaves a server that sends 200-then-nothing able to hang the tool
    // forever. The timer must stay armed through body parsing.
    // @ts-expect-error global override for testing
    global.fetch = async (_url: string, init?: RequestInit) => {
      const signal = init?.signal;
      const body = new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('{"markets":'));
          // ...and never closes. Only the abort signal can end this.
          signal?.addEventListener("abort", () => {
            try { controller.error(new Error("aborted")); } catch { /* already errored */ }
          });
        },
      });
      return new Response(body, { status: 200, headers: { "Content-Type": "application/json" } });
    };

    const started = Date.now();
    await assert.rejects(() => fetchPublicJson("https://api.simmer.markets/api/markets", 150));
    const elapsed = Date.now() - started;
    assert.ok(elapsed < 5_000, `Expected abort near the 150ms deadline, hung for ${elapsed}ms`);
  });

  it("falls back to the status code when the error body is not JSON", async () => {
    // @ts-expect-error global override for testing
    global.fetch = async () => new Response("<html>502</html>", { status: 502 });
    await assert.rejects(
      () => getLeaderboard("https://api.simmer.markets"),
      (err: unknown) => {
        assert.ok(err instanceof BackendError);
        assert.equal(err.body, "HTTP 502");
        return true;
      },
    );
  });
});
