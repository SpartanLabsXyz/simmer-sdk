import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  formatNum,
  isBetter,
  currentResults,
  findBaselineMetric,
  computeConfidence,
} from "../dist/core/scoring.js";
import type { ExperimentResult } from "../dist/core/types.js";

function makeResult(overrides: Partial<ExperimentResult> = {}): ExperimentResult {
  return {
    commit: "abc1234",
    metric: 100,
    metrics: {},
    status: "keep",
    description: "test",
    timestamp: Date.now(),
    segment: 0,
    confidence: null,
    ...overrides,
  };
}

describe("formatNum", () => {
  it("formats null as dash", () => {
    assert.equal(formatNum(null, "$"), "—");
  });
  it("formats integer without decimals", () => {
    assert.equal(formatNum(100, "$"), "100$");
  });
  it("formats float to 2 decimals", () => {
    assert.equal(formatNum(3.14159, "%"), "3.14%");
  });
  it("handles empty unit", () => {
    assert.equal(formatNum(42, ""), "42");
  });
});

describe("isBetter", () => {
  it("lower is better", () => {
    assert.equal(isBetter(5, 10, "lower"), true);
    assert.equal(isBetter(15, 10, "lower"), false);
  });
  it("higher is better", () => {
    assert.equal(isBetter(15, 10, "higher"), true);
    assert.equal(isBetter(5, 10, "higher"), false);
  });
});

describe("currentResults", () => {
  it("filters by segment", () => {
    const results = [
      makeResult({ segment: 0, metric: 10 }),
      makeResult({ segment: 1, metric: 20 }),
      makeResult({ segment: 0, metric: 30 }),
    ];
    const cur = currentResults(results, 0);
    assert.equal(cur.length, 2);
    assert.equal(cur[0].metric, 10);
    assert.equal(cur[1].metric, 30);
  });
});

describe("findBaselineMetric", () => {
  it("returns first result metric in segment", () => {
    const results = [
      makeResult({ segment: 0, metric: 42 }),
      makeResult({ segment: 0, metric: 99 }),
    ];
    assert.equal(findBaselineMetric(results, 0), 42);
  });
  it("returns null for empty segment", () => {
    assert.equal(findBaselineMetric([], 0), null);
  });
});

describe("computeConfidence", () => {
  it("returns null with fewer than 3 data points", () => {
    const results = [
      makeResult({ metric: 10 }),
      makeResult({ metric: 20 }),
    ];
    assert.equal(computeConfidence(results, 0, "higher"), null);
  });
  it("returns null when MAD is 0 (all same value)", () => {
    const results = [
      makeResult({ metric: 10 }),
      makeResult({ metric: 10 }),
      makeResult({ metric: 10 }),
    ];
    assert.equal(computeConfidence(results, 0, "higher"), null);
  });
  it("returns positive confidence for real improvement", () => {
    const results = [
      makeResult({ metric: 10, status: "keep" }),
      makeResult({ metric: 11, status: "discard" }),
      makeResult({ metric: 12, status: "discard" }),
      makeResult({ metric: 20, status: "keep" }),
    ];
    const conf = computeConfidence(results, 0, "higher");
    assert.ok(conf !== null);
    assert.ok(conf > 0);
  });
  it("returns null when no kept experiments beat baseline", () => {
    const results = [
      makeResult({ metric: 10, status: "keep" }),
      makeResult({ metric: 8, status: "discard" }),
      makeResult({ metric: 9, status: "discard" }),
      makeResult({ metric: 7, status: "discard" }),
    ];
    // No kept results that beat baseline (only the baseline itself is kept)
    assert.equal(computeConfidence(results, 0, "higher"), null);
  });
});
