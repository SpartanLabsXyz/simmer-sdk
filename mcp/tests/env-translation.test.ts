import { test } from "node:test";
import assert from "node:assert/strict";
import { buildEnv, envToArgName } from "../src/env-translation.ts";
import type { Skill } from "../src/core/types.js";

const FIXTURE: Skill = {
  slug: "test", toolName: "simmer_test", name: "Test", description: "", version: "0.1.0",
  tier: "trading", entrypoint: "test.py", skillDir: "/tmp/test", hasDisclaimer: false,
  tunables: [
    { env: "TEST_FASTSCALER_DAILY_BUDGET", type: "number", default: 30, label: "Budget" },
    { env: "TEST_FASTSCALER_GATE", type: "number", default: 0.1, label: "Gate" },
  ],
};

test("envToArgName lowercases + strips 2-segment skill prefix", () => {
  // Real pattern: SIMMER_SLUG_ARG_NAME — strip first 2 segments
  assert.equal(envToArgName("TEST_FASTSCALER_DAILY_BUDGET"), "daily_budget");
  assert.equal(envToArgName("SIMMER_BTCUD_EXIT_BEFORE_RESOLUTION_HOURS"), "exit_before_resolution_hours");
  assert.equal(envToArgName("SIMMER_BTCUD_DAILY_BUDGET_USD"), "daily_budget_usd");
  // Short env vars (≤2 segs): returned as-is lowercased
  assert.equal(envToArgName("SIMMER_KEY"), "simmer_key");
  assert.equal(envToArgName("GATE"), "gate");
});

test("buildEnv defaults to sim when no live consent", () => {
  const env = buildEnv(FIXTURE, {}, { processEnv: {} });
  assert.equal(env.TRADING_VENUE, "sim");
  assert.equal(env.SIMMER_MANAGED_MODE, "1");
  assert.equal(env.AUTOMATON_MANAGED, "1");
});

test("buildEnv coerces to sim when dry_run=false but SIMMER_MCP_ALLOW_LIVE unset", () => {
  const env = buildEnv(FIXTURE, { dry_run: false, trading_venue: "polymarket" }, { processEnv: {} });
  assert.equal(env.TRADING_VENUE, "sim");
});

test("buildEnv coerces to sim when SIMMER_MCP_ALLOW_LIVE set but dry_run omitted", () => {
  const env = buildEnv(FIXTURE, { trading_venue: "polymarket" }, { processEnv: { SIMMER_MCP_ALLOW_LIVE: "true" } });
  assert.equal(env.TRADING_VENUE, "sim");
});

test("buildEnv coerces to sim when trading_venue=sim even with all gates", () => {
  const env = buildEnv(FIXTURE, { dry_run: false, trading_venue: "sim" }, { processEnv: { SIMMER_MCP_ALLOW_LIVE: "true" } });
  assert.equal(env.TRADING_VENUE, "sim");
});

test("buildEnv flips to live only when all three gates met", () => {
  const env = buildEnv(FIXTURE, { dry_run: false, trading_venue: "polymarket" }, { processEnv: { SIMMER_MCP_ALLOW_LIVE: "true" } });
  assert.equal(env.TRADING_VENUE, "polymarket");
});

test("buildEnv translates tunable args to env vars", () => {
  const env = buildEnv(FIXTURE, { daily_budget: 50, gate: 0.2 }, { processEnv: {} });
  assert.equal(env.TEST_FASTSCALER_DAILY_BUDGET, "50");
  assert.equal(env.TEST_FASTSCALER_GATE, "0.2");
});

test("buildEnv inherits process.env but does NOT inherit TRADING_VENUE from it", () => {
  const env = buildEnv(FIXTURE, {}, { processEnv: { TRADING_VENUE: "polymarket", SOME_OTHER: "val" } });
  assert.equal(env.TRADING_VENUE, "sim");
  assert.equal(env.SOME_OTHER, "val");
});
