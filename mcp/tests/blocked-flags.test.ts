import { test } from "node:test";
import assert from "node:assert/strict";
import { filterBlockedFlags, isBlockedFlag } from "../src/blocked-flags.ts";

test("blocks --live and short forms case-insensitive", () => {
  assert.equal(isBlockedFlag("--live"), true);
  assert.equal(isBlockedFlag("--LIVE"), true);
  assert.equal(isBlockedFlag("--no-dry-run"), true);
  assert.equal(isBlockedFlag("--no-dry"), true);
  assert.equal(isBlockedFlag("--real"), true);
  assert.equal(isBlockedFlag("--production"), true);
});

test("blocks --live=true and --live=1 (=-form)", () => {
  assert.equal(isBlockedFlag("--live=true"), true);
  assert.equal(isBlockedFlag("--live=1"), true);
  assert.equal(isBlockedFlag("--no-dry-run=true"), true);
  assert.equal(isBlockedFlag("--LIVE=YES"), true);
});

test("blocks --mode=live and --venue=*live*", () => {
  assert.equal(isBlockedFlag("--mode=live"), true);
  assert.equal(isBlockedFlag("--venue=polymarket-live"), true);
  assert.equal(isBlockedFlag("--venue=production"), true);
});

test("allows benign flags", () => {
  assert.equal(isBlockedFlag("--positions-only"), false);
  assert.equal(isBlockedFlag("--markets=abc,def"), false);
  assert.equal(isBlockedFlag("--cancel-all"), false);
  assert.equal(isBlockedFlag("--status"), false);
  assert.equal(isBlockedFlag(""), false);
});

test("filterBlockedFlags removes blocked and follows-value forms", () => {
  assert.deepEqual(filterBlockedFlags(["--live"]), []);
  assert.deepEqual(filterBlockedFlags(["--live", "true"]), []);
  assert.deepEqual(filterBlockedFlags(["--mode", "live"]), []);
  assert.deepEqual(filterBlockedFlags(["--positions-only", "--live", "--cancel-all"]), ["--positions-only", "--cancel-all"]);
  assert.deepEqual(filterBlockedFlags(["--ok", 42 as unknown as string, null as unknown as string]), ["--ok"]);
});

test("filterBlockedFlags handles --mode value-pair", () => {
  assert.deepEqual(filterBlockedFlags(["--mode", "live"]), []);
  assert.deepEqual(filterBlockedFlags(["--mode", "paper"]), ["--mode", "paper"]);
});
