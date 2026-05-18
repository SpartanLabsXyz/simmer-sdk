#!/usr/bin/env node
/**
 * Simmer MCP Server
 *
 * Exposes all Simmer SDK skills as MCP tools for Claude Desktop / Claude Code.
 * Each skill becomes a callable tool; automaton skills run as subprocesses,
 * non-automaton skills return their documentation.
 *
 * Skill discovery order:
 *   1. SIMMER_SKILLS_DIR env var
 *   2. Adjacent ../skills/ relative to this file (repo usage)
 *   3. ~/.simmer/skills/ (ClawHub installs)
 *   4. Python package location (pip install simmer-sdk)
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import { spawn, spawnSync } from "child_process";
import * as fs from "fs";
import * as path from "path";
import * as os from "os";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ClawHubJson {
  emoji?: string;
  primaryEnv?: string;
  requires?: { env?: string[]; pip?: string[] };
  envVars?: Array<{ name: string; required?: boolean; description?: string }>;
  cron?: string | null;
  autostart?: boolean;
  automaton?: {
    managed?: boolean;
    entrypoint?: string | null;
  };
  tunables?: Array<{
    env: string;
    type: string;
    default: unknown;
    label?: string;
  }>;
  publish?: boolean;
}

interface Skill {
  slug: string;
  name: string;
  description: string;
  emoji: string;
  skillDir: string;
  clawhub: ClawHubJson;
  skillMdPath: string;
  isRunnable: boolean;
  entrypoint: string | null;
  requiredEnv: string[];
}

// ---------------------------------------------------------------------------
// Skill discovery
// ---------------------------------------------------------------------------

function findSkillsDir(): string | null {
  // 1. Explicit override
  if (process.env.SIMMER_SKILLS_DIR) {
    const d = process.env.SIMMER_SKILLS_DIR;
    if (fs.existsSync(d)) return d;
    process.stderr.write(
      `[simmer-mcp] SIMMER_SKILLS_DIR=${d} does not exist\n`
    );
  }

  // 2. Adjacent skills/ (repo usage — mcp/dist/index.js → mcp/ → simmer-sdk/skills/)
  const repoSkills = path.resolve(__dirname, "..", "..", "skills");
  if (fs.existsSync(repoSkills)) return repoSkills;

  // 3. ~/.simmer/skills/ (ClawHub install path)
  const clawHubSkills = path.join(os.homedir(), ".simmer", "skills");
  if (fs.existsSync(clawHubSkills)) return clawHubSkills;

  // 4. Find via Python package
  try {
    const result = spawnSync(
      "python3",
      [
        "-c",
        `import importlib.util, os; spec=importlib.util.find_spec('simmer_sdk'); root=os.path.dirname(os.path.dirname(spec.origin)); print(os.path.join(root,'skills'))`,
      ],
      { encoding: "utf8", timeout: 5000 }
    );
    if (result.status === 0) {
      const pySkills = result.stdout.trim();
      if (fs.existsSync(pySkills)) return pySkills;
    }
  } catch {
    // Python not available or simmer_sdk not installed
  }

  return null;
}

function parseSkillMdFrontmatter(
  content: string
): Record<string, string> | null {
  const match = content.match(/^---\n([\s\S]*?)\n---/);
  if (!match) return null;
  const fm: Record<string, string> = {};
  // Only parse top-level YAML keys (no leading whitespace) to avoid picking
  // up nested fields like envVars[].description overwriting the skill description.
  for (const line of match[1].split("\n")) {
    if (!line || line[0] === " " || line[0] === "\t" || line[0] === "-") continue;
    const colonIdx = line.indexOf(":");
    if (colonIdx === -1) continue;
    const key = line.slice(0, colonIdx).trim();
    const value = line
      .slice(colonIdx + 1)
      .trim()
      .replace(/^["']|["']$/g, "");
    if (key && value) fm[key] = value;
  }
  return fm;
}

function discoverSkills(skillsDir: string): Skill[] {
  const skills: Skill[] = [];

  let entries: fs.Dirent[];
  try {
    entries = fs.readdirSync(skillsDir, { withFileTypes: true });
  } catch {
    return skills;
  }

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const skillDir = path.join(skillsDir, entry.name);
    const clawHubPath = path.join(skillDir, "clawhub.json");
    const skillMdPath = path.join(skillDir, "SKILL.md");

    if (!fs.existsSync(clawHubPath)) continue;

    let clawhub: ClawHubJson;
    try {
      clawhub = JSON.parse(fs.readFileSync(clawHubPath, "utf8"));
    } catch {
      continue;
    }

    // Respect publish: false flag
    if (clawhub.publish === false) continue;

    // Extract description from SKILL.md frontmatter
    let description = `Simmer skill: ${entry.name}`;
    let displayName = entry.name;
    if (fs.existsSync(skillMdPath)) {
      const content = fs.readFileSync(skillMdPath, "utf8");
      const fm = parseSkillMdFrontmatter(content);
      if (fm) {
        if (fm.description) description = fm.description;
        if (fm.displayName) displayName = fm.displayName;
      }
    }

    const automaton = clawhub.automaton ?? {};
    const entrypoint = automaton.entrypoint ?? null;

    // A skill is "runnable" if it has an entrypoint OR a Python script in the dir
    let isRunnable = !!entrypoint;
    let resolvedEntrypoint = entrypoint;

    if (!isRunnable) {
      // Check for standalone Python scripts (like wallet_xray.py, x402_cli.py)
      const pyFiles = fs
        .readdirSync(skillDir)
        .filter((f) => f.endsWith(".py") && !f.startsWith("_"));
      if (pyFiles.length > 0) {
        isRunnable = true;
        resolvedEntrypoint = pyFiles[0];
      }
    }

    skills.push({
      slug: entry.name,
      name: displayName,
      description,
      emoji: clawhub.emoji ?? "🔮",
      skillDir,
      clawhub,
      skillMdPath,
      isRunnable,
      entrypoint: resolvedEntrypoint,
      requiredEnv: clawhub.requires?.env ?? [],
    });
  }

  return skills.sort((a, b) => a.slug.localeCompare(b.slug));
}

// ---------------------------------------------------------------------------
// Tool name helpers
// ---------------------------------------------------------------------------

function slugToToolName(slug: string): string {
  // polymarket-btc-up-down-trader → simmer_polymarket_btc_up_down_trader
  return "simmer_" + slug.replace(/-/g, "_");
}

// ---------------------------------------------------------------------------
// Skill execution
// ---------------------------------------------------------------------------

function runSkill(
  skill: Skill,
  args: Record<string, unknown>,
  timeoutMs: number
): Promise<string> {
  return new Promise((resolve, reject) => {
    if (!skill.isRunnable || !skill.entrypoint) {
      reject(new Error("Skill has no runnable entrypoint"));
      return;
    }

    const scriptPath = path.join(skill.skillDir, skill.entrypoint);
    if (!fs.existsSync(scriptPath)) {
      reject(new Error(`Entrypoint not found: ${scriptPath}`));
      return;
    }

    const env: Record<string, string> = { ...process.env } as Record<
      string,
      string
    >;
    env.AUTOMATON_MANAGED = "1";
    env.PYTHONUNBUFFERED = "1";

    // Pass any extra env overrides from args
    if (args.trading_venue && typeof args.trading_venue === "string") {
      env.TRADING_VENUE = args.trading_venue;
    }
    if (args.dry_run === true) {
      env.TRADING_VENUE = "sim";
    }

    // Build CLI args for scripts that accept them (wallet_xray, x402, etc.)
    const cliArgs: string[] = [];
    if (args.wallet_address && typeof args.wallet_address === "string") {
      cliArgs.push(args.wallet_address);
    }
    if (args.compare_address && typeof args.compare_address === "string") {
      cliArgs.push(args.compare_address);
      cliArgs.push("--compare");
    }
    if (args.json_output === true) {
      cliArgs.push("--json");
    }
    if (args.extra_args && Array.isArray(args.extra_args)) {
      cliArgs.push(...(args.extra_args as string[]));
    }

    const child = spawn("python3", [scriptPath, ...cliArgs], {
      env,
      cwd: skill.skillDir,
    });

    let stdout = "";
    let stderr = "";

    child.stdout.on("data", (d: Buffer) => {
      stdout += d.toString();
    });
    child.stderr.on("data", (d: Buffer) => {
      stderr += d.toString();
    });

    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      resolve(
        `[timeout after ${timeoutMs / 1000}s]\n\nStdout:\n${stdout}\n\nStderr:\n${stderr}`
      );
    }, timeoutMs);

    child.on("close", (code) => {
      clearTimeout(timer);
      if (code === 0 || stdout.length > 0) {
        const output = stdout || "(no output)";
        const extra = stderr ? `\n\n[stderr]\n${stderr}` : "";
        resolve(output + extra);
      } else {
        reject(
          new Error(
            `Skill exited with code ${code}\n\nStderr:\n${stderr}\n\nStdout:\n${stdout}`
          )
        );
      }
    });
  });
}

function getSkillDocs(skill: Skill): string {
  if (!fs.existsSync(skill.skillMdPath)) {
    return `# ${skill.name}\n\n${skill.description}\n\nNo documentation available.`;
  }
  return fs.readFileSync(skill.skillMdPath, "utf8");
}

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------

async function main() {
  const skillsDir = findSkillsDir();

  let skills: Skill[] = [];
  if (skillsDir) {
    skills = discoverSkills(skillsDir);
    process.stderr.write(
      `[simmer-mcp] Loaded ${skills.length} skills from ${skillsDir}\n`
    );
  } else {
    process.stderr.write(
      "[simmer-mcp] No skills directory found. Set SIMMER_SKILLS_DIR or install simmer-sdk.\n"
    );
  }

  const server = new Server(
    { name: "simmer-mcp", version: "0.1.0" },
    { capabilities: { tools: {} } }
  );

  // -------------------------------------------------------------------------
  // List tools
  // -------------------------------------------------------------------------
  server.setRequestHandler(ListToolsRequestSchema, async () => {
    const tools = [
      {
        name: "simmer_list_skills",
        description:
          "List all available Simmer SDK skills with their descriptions and status.",
        inputSchema: {
          type: "object",
          properties: {},
          required: [],
        },
      },
      {
        name: "simmer_get_skill_docs",
        description:
          "Get full documentation for a specific Simmer skill (returns SKILL.md content).",
        inputSchema: {
          type: "object",
          properties: {
            slug: {
              type: "string",
              description:
                "Skill slug, e.g. 'polymarket-btc-up-down-trader'. Use simmer_list_skills to see all slugs.",
            },
          },
          required: ["slug"],
        },
      },
    ];

    for (const skill of skills) {
      const isAutomaton = skill.clawhub.automaton?.managed === true;
      const toolName = slugToToolName(skill.slug);

      const properties: Record<string, unknown> = {
        timeout_seconds: {
          type: "number",
          description: "Maximum run time in seconds (default: 120)",
        },
      };

      if (isAutomaton) {
        properties.dry_run = {
          type: "boolean",
          description:
            "If true, runs against the paper-trading $SIM venue (default: true for safety)",
        };
        properties.trading_venue = {
          type: "string",
          enum: ["sim", "polymarket", "kalshi"],
          description:
            "Override trading venue. Defaults to sim (paper). Set to polymarket or kalshi for real money (requires dashboard approval).",
        };
      }

      // Wallet xray specific args
      if (skill.slug === "polymarket-wallet-xray") {
        properties.wallet_address = {
          type: "string",
          description: "Polymarket wallet address (0x...) to analyze",
        };
        properties.compare_address = {
          type: "string",
          description: "Optional second wallet address to compare against",
        };
        properties.json_output = {
          type: "boolean",
          description: "Return JSON output instead of formatted text",
        };
      }

      // Generic extra args passthrough
      properties.extra_args = {
        type: "array",
        items: { type: "string" },
        description: "Additional CLI arguments to pass to the skill script",
      };

      const runVerb = isAutomaton ? "run" : "execute";
      const safetyNote = isAutomaton
        ? " Defaults to dry_run=true (paper $SIM venue) for safety."
        : "";
      const docsNote = skill.isRunnable
        ? ""
        : " This is a reference skill — calling it returns the documentation.";

      tools.push({
        name: toolName,
        description: `${skill.emoji} ${skill.description}${docsNote}${safetyNote}`,
        inputSchema: {
          type: "object",
          properties,
          required: [],
        },
      });
    }

    return { tools };
  });

  // -------------------------------------------------------------------------
  // Call tools
  // -------------------------------------------------------------------------
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args = {} } = request.params;

    // Meta tools
    if (name === "simmer_list_skills") {
      if (skills.length === 0) {
        return {
          content: [
            {
              type: "text",
              text: "No skills found. Set SIMMER_SKILLS_DIR or clone the simmer-sdk repo and re-run.\n\nInstall: pip install simmer-sdk",
            },
          ],
        };
      }

      const lines = ["# Simmer Skills\n"];
      for (const skill of skills) {
        const runnable = skill.isRunnable ? "runnable" : "docs";
        const automaton =
          skill.clawhub.automaton?.managed === true ? "automaton" : "";
        const tags = [runnable, automaton].filter(Boolean).join(", ");
        lines.push(
          `**${skill.emoji} ${skill.name}** (\`${skill.slug}\`) [${tags}]`
        );
        lines.push(`  ${skill.description}`);
        if (skill.requiredEnv.length > 0) {
          lines.push(`  Requires: ${skill.requiredEnv.join(", ")}`);
        }
        lines.push("");
      }

      return { content: [{ type: "text", text: lines.join("\n") }] };
    }

    if (name === "simmer_get_skill_docs") {
      const slug = String(args.slug ?? "");
      const skill = skills.find((s) => s.slug === slug);
      if (!skill) {
        return {
          content: [
            {
              type: "text",
              text: `Skill '${slug}' not found. Use simmer_list_skills to see available skills.`,
            },
          ],
          isError: true,
        };
      }
      return { content: [{ type: "text", text: getSkillDocs(skill) }] };
    }

    // Per-skill tools
    const skill = skills.find((s) => slugToToolName(s.slug) === name);
    if (!skill) {
      return {
        content: [
          {
            type: "text",
            text: `Unknown tool: ${name}. Use simmer_list_skills to see available tools.`,
          },
        ],
        isError: true,
      };
    }

    // Non-runnable reference skills → return docs
    if (!skill.isRunnable) {
      return { content: [{ type: "text", text: getSkillDocs(skill) }] };
    }

    // Check required env vars
    const missingEnv = skill.requiredEnv.filter((e) => !process.env[e]);
    if (missingEnv.length > 0) {
      return {
        content: [
          {
            type: "text",
            text: `Missing required environment variables: ${missingEnv.join(", ")}\n\nSet them and restart the MCP server, or run:\n  export ${missingEnv[0]}=your_value`,
          },
        ],
        isError: true,
      };
    }

    const timeoutMs = typeof args.timeout_seconds === "number"
      ? args.timeout_seconds * 1000
      : 120_000;

    // Default automaton skills to dry_run (paper trading) for safety
    const effectiveArgs = {
      ...args,
      dry_run:
        skill.clawhub.automaton?.managed === true
          ? (args.dry_run !== false)  // default true unless explicitly false
          : args.dry_run,
    };

    try {
      const output = await runSkill(skill, effectiveArgs, timeoutMs);
      return { content: [{ type: "text", text: output }] };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      return {
        content: [{ type: "text", text: `Error running skill: ${msg}` }],
        isError: true,
      };
    }
  });

  const transport = new StdioServerTransport();
  await server.connect(transport);
  process.stderr.write("[simmer-mcp] Server started\n");
}

main().catch((err) => {
  process.stderr.write(`[simmer-mcp] Fatal: ${err}\n`);
  process.exit(1);
});
