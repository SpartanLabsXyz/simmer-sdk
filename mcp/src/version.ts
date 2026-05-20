/**
 * Single source of truth for the package version.
 * Reads from package.json at module load so the runtime value is always
 * consistent with what npm publishes.
 *
 * SIM-2104: previously hardcoded as a literal in mcp-server.ts, which drifted
 * from package.json during the 3.0.0 → 3.0.1 hotfix and caused the server to
 * report the wrong version + print spurious "update available" warnings.
 */
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

interface PackageJson {
  version: string;
}

const pkg = JSON.parse(
  fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf-8"),
) as PackageJson;

export const BUNDLED_VERSION: string = pkg.version;
