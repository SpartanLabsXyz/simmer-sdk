import * as fs from "node:fs";
import * as path from "node:path";
import type { Skill, Tunable } from "./core/types.js";

// Local copy — avoids a cross-source import that breaks node --test's .ts loader.
function envToArgName(envName: string): string {
  const parts = envName.split("_");
  if (parts.length <= 2) return envName.toLowerCase();
  return parts.slice(2).join("_").toLowerCase();
}

// Gate-controlled env names that skill tunables must never overwrite.
export const RESERVED_ENV_NAMES = new Set([
  "TRADING_VENUE", "SIMMER_MANAGED_MODE", "AUTOMATON_MANAGED",
  "PYTHONUNBUFFERED", "SIMMER_MCP_ALLOW_LIVE", "SIMMER_MCP_ALLOW_EXTRA_ARGS",
]);

// Schema arg names reserved for gate-relevant parameters in buildToolSchema.
export const RESERVED_ARG_NAMES = new Set(["dry_run", "trading_venue", "extra_args", "timeout_s"]);

export function slugToToolName(slug: string): string {
  return "simmer_" + slug.replace(/-/g, "_");
}

interface FrontmatterMeta {
  version?: string;
  displayName?: string;
  status?: string;
}

interface SkillMdFrontmatter {
  name?: string;
  description?: string;
  metadata?: FrontmatterMeta;
}

function parseSkillMd(skillMdPath: string): SkillMdFrontmatter {
  if (!fs.existsSync(skillMdPath)) return {};
  const content = fs.readFileSync(skillMdPath, "utf-8");
  const match = content.match(/^---\n([\s\S]*?)\n---/);
  if (!match) return {};
  const frontmatter: SkillMdFrontmatter = {};
  const lines = match[1].split("\n");
  let inMetadata = false;
  for (const line of lines) {
    if (line.match(/^metadata:\s*$/)) { inMetadata = true; continue; }
    if (line.match(/^[a-z]/) && !line.startsWith(" ")) { inMetadata = false; }
    const kv = line.match(/^\s*([a-zA-Z_]+):\s*["']?(.*?)["']?\s*$/);
    if (!kv) continue;
    const [, key, value] = kv;
    if (inMetadata) {
      frontmatter.metadata = frontmatter.metadata ?? {};
      if (key === "version") frontmatter.metadata.version = value;
      else if (key === "displayName") frontmatter.metadata.displayName = value;
      else if (key === "status") frontmatter.metadata.status = value;
    } else {
      if (key === "name") frontmatter.name = value;
      else if (key === "description") frontmatter.description = value;
    }
  }
  return frontmatter;
}

function parseTunables(slug: string, rawTunables: unknown): Tunable[] {
  if (!Array.isArray(rawTunables)) return [];
  const out: Tunable[] = [];
  for (const t of rawTunables) {
    if (typeof t !== "object" || t === null) continue;
    const obj = t as Record<string, unknown>;
    if (typeof obj.env !== "string" || typeof obj.label !== "string") continue;
    if (RESERVED_ENV_NAMES.has(obj.env)) {
      console.error(`[skill-discovery] WARN: skill "${slug}" tunable env="${obj.env}" is reserved, skipping`);
      continue;
    }
    const argName = envToArgName(obj.env);
    if (RESERVED_ARG_NAMES.has(argName)) {
      console.error(`[skill-discovery] WARN: skill "${slug}" tunable env="${obj.env}" maps to reserved arg "${argName}", skipping`);
      continue;
    }
    if (obj.type === "number" && typeof obj.default === "number") {
      out.push({
        env: obj.env, type: "number", default: obj.default,
        range: Array.isArray(obj.range) && obj.range.length === 2 ? [obj.range[0] as number, obj.range[1] as number] : undefined,
        step: typeof obj.step === "number" ? obj.step : undefined,
        label: obj.label,
      });
    } else if (obj.type === "string" && typeof obj.default === "string") {
      out.push({
        env: obj.env, type: "string", default: obj.default,
        enum: Array.isArray(obj.enum) ? obj.enum.filter((x): x is string => typeof x === "string") : undefined,
        label: obj.label,
      });
    } else if (obj.type === "boolean" && typeof obj.default === "boolean") {
      out.push({ env: obj.env, type: "boolean", default: obj.default, label: obj.label });
    }
  }
  return out;
}

export function discoverSkills(skillsRoot: string): Skill[] {
  if (!fs.existsSync(skillsRoot)) return [];

  const skills: Skill[] = [];
  for (const entry of fs.readdirSync(skillsRoot, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const slug = entry.name;
    const dir = path.join(skillsRoot, slug);
    const manifestPath = path.join(dir, "clawhub.json");
    if (!fs.existsSync(manifestPath)) continue;

    let manifest: Record<string, unknown>;
    try {
      manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
    } catch (err) {
      console.error(`[skill-discovery] WARN: malformed clawhub.json in ${slug}: ${err}`);
      continue;
    }

    const automaton = (manifest.automaton ?? {}) as { managed?: boolean; entrypoint?: string };
    const tier = automaton.managed === true ? "trading" : "instruction";
    const entrypoint = automaton.entrypoint;
    const frontmatter = parseSkillMd(path.join(dir, "SKILL.md"));

    skills.push({
      slug,
      toolName: slugToToolName(slug),
      name: frontmatter.metadata?.displayName ?? frontmatter.name ?? slug,
      description: frontmatter.description ?? "",
      version: frontmatter.metadata?.version ?? "0.0.0",
      tier,
      status: frontmatter.metadata?.status ?? manifest.status as string | undefined,
      published: manifest.published === true,
      entrypoint,
      tunables: parseTunables(slug, manifest.tunables),
      skillDir: dir,
      hasDisclaimer: fs.existsSync(path.join(dir, "DISCLAIMER.md")),
    });
  }
  return skills;
}
