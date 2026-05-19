/**
 * Per-skill smoke tests (Tasks 19-20).
 * Validates structure + Python syntax for all Tier B (trading) skills.
 * Does NOT execute trading logic — no network, no API keys required.
 * Skips cleanly if python3 is not found on PATH.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import { discoverSkills } from "../src/skill-discovery.ts";
import { runSkillProcess } from "../src/skill-runner.ts";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BUNDLED_SKILLS_DIR = path.join(__dirname, "../bundled-skills");

function detectPython3(): string | null {
  const r = spawnSync("python3", ["--version"], { timeout: 3000 });
  return r.status === 0 ? "found" : null;
}

const PYTHON3_AVAILABLE = detectPython3();
const SKIP_REASON = PYTHON3_AVAILABLE === null ? "python3 not found on PATH — skipping Python syntax checks" : false;

test("bundled-skills directory exists", () => {
  assert.ok(fs.existsSync(BUNDLED_SKILLS_DIR), `bundled-skills not found at ${BUNDLED_SKILLS_DIR}`);
});

test("discovers at least 10 Tier B trading skills", () => {
  const skills = discoverSkills(BUNDLED_SKILLS_DIR);
  const trading = skills.filter((s) => s.tier === "trading");
  assert.ok(trading.length >= 10, `expected >= 10 trading skills, found ${trading.length}: ${trading.map((s) => s.slug).join(", ")}`);
});

test("all Tier B skills have entrypoint file on disk", () => {
  const skills = discoverSkills(BUNDLED_SKILLS_DIR);
  const trading = skills.filter((s) => s.tier === "trading");
  for (const skill of trading) {
    assert.ok(skill.entrypoint, `${skill.slug}: no entrypoint in clawhub.json`);
    const ep = path.join(skill.skillDir, skill.entrypoint!);
    assert.ok(fs.existsSync(ep), `${skill.slug}: entrypoint not found at ${ep}`);
  }
});

test("all Tier B skills have SKILL.md", () => {
  const skills = discoverSkills(BUNDLED_SKILLS_DIR);
  for (const skill of skills.filter((s) => s.tier === "trading")) {
    const md = path.join(skill.skillDir, "SKILL.md");
    assert.ok(fs.existsSync(md), `${skill.slug}: SKILL.md missing`);
  }
});

// Python syntax check per skill — skips if python3 not available
const tradingSkills = discoverSkills(BUNDLED_SKILLS_DIR).filter((s) => s.tier === "trading");

for (const skill of tradingSkills) {
  test(`py_compile: ${skill.slug}`, { skip: SKIP_REASON }, async () => {
    const ep = path.join(skill.skillDir, skill.entrypoint!);
    const safeEnv = Object.fromEntries(
      Object.entries(process.env).filter((pair): pair is [string, string] => pair[1] !== undefined)
    );
    const result = await runSkillProcess({
      file: "python3",
      args: ["-m", "py_compile", ep],
      env: safeEnv,
      timeoutMs: 10_000,
    });
    assert.equal(
      result.exitCode, 0,
      `${skill.slug}: py_compile failed (exit ${result.exitCode})\n${result.stderr}`
    );
  });
}
