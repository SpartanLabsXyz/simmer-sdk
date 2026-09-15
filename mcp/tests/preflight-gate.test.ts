/**
 * Unit tests for the MCP live preflight mirror (CTO review on #372).
 */
import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { SimmerApi } from "../dist/api.js";
import {
  alertSignalsInsufficientGas,
  evaluateSdkPreflight,
  isSimLikePositionVenue,
  parseExposureCapUsd,
} from "../dist/preflight-gate.js";

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

function liveOkReads(opts: {
  positions?: Array<Record<string, unknown>>;
  riskAlerts?: unknown[];
} = {}) {
  return async (url: string | URL | Request) => {
    const u = url.toString();
    if (u.includes("/api/sdk/agents/me")) {
      return okJson({ real_trading_enabled: true, wallet_address: "0xabc" });
    }
    if (u.includes("/api/sdk/briefing")) {
      return okJson({
        venues: { polymarket: { balance: 50 } },
        risk_alerts: opts.riskAlerts ?? [],
      });
    }
    if (u.includes("/api/sdk/positions")) {
      return okJson({ positions: opts.positions ?? [] });
    }
    return okJson({});
  };
}

describe("parseExposureCapUsd", () => {
  it("unset / empty is 0 (opt-in, not a $100 default)", () => {
    assert.equal(parseExposureCapUsd(undefined), 0);
    assert.equal(parseExposureCapUsd(null), 0);
    assert.equal(parseExposureCapUsd(""), 0);
    assert.equal(parseExposureCapUsd("   "), 0);
  });

  it("parses a finite cap", () => {
    assert.equal(parseExposureCapUsd("25"), 25);
    assert.equal(parseExposureCapUsd(10), 10);
    assert.equal(parseExposureCapUsd("0"), 0);
  });

  it("rejects NaN / Infinity / 1e309 as non-finite", () => {
    assert.equal(Number.isFinite(parseExposureCapUsd("NaN")), false);
    assert.equal(Number.isFinite(parseExposureCapUsd("Infinity")), false);
    assert.equal(Number.isFinite(parseExposureCapUsd("1e309")), false);
    assert.equal(Number.isFinite(parseExposureCapUsd(Number.POSITIVE_INFINITY)), false);
    assert.equal(Number.isFinite(parseExposureCapUsd(Number.NaN)), false);
  });
});

describe("isSimLikePositionVenue", () => {
  it("treats null and undefined as sim, matching Python", () => {
    assert.equal(isSimLikePositionVenue(null), true);
    assert.equal(isSimLikePositionVenue(undefined), true);
    assert.equal(isSimLikePositionVenue("sim"), true);
    assert.equal(isSimLikePositionVenue("polymarket"), false);
    assert.equal(isSimLikePositionVenue("kalshi"), false);
  });
});

describe("alertSignalsInsufficientGas", () => {
  it("does not match free-text pol / policy / gas", () => {
    assert.equal(alertSignalsInsufficientGas({ message: "political risk policy" }), false);
    assert.equal(alertSignalsInsufficientGas({ message: "low pol on the wallet" }), false);
    assert.equal(alertSignalsInsufficientGas({ message: "insufficient gas on polygon" }), false);
    assert.equal(alertSignalsInsufficientGas("vegas political gaslighting"), false);
  });

  it("matches a structured INSUFFICIENT_GAS code", () => {
    assert.equal(alertSignalsInsufficientGas({ code: "INSUFFICIENT_GAS" }), true);
    assert.equal(alertSignalsInsufficientGas({ type: "insufficient_gas" }), true);
    assert.equal(alertSignalsInsufficientGas("INSUFFICIENT_GAS"), true);
  });
});

describe("evaluateSdkPreflight", () => {
  beforeEach(() => { savedFetch = global.fetch; });
  afterEach(() => { global.fetch = savedFetch; });

  const api = new SimmerApi("sk_live_test", "https://api.simmer.markets", "3.5.5");

  it("counts venue:null as sim, not real exposure", async () => {
    mockFetch(liveOkReads({
      positions: [
        { venue: null, current_value: 80 },
        { venue: "polymarket", current_value: 15 },
      ],
    }));

    const verdict = await evaluateSdkPreflight(api, {
      venue: "polymarket",
      plannedAmount: 0,
      exposureCapUsd: 100,
    });

    assert.equal(verdict.ok_to_trade, true);
    assert.equal(verdict.blockers.includes("EXPOSURE_CAP_EXCEEDED"), false);
  });

  it("does not fire INSUFFICIENT_GAS on free-text pol", async () => {
    mockFetch(liveOkReads({
      riskAlerts: [{ message: "political / policy watch: vegas event" }],
    }));

    const verdict = await evaluateSdkPreflight(api, {
      venue: "polymarket",
      plannedAmount: 10,
      exposureCapUsd: 0,
    });

    assert.equal(verdict.blockers.includes("INSUFFICIENT_GAS"), false);
    assert.equal(verdict.ok_to_trade, true);
  });

  it("fires INSUFFICIENT_GAS on a structured code", async () => {
    mockFetch(liveOkReads({
      riskAlerts: [{ code: "INSUFFICIENT_GAS", message: "fund wallet POL" }],
    }));

    const verdict = await evaluateSdkPreflight(api, {
      venue: "polymarket",
      plannedAmount: 10,
      exposureCapUsd: 0,
    });

    assert.equal(verdict.blockers.includes("INSUFFICIENT_GAS"), true);
    assert.equal(verdict.ok_to_trade, false);
  });

  it("throws on a non-finite exposure cap", async () => {
    mockFetch(liveOkReads());
    await assert.rejects(
      () => evaluateSdkPreflight(api, {
        venue: "polymarket",
        plannedAmount: 10,
        exposureCapUsd: Number.NaN,
      }),
      /finite number/,
    );
  });

  it("starts the three preflight reads without waiting on each other", async () => {
    let started = 0;
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    mockFetch(async (url) => {
      started += 1;
      await gate;
      return liveOkReads()(url);
    });

    const pending = evaluateSdkPreflight(api, {
      venue: "polymarket",
      plannedAmount: 1,
      exposureCapUsd: 0,
    });
    await new Promise((r) => setTimeout(r, 20));
    assert.equal(started, 3, "identity, briefing, and positions must start together");
    release();
    const verdict = await pending;
    assert.equal(verdict.ok_to_trade, true);
  });
});
