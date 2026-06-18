import * as fs from "node:fs";
import * as path from "node:path";
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
    requires_pro: s.tier === "trading",
    has_disclaimer: s.hasDisclaimer,
  }));
}

export interface ToolResponse {
  [key: string]: unknown;
  content: Array<{ type: "text"; text: string }>;
  isError: boolean;
}

export function getSkillDocs(skills: Skill[], slug: string): ToolResponse {
  const skill = skills.find((s) => s.slug === slug);
  if (!skill) {
    const available = skills.map((s) => s.slug).join(", ");
    return {
      content: [{
        type: "text",
        text:
          `Skill "${slug}" is not bundled in this slim simmer-mcp package.\n\n` +
          `Install the latest copy from ClawHub on demand:\n\n` +
          `npx clawhub@latest install ${slug}\n\n` +
          `Bundled core skills: ${available}`,
      }],
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
