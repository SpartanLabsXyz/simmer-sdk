import { test } from "node:test";
import assert from "node:assert/strict";
import { parseSkillOutput } from "../src/output-parsing.ts";

test("parses simmer_managed_output on last line", () => {
  const stdout = `running...\n{"simmer_managed_output":{"trades":3,"pnl":1.5}}\n`;
  const r = parseSkillOutput(stdout);
  assert.deepEqual(r.result, { trades: 3, pnl: 1.5 });
});

test("parses automaton key (back-compat) when simmer_managed_output absent", () => {
  const stdout = `running...\n{"automaton":{"trades":3}}\n`;
  const r = parseSkillOutput(stdout);
  assert.deepEqual(r.result, { trades: 3 });
});

test("prefers simmer_managed_output over automaton when both present", () => {
  const stdout = `{"simmer_managed_output":{"new":true}}\n{"automaton":{"old":true}}\n`;
  const r = parseSkillOutput(stdout);
  assert.equal(r.result!.new ?? r.result!.old, true);
});

test("returns null result when no parseable JSON", () => {
  const stdout = "just some logs\nno json here\n";
  const r = parseSkillOutput(stdout);
  assert.equal(r.result, null);
  assert.match(r.log, /just some logs/);
});

test("returns null result when JSON exists but no recognized key", () => {
  const stdout = '{"foo":"bar"}\n';
  const r = parseSkillOutput(stdout);
  assert.equal(r.result, null);
});

test("ignores non-JSON noise on intermediate lines", () => {
  const stdout = `step 1\nstep 2\n{"simmer_managed_output":{"k":1}}\n`;
  const r = parseSkillOutput(stdout);
  assert.deepEqual(r.result, { k: 1 });
  assert.match(r.log, /step 1/);
});

test("handles trailing whitespace + empty lines", () => {
  const stdout = `{"simmer_managed_output":{"k":1}}\n\n  \n`;
  const r = parseSkillOutput(stdout);
  assert.deepEqual(r.result, { k: 1 });
});
