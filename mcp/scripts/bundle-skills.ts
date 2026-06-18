#!/usr/bin/env node
/**
 * Copies skill directories from simmer-sdk/skills/ into mcp/bundled-skills/.
 * Run via: npm run bundle-skills
 * Hooked as prepack so bundled-skills/ is always fresh before npm publish.
 *
 * Use --check in CI/release smoke tests to verify an existing bundle matches
 * the canonical skills tree without rewriting mcp/bundled-skills/.
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const mcpDir = path.resolve(__dirname, '..');
const skillsDir = path.resolve(mcpDir, '..', 'skills');
const outDir = path.resolve(mcpDir, 'bundled-skills');
const checkMode = process.argv.includes('--check');

// Keep the npm package small and drift-resistant. The MCP server exposes base
// trade/market tools in TypeScript; bundled skills are only the pinned core
// playbooks/runnables every agent needs before installing situational skills
// on demand from ClawHub.
const CORE_BUNDLED_SKILLS = new Set([
  'simmer',
  'simmer-wallet-setup',
  'simmer-mcp-setup',
  'preflight',
  'polymarket-btc-up-down-trader',
]);

// config.json is gitignored per-user skill config (.gitignore: **/config.json) —
// it exists in a dev's working tree but never in a clean checkout, so bundling it
// makes the committed bundle un-reproducible on CI (extra: drift). Never bundle it.
const SKIP = new Set(['__pycache__', 'node_modules', '.DS_Store', 'config.json']);

function copyDir(src: string, dest: string): void {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (SKIP.has(entry.name)) continue;
    const srcPath = path.join(src, entry.name);
    const destPath = path.join(dest, entry.name);
    if (entry.isDirectory()) {
      copyDir(srcPath, destPath);
    } else {
      fs.copyFileSync(srcPath, destPath);
    }
  }
}

function getSkipReason(skillDir: string): string | null {
  const manifestPath = path.join(skillDir, 'clawhub.json');
  if (!fs.existsSync(manifestPath)) return 'missing clawhub.json';

  try {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    if (manifest?.published === false) return 'published=false';
    if (manifest?.sensitivity === 'sensitive' && manifest?.sensitivity_approved !== true) {
      return 'sensitive skill without approval';
    }
  } catch {
    return 'invalid clawhub.json';
  }

  return null;
}

function hashFile(filePath: string): string {
  return crypto.createHash('sha256').update(fs.readFileSync(filePath)).digest('hex');
}

function collectFiles(root: string): Map<string, string> {
  const files = new Map<string, string>();
  if (!fs.existsSync(root)) return files;

  function walk(dir: string): void {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (SKIP.has(entry.name)) continue;
      const fullPath = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(fullPath);
      } else if (entry.isFile()) {
        files.set(path.relative(root, fullPath), hashFile(fullPath));
      }
    }
  }

  walk(root);
  return files;
}

function diffBundles(expectedDir: string, actualDir: string): string[] {
  const expected = collectFiles(expectedDir);
  const actual = collectFiles(actualDir);
  const diffs: string[] = [];

  for (const [file, hash] of expected) {
    const actualHash = actual.get(file);
    if (actualHash === undefined) {
      diffs.push(`missing: ${file}`);
    } else if (actualHash !== hash) {
      diffs.push(`changed: ${file}`);
    }
  }

  for (const file of actual.keys()) {
    if (!expected.has(file)) {
      diffs.push(`extra: ${file}`);
    }
  }

  return diffs.sort();
}

function buildBundle(targetDir: string): { count: number; skipped: number } {
  if (fs.existsSync(targetDir)) fs.rmSync(targetDir, { recursive: true });
  fs.mkdirSync(targetDir, { recursive: true });

  let count = 0;
  let skipped = 0;
  for (const entry of fs.readdirSync(skillsDir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    if (SKIP.has(entry.name)) continue;
    if (!CORE_BUNDLED_SKILLS.has(entry.name)) {
      console.log(`[bundle-skills] skipping ${entry.name}: not in core bundle allowlist`);
      skipped++;
      continue;
    }
    const skillDir = path.join(skillsDir, entry.name);
    const skipReason = getSkipReason(skillDir);
    if (skipReason !== null) {
      console.log(`[bundle-skills] skipping ${entry.name}: ${skipReason}`);
      skipped++;
      continue;
    }
    copyDir(skillDir, path.join(targetDir, entry.name));
    count++;
  }

  return { count, skipped };
}

// Validate source exists
if (!fs.existsSync(skillsDir)) {
  console.error(`skills dir not found: ${skillsDir}`);
  process.exit(1);
}

if (checkMode) {
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'simmer-mcp-bundled-skills-'));
  try {
    const { count, skipped } = buildBundle(tmpDir);
    const diffs = diffBundles(tmpDir, outDir);
    if (diffs.length > 0) {
      console.error(
        `[bundle-skills] DRIFT: ${path.relative(process.cwd(), outDir)} does not match ../skills.\n` +
        `  Run: npm run bundle-skills\n` +
        diffs.slice(0, 50).map((d) => `  - ${d}`).join('\n') +
        (diffs.length > 50 ? `\n  ... ${diffs.length - 50} more` : ''),
      );
      process.exit(1);
    }
    console.log(`bundle fresh: ${count} skills, skipped ${skipped} → ${path.relative(process.cwd(), outDir)}`);
  } finally {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  }
} else {
  const { count, skipped } = buildBundle(outDir);
  console.log(`bundled ${count} skills, skipped ${skipped} → ${path.relative(process.cwd(), outDir)}`);
}
