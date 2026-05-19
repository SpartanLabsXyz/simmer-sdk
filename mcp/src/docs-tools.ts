import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import type { Skill } from "./core/types.js";

export interface SkillListEntry {
  slug: string;
  name: string;
  description: string;
  version: string;
  tier: "trading" | "instruction";
  status?: string;
  requires_api_key: boolean;
  requires_pro: boolean;
  has_disclaimer: boolean;
}

export function listSkills(skills: Skill[]): SkillListEntry[] {
  return skills.map((s) => ({
    slug: s.slug,
    name: s.name,
    description: s.description,
    version: s.version,
    tier: s.tier,
    status: s.status,
    requires_api_key: s.tier === "trading",
    requires_pro: false,
    has_disclaimer: s.hasDisclaimer,
  }));
}

export interface ToolResponse {
  content: Array<{ type: "text"; text: string }>;
  isError: boolean;
}

export function getSkillDocs(skills: Skill[], slug: string): ToolResponse {
  const skill = skills.find((s) => s.slug === slug);
  if (!skill) {
    const available = skills.map((s) => s.slug).join(", ");
    return {
      content: [{ type: "text", text: `Skill "${slug}" not found. Available: ${available}` }],
      isError: true,
    };
  }
  const skillMd = path.join(skill.skillDir, "SKILL.md");
  if (!fs.existsSync(skillMd)) {
    return {
      content: [{ type: "text", text: `SKILL.md missing for ${slug}` }],
      isError: true,
    };
  }
  const body = fs.readFileSync(skillMd, "utf-8");
  return { content: [{ type: "text", text: body }], isError: false };
}

export interface DocResource {
  uri: string;
  name: string;
  description: string;
  mimeType: "text/markdown";
}

export interface DocOptions {
  snapshotsDir?: string;
}

const RESOURCE_FILES: Record<string, string> = {
  "simmer://docs/api-reference": "docs.md",
  "simmer://docs/skill-reference": "skill.md",
};

const RESOURCE_NAMES: Record<string, string> = {
  "simmer://docs/api-reference": "Simmer API Reference",
  "simmer://docs/skill-reference": "Simmer Agent Reference",
};

const RESOURCE_DESCRIPTIONS: Record<string, string> = {
  "simmer://docs/api-reference": "Full Simmer API reference (~2400 lines)",
  "simmer://docs/skill-reference": "Condensed agent-facing reference (~600 lines)",
};

export function listDocResources(opts: DocOptions = {}): DocResource[] {
  void opts;
  return Object.keys(RESOURCE_FILES).map((uri) => ({
    uri,
    name: RESOURCE_NAMES[uri],
    description: RESOURCE_DESCRIPTIONS[uri],
    mimeType: "text/markdown" as const,
  }));
}

export interface DocReadResult {
  contents: Array<{ uri: string; mimeType: "text/markdown"; text: string }>;
  isError: boolean;
}

export async function readDocResource(uri: string, opts: DocOptions = {}): Promise<DocReadResult> {
  const file = RESOURCE_FILES[uri];
  if (!file) {
    return { contents: [], isError: true };
  }
  const snapshotsDir = opts.snapshotsDir ?? path.join(
    path.dirname(fileURLToPath(import.meta.url)),
    "..",
    "bundled-snapshots"
  );
  const filePath = path.join(snapshotsDir, file);
  if (!fs.existsSync(filePath)) {
    return { contents: [], isError: true };
  }
  return {
    contents: [{ uri, mimeType: "text/markdown", text: fs.readFileSync(filePath, "utf-8") }],
    isError: false,
  };
}
