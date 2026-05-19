import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import { discoverSkills, slugToToolName } from "../src/skill-discovery.ts";

function makeFixtureSkills(tmpDir: string): void {
  fs.mkdirSync(path.join(tmpDir, "test-trading-skill"), { recursive: true });
  fs.writeFileSync(
    path.join(tmpDir, "test-trading-skill", "clawhub.json"),
    JSON.stringify({
      emoji: "⚡",
      automaton: { managed: true, entrypoint: "trader.py" },
      tunables: [
        { env: "TEST_BUDGET", type: "number", default: 10, range: [1, 50], step: 1, label: "Budget" }
      ]
    })
  );
  fs.writeFileSync(
    path.join(tmpDir, "test-trading-skill", "SKILL.md"),
    "---\nname: test-trading-skill\ndescription: trading test\nmetadata:\n  version: '0.1.0'\n  displayName: 'Test Trader'\n---\n# Test"
  );
  fs.writeFileSync(path.join(tmpDir, "test-trading-skill", "trader.py"), "");

  fs.mkdirSync(path.join(tmpDir, "test-instruction-skill"), { recursive: true });
  fs.writeFileSync(
    path.join(tmpDir, "test-instruction-skill", "clawhub.json"),
    JSON.stringify({ emoji: "📖", automaton: { managed: false } })
  );
  fs.writeFileSync(
    path.join(tmpDir, "test-instruction-skill", "SKILL.md"),
    "---\nname: test-instruction-skill\ndescription: docs only\nmetadata:\n  version: '1.0.0'\n  displayName: 'Test Instruction'\n---\n# Test"
  );

  fs.mkdirSync(path.join(tmpDir, "test-unmanaged"), { recursive: true });
  fs.writeFileSync(
    path.join(tmpDir, "test-unmanaged", "clawhub.json"),
    JSON.stringify({ emoji: "🤷" })
  );
  fs.writeFileSync(
    path.join(tmpDir, "test-unmanaged", "SKILL.md"),
    "---\nname: test-unmanaged\ndescription: no automaton\nmetadata:\n  version: '0.0.1'\n  displayName: 'Test Unmanaged'\n---"
  );

  fs.mkdirSync(path.join(tmpDir, "test-malformed"), { recursive: true });
  fs.writeFileSync(path.join(tmpDir, "test-malformed", "clawhub.json"), "{ this is not valid json");
}

test("slugToToolName converts kebab to snake with simmer_ prefix", () => {
  assert.equal(slugToToolName("polymarket-fast-scaler"), "simmer_polymarket_fast_scaler");
  assert.equal(slugToToolName("simmer-briefing"), "simmer_simmer_briefing");
  assert.equal(slugToToolName("a-b-c-d"), "simmer_a_b_c_d");
});

test("discoverSkills classifies trading vs instruction tier", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "skill-discovery-test-"));
  makeFixtureSkills(tmpDir);
  const skills = discoverSkills(tmpDir);

  const trading = skills.find((s) => s.slug === "test-trading-skill");
  assert.ok(trading);
  assert.equal(trading.tier, "trading");
  assert.equal(trading.toolName, "simmer_test_trading_skill");
  assert.equal(trading.entrypoint, "trader.py");
  assert.equal(trading.tunables.length, 1);
  assert.equal(trading.tunables[0].env, "TEST_BUDGET");

  const instruction = skills.find((s) => s.slug === "test-instruction-skill");
  assert.ok(instruction);
  assert.equal(instruction.tier, "instruction");
  assert.equal(instruction.entrypoint, undefined);

  const unmanaged = skills.find((s) => s.slug === "test-unmanaged");
  assert.ok(unmanaged);
  assert.equal(unmanaged.tier, "instruction");

  fs.rmSync(tmpDir, { recursive: true });
});

test("discoverSkills skips malformed manifests without crashing", () => {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "skill-discovery-test-"));
  makeFixtureSkills(tmpDir);
  const skills = discoverSkills(tmpDir);
  const malformed = skills.find((s) => s.slug === "test-malformed");
  assert.equal(malformed, undefined);
  assert.ok(skills.find((s) => s.slug === "test-trading-skill"));
  fs.rmSync(tmpDir, { recursive: true });
});

test("discoverSkills returns empty array if dir doesn't exist", () => {
  const skills = discoverSkills("/nonexistent/path/abc123");
  assert.deepEqual(skills, []);
});
