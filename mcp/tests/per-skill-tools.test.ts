import { test } from "node:test";
import assert from "node:assert/strict";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { invokeSkillTool } from "../dist/per-skill-tools.js";
import type { Skill } from "../src/core/types.ts";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const fixtureSkill: Skill = {
  slug: "fixture-skill",
  toolName: "simmer_fixture_skill",
  name: "Fixture Skill",
  description: "Echoes argv for MCP tests",
  version: "0.0.0",
  tier: "trading",
  entrypoint: "echo_skill.py",
  tunables: [],
  skillDir: path.join(__dirname, "fixtures"),
  hasDisclaimer: false,
};

const processEnv: Record<string, string> = {
  PATH: process.env.PATH ?? "",
  HOME: process.env.HOME ?? "",
  SIMMER_MCP_PYTHON: "python3",
};

function resultArgv(resp: Awaited<ReturnType<typeof invokeSkillTool>>): string[] {
  const result = resp._meta?.result as { argv?: unknown } | undefined;
  assert.ok(result, `missing parsed result: ${JSON.stringify(resp)}`);
  assert.ok(Array.isArray(result.argv), `missing argv: ${JSON.stringify(result)}`);
  return result.argv as string[];
}

test("invokeSkillTool passes read-only extra_args without SIMMER_MCP_ALLOW_EXTRA_ARGS", async () => {
  const resp = await invokeSkillTool(
    fixtureSkill,
    { dry_run: true, extra_args: ["--check", "--config"] },
    { processEnv },
  );

  assert.equal(resp.isError, false, resp.content[0]?.text);
  assert.deepEqual(resultArgv(resp), ["--check", "--config"]);
});

test("invokeSkillTool still drops non-read-only extra_args by default", async () => {
  const resp = await invokeSkillTool(
    fixtureSkill,
    { dry_run: true, extra_args: ["--check", "--positions-only", "--live"] },
    { processEnv },
  );

  assert.equal(resp.isError, false, resp.content[0]?.text);
  assert.deepEqual(resultArgv(resp), ["--check"]);
});

test("invokeSkillTool passes arbitrary sanitized extra_args when explicitly enabled", async () => {
  const resp = await invokeSkillTool(
    fixtureSkill,
    { dry_run: true, extra_args: ["--positions-only", "--live", "--config"] },
    { processEnv: { ...processEnv, SIMMER_MCP_ALLOW_EXTRA_ARGS: "true" } },
  );

  assert.equal(resp.isError, false, resp.content[0]?.text);
  assert.deepEqual(resultArgv(resp), ["--positions-only", "--config"]);
});

test("invokeSkillTool returns SKILL.md instructions for a Tier-A instruction-only skill", async () => {
  const instructionOnlySkill: Skill = {
    ...fixtureSkill,
    slug: "fixture-instruction-only",
    toolName: "simmer_fixture_instruction_only",
    tier: "instruction",
    entrypoint: undefined,
  };

  const resp = await invokeSkillTool(instructionOnlySkill, {}, { processEnv });

  // Must NOT dead-end with an error — it returns the playbook instead.
  assert.equal(resp.isError, false, resp.content[0]?.text);
  const text = resp.content[0]?.text ?? "";
  assert.match(text, /instruction-only skill \(Tier A\)/);
  assert.match(text, /UNIQUE_FIXTURE_MARKER_4815162342/);
});

test("invokeSkillTool gives a locate-the-file fallback when a Tier-A skill has no SKILL.md", async () => {
  const missingMdSkill: Skill = {
    ...fixtureSkill,
    slug: "fixture-no-md",
    toolName: "simmer_fixture_no_md",
    tier: "instruction",
    entrypoint: undefined,
    skillDir: path.join(__dirname, "fixtures", "does-not-exist"),
  };

  const resp = await invokeSkillTool(missingMdSkill, {}, { processEnv });

  assert.equal(resp.isError, false, resp.content[0]?.text);
  const text = resp.content[0]?.text ?? "";
  assert.match(text, /instruction-only skill \(Tier A\)/);
  assert.match(text, /SKILL\.md/);
});
