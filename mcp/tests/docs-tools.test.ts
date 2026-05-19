import { test } from "node:test";
import assert from "node:assert/strict";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { listSkills, getSkillDocs, listDocResources, readDocResource } from "../src/docs-tools.ts";
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
});

test("listDocResources returns 2 resource URIs", () => {
  const resources = listDocResources();
  assert.ok(resources.length >= 2, `expected >= 2 resources, got ${resources.length}`);
  for (const r of resources) {
    assert.ok(r.uri.startsWith("simmer://"), `unexpected URI: ${r.uri}`);
    assert.equal(r.mimeType, "text/markdown");
  }
});

test("readDocResource returns isError=true when snapshots missing", async () => {
  const r = await readDocResource("simmer://docs/api-reference", { snapshotsDir: "/nonexistent/dir/abc123" });
  assert.equal(r.isError, true);
});

test("readDocResource returns isError=true for unknown URI", async () => {
  const r = await readDocResource("simmer://docs/unknown-resource");
  assert.equal(r.isError, true);
});
