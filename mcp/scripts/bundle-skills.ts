#!/usr/bin/env node
/**
 * Copies skill directories from simmer-sdk/skills/ into mcp/bundled-skills/.
 * Run via: npm run bundle-skills
 * Hooked as prepack so bundled-skills/ is always fresh before npm publish.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const mcpDir = path.resolve(__dirname, '..');
const skillsDir = path.resolve(mcpDir, '..', 'skills');
const outDir = path.resolve(mcpDir, 'bundled-skills');

const SKIP = new Set(['__pycache__', 'node_modules', '.DS_Store']);

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

// Validate source exists
if (!fs.existsSync(skillsDir)) {
  console.error(`skills dir not found: ${skillsDir}`);
  process.exit(1);
}

// Wipe and recreate output dir
if (fs.existsSync(outDir)) fs.rmSync(outDir, { recursive: true });
fs.mkdirSync(outDir, { recursive: true });

let count = 0;
for (const entry of fs.readdirSync(skillsDir, { withFileTypes: true })) {
  if (!entry.isDirectory()) continue;
  if (SKIP.has(entry.name)) continue;
  if (!fs.existsSync(path.join(skillsDir, entry.name, 'clawhub.json'))) continue;
  copyDir(path.join(skillsDir, entry.name), path.join(outDir, entry.name));
  count++;
}

console.log(`bundled ${count} skills → ${path.relative(process.cwd(), outDir)}`);
