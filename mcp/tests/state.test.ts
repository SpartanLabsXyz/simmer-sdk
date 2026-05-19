import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import { reconstructState, defaultState, appendJsonl, writeJsonl } from "../dist/core/state.js";

let tmpDir: string;

beforeEach(() => {
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "autoresearch-test-"));
});

afterEach(() => {
  fs.rmSync(tmpDir, { recursive: true, force: true });
});

describe("defaultState", () => {
  it("returns fresh state with sensible defaults", () => {
    const s = defaultState();
    assert.equal(s.results.length, 0);
    assert.equal(s.bestDirection, "higher");
    assert.equal(s.metricName, "pnl");
    assert.equal(s.metricUnit, "$");
    assert.equal(s.paused, false);
    assert.equal(s.currentSegment, 0);
  });
});

describe("writeJsonl / appendJsonl", () => {
  it("writes and appends JSONL lines", () => {
    writeJsonl(tmpDir, { type: "config", name: "test" });
    appendJsonl(tmpDir, { run: 1, metric: 42 });

    const content = fs.readFileSync(path.join(tmpDir, "autoresearch.jsonl"), "utf-8");
    const lines = content.trim().split("\n");
    assert.equal(lines.length, 2);
    assert.deepEqual(JSON.parse(lines[0]), { type: "config", name: "test" });
    assert.deepEqual(JSON.parse(lines[1]), { run: 1, metric: 42 });
  });
});

describe("reconstructState", () => {
  it("returns default state when no JSONL exists", () => {
    const s = reconstructState(tmpDir);
    assert.equal(s.results.length, 0);
    assert.equal(s.name, null);
  });

  it("reconstructs config from JSONL", () => {
    writeJsonl(tmpDir, {
      type: "config",
      name: "My Session",
      skillSlug: "fast-loop",
      metricName: "pnl",
      metricUnit: "$",
      bestDirection: "higher",
    });

    const s = reconstructState(tmpDir);
    assert.equal(s.name, "My Session");
    assert.equal(s.skillSlug, "fast-loop");
    assert.equal(s.metricName, "pnl");
    assert.equal(s.bestDirection, "higher");
  });

  it("reconstructs results from JSONL", () => {
    writeJsonl(tmpDir, { type: "config", name: "test", metricName: "pnl", bestDirection: "higher" });
    appendJsonl(tmpDir, { commit: "abc1234", metric: 10, metrics: {}, status: "keep", description: "baseline", timestamp: 1000 });
    appendJsonl(tmpDir, { commit: "def5678", metric: 15, metrics: { trades: 5 }, status: "keep", description: "improved", timestamp: 2000 });

    const s = reconstructState(tmpDir);
    assert.equal(s.results.length, 2);
    assert.equal(s.results[0].metric, 10);
    assert.equal(s.results[1].metric, 15);
    assert.equal(s.results[1].metrics.trades, 5);
    assert.equal(s.bestMetric, 10); // baseline
  });

  it("handles segments correctly", () => {
    writeJsonl(tmpDir, { type: "config", name: "v1", metricName: "pnl", bestDirection: "higher" });
    appendJsonl(tmpDir, { commit: "a", metric: 10, metrics: {}, status: "keep", description: "baseline", timestamp: 1 });
    // Re-init = new segment
    appendJsonl(tmpDir, { type: "config", name: "v2", metricName: "pnl", bestDirection: "higher" });
    appendJsonl(tmpDir, { commit: "b", metric: 20, metrics: {}, status: "keep", description: "new baseline", timestamp: 2 });

    const s = reconstructState(tmpDir);
    assert.equal(s.currentSegment, 1);
    assert.equal(s.results.length, 2);
    assert.equal(s.results[0].segment, 0);
    assert.equal(s.results[1].segment, 1);
    assert.equal(s.bestMetric, 20); // baseline of current segment
  });

  it("skips malformed lines", () => {
    const jsonlPath = path.join(tmpDir, "autoresearch.jsonl");
    fs.writeFileSync(jsonlPath,
      JSON.stringify({ type: "config", name: "test", metricName: "pnl", bestDirection: "higher" }) + "\n" +
      "NOT VALID JSON\n" +
      JSON.stringify({ commit: "a", metric: 10, metrics: {}, status: "keep", description: "ok", timestamp: 1 }) + "\n"
    );

    const s = reconstructState(tmpDir);
    assert.equal(s.results.length, 1);
    assert.equal(s.results[0].metric, 10);
  });
});
