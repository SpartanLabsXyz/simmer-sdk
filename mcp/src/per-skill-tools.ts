/**
 * Per-skill execution tool generation.
 * One simmer_<slug> tool per Tier B (trading) skill, schema from clawhub.json tunables.
 */

import { z, ZodType } from "zod";
import * as path from "node:path";
import type { Skill, Tunable } from "./core/types.js";
import { filterBlockedFlags } from "./blocked-flags.js";
import { buildEnv, envToArgName } from "./env-translation.js";
import { parseSkillOutput } from "./output-parsing.js";
import { runSkillProcess } from "./skill-runner.js";

function tunableToZod(t: Tunable): ZodType<unknown> {
  if (t.type === "number") {
    let s: z.ZodNumber = z.number();
    if (t.range) s = s.min(t.range[0]).max(t.range[1]);
    return s.default(t.default).describe(t.label);
  }
  if (t.type === "string") {
    if (t.enum && t.enum.length > 0) {
      const [head, ...rest] = t.enum;
      return z.enum([head, ...rest] as [string, ...string[]]).default(t.default).describe(t.label);
    }
    return z.string().default(t.default).describe(t.label);
  }
  return z.boolean().default(t.default).describe(t.label);
}

// Defense-in-depth: tunables must not shadow gate-relevant schema fields.
const RESERVED_ARG_NAMES = new Set(["dry_run", "trading_venue", "extra_args", "timeout_s"]);

export function buildToolSchema(skill: Skill): z.ZodObject<z.ZodRawShape> {
  const tunableShape: Record<string, ZodType<unknown>> = {};
  for (const t of skill.tunables) {
    const argName = envToArgName(t.env);
    if (RESERVED_ARG_NAMES.has(argName)) continue;
    tunableShape[argName] = tunableToZod(t).optional();
  }

  return z.object({
    dry_run: z.boolean().default(true).describe(
      "If false, may place real orders (requires SIMMER_MCP_ALLOW_LIVE=true env). Default: paper mode."
    ),
    trading_venue: z.enum(["sim", "polymarket", "kalshi"]).default("sim").describe(
      "Trading venue. 'sim' = simulated $SIM venue."
    ),
    extra_args: z.array(z.string()).optional().describe(
      "Pass-through CLI flags. Live-trading flags (--live, --no-dry-run, --mode=live, etc.) are filtered."
    ),
    ...tunableShape,
  });
}

export function buildToolDescription(skill: Skill): string {
  const parts = ["Simmer", skill.tier];
  if (skill.status) parts.push(skill.status);
  const prefix = `[${parts.join(" · ")}]`;

  const lines = [
    `${prefix} ${skill.name} v${skill.version}`,
    "",
    skill.description,
  ];

  if (skill.hasDisclaimer) {
    lines.push("");
    lines.push("Read DISCLAIMER.md before connecting real funds (default is dry-run paper mode).");
  }

  return lines.join("\n");
}

const DEFAULT_TIMEOUT_MS = 60_000;
const MAX_TIMEOUT_MS = 300_000;

export interface InvokeSkillResponse {
  content: Array<{ type: "text"; text: string }>;
  isError: boolean;
  _meta?: Record<string, unknown>;
}

export interface InvokeSkillOptions {
  processEnv?: Record<string, string | undefined>;
}

export async function invokeSkillTool(
  skill: Skill,
  args: Record<string, unknown>,
  options: InvokeSkillOptions = {}
): Promise<InvokeSkillResponse> {
  if (!skill.entrypoint) {
    return {
      content: [{ type: "text", text: `Skill ${skill.slug} has no entrypoint (Tier A, instruction-only).` }],
      isError: true,
    };
  }

  const skillPath = path.join(skill.skillDir, skill.entrypoint);
  const allowExtraArgs = (options.processEnv ?? process.env).SIMMER_MCP_ALLOW_EXTRA_ARGS === "true";
  const rawExtraArgs = (args.extra_args as unknown[]) ?? [];
  const argv = allowExtraArgs
    ? [skillPath, ...filterBlockedFlags(rawExtraArgs)]
    : [skillPath];

  const env = buildEnv(skill, args, { processEnv: options.processEnv });

  const timeoutS = typeof args.timeout_s === "number" ? args.timeout_s : DEFAULT_TIMEOUT_MS / 1000;
  const timeoutMs = Math.min(Math.max(timeoutS * 1000, 1000), MAX_TIMEOUT_MS);

  const result = await runSkillProcess({
    file: "python3",
    args: argv,
    env,
    timeoutMs,
  });

  if (result.timedOut) {
    return {
      content: [{ type: "text", text: `TIMEOUT after ${(result.durationMs / 1000).toFixed(1)}s\n\nStdout tail:\n${tailLines(result.stdout, 40)}\n\nStderr tail:\n${tailLines(result.stderr, 20)}` }],
      isError: true,
      _meta: { timeout_ms: timeoutMs, duration_ms: result.durationMs },
    };
  }

  const parsed = parseSkillOutput(result.stdout);
  const exitOk = result.exitCode === 0;

  const summary = exitOk
    ? `✅ ${skill.slug} completed in ${(result.durationMs / 1000).toFixed(1)}s`
    : `⚠️ ${skill.slug} exited with code ${result.exitCode}`;

  const sections = [summary];
  if (parsed.result) sections.push("Result:\n" + JSON.stringify(parsed.result, null, 2));
  sections.push("Log:\n" + tailLines(result.stdout, 40));
  if (result.stderr.trim()) sections.push("Errors:\n" + tailLines(result.stderr, 20));

  return {
    content: [{ type: "text", text: sections.join("\n\n") }],
    isError: !exitOk,
    _meta: {
      exit_code: result.exitCode,
      duration_ms: result.durationMs,
      ...(parsed.result ? { result: parsed.result } : {}),
    },
  };
}

function tailLines(s: string, n: number): string {
  return s.split("\n").slice(-n).join("\n");
}
