import { test } from "node:test";
import assert from "node:assert/strict";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { runSkillProcess } from "../src/skill-runner.ts";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ECHO_SKILL = path.join(__dirname, "fixtures/echo_skill.py");

const BASE_ENV: Record<string, string> = {
  PATH: process.env.PATH ?? "",
  HOME: process.env.HOME ?? "",
};

test("runSkillProcess: echo skill exits 0 and includes stdout", async () => {
  const r = await runSkillProcess({ file: "python3", args: [ECHO_SKILL], env: BASE_ENV, timeoutMs: 5000 });
  assert.equal(r.exitCode, 0);
  assert.ok(r.stdout.includes("simmer_managed_output"), `stdout: ${r.stdout}`);
  assert.equal(r.timedOut, false);
});

test("runSkillProcess: exit1 mode produces non-zero exit", async () => {
  const r = await runSkillProcess({
    file: "python3", args: [ECHO_SKILL],
    env: { ...BASE_ENV, ECHO_MODE: "exit1" },
    timeoutMs: 5000,
  });
  assert.equal(r.exitCode, 1);
  assert.ok(r.stderr.includes("an error happened"));
});

test("runSkillProcess: sleep mode triggers timeout", async () => {
  const r = await runSkillProcess({
    file: "python3", args: [ECHO_SKILL],
    env: { ...BASE_ENV, ECHO_MODE: "sleep" },
    timeoutMs: 300,
  });
  assert.equal(r.timedOut, true);
});

test("runSkillProcess: missing binary returns error", async () => {
  const r = await runSkillProcess({
    file: "no_such_binary_xyz",
    args: [],
    env: BASE_ENV,
    timeoutMs: 2000,
  });
  assert.equal(r.exitCode, null);
  assert.ok(r.stderr.length > 0);
});
