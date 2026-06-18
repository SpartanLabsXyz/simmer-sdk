import { test } from "node:test";
import assert from "node:assert/strict";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { listSkills, getSkillDocs } from "../src/docs-tools.ts";
import { discoverSkills } from "../src/skill-discovery.ts";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BUNDLED_SKILLS_DIR = path.join(__dirname, "../bundled-skills");
const skills = discoverSkills(BUNDLED_SKILLS_DIR);

test("listSkills returns an entry for every discovered skill", () => {
  const list = listSkills(skills);
  assert.equal(list.length, skills.length);
  for (const entry of list) {
    assert.ok(entry.slug, "slug missing");
    assert.ok(["trading", "instruction"].includes(entry.tier), `unexpected tier: ${entry.tier}`);
  }
});

test("getSkillDocs returns SKILL.md body for a known skill", () => {
  const tradingSkill = skills.find((s) => s.tier === "trading");
  if (!tradingSkill) {
    // No trading skills found — bundled-skills may be empty in some environments
    return;
  }
  const r = getSkillDocs(skills, tradingSkill.slug);
  assert.equal(r.isError, false, `isError=true: ${r.content[0]?.text}`);
  assert.ok(r.content[0]?.text.length > 0, "empty SKILL.md body");
});

test("getSkillDocs returns error for unknown slug", () => {
  const r = getSkillDocs(skills, "no-such-skill-xyz");
  assert.equal(r.isError, true);
  assert.ok(r.content[0]?.text.includes("no-such-skill-xyz"));
  assert.ok(r.content[0]?.text.includes("npx clawhub@latest install no-such-skill-xyz"));
});

test("getSkillDocs routes unbundled long-tail skills to ClawHub on demand", () => {
  for (const slug of ["polymarket-combo-builder", "polymarket-soccer-shock-ladder"]) {
    const r = getSkillDocs(skills, slug);
    assert.equal(r.isError, true);
    assert.ok(r.content[0]?.text.includes(`npx clawhub@latest install ${slug}`));
  }
});
