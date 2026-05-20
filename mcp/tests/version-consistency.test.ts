/**
 * Regression test for SIM-2104: ensures BUNDLED_VERSION is sourced from
 * package.json at runtime, not hardcoded as a literal that can drift.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { BUNDLED_VERSION } from "../dist/version.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

test("BUNDLED_VERSION matches package.json version", () => {
  const pkg = JSON.parse(
    fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf-8"),
  );
  assert.equal(BUNDLED_VERSION, pkg.version);
});

test("BUNDLED_VERSION is a non-empty semver-shaped string", () => {
  assert.ok(typeof BUNDLED_VERSION === "string", "BUNDLED_VERSION should be a string");
  assert.ok(BUNDLED_VERSION.length > 0, "BUNDLED_VERSION should be non-empty");
  assert.match(BUNDLED_VERSION, /^\d+\.\d+\.\d+/, "BUNDLED_VERSION should start with semver MAJOR.MINOR.PATCH");
});
