#!/usr/bin/env node

import * as path from "node:path";
import * as fs from "node:fs";
import { fileURLToPath } from "node:url";

// ---------------------------------------------------------------------------
// CLI: install-skill command (runs before MCP server startup)
// ---------------------------------------------------------------------------

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

if (process.argv[2] === "install-skill") {
  const skillSrc = path.join(__dirname, "..", "skills", "autoresearch", "SKILL.md");
  if (!fs.existsSync(skillSrc)) {
    console.error(`Error: SKILL.md not found at ${skillSrc}`);
    process.exit(1);
  }

  const home = process.env.HOME || process.env.USERPROFILE || "";
  const runtimes: { name: string; dir: string }[] = [];

  const openclawDir = path.join(home, ".openclaw", "skills", "autoresearch");
  if (fs.existsSync(path.join(home, ".openclaw"))) {
    runtimes.push({ name: "OpenClaw", dir: openclawDir });
  }
  const hermesDir = path.join(home, ".hermes", "skills", "autoresearch");
  if (fs.existsSync(path.join(home, ".hermes"))) {
    runtimes.push({ name: "Hermes", dir: hermesDir });
  }

  if (runtimes.length === 0) {
    console.log("No supported runtime detected (~/.openclaw or ~/.hermes not found).");
    console.log(`\nManual install — copy the skill file to your agent's skills directory:`);
    console.log(`  ${skillSrc}`);
    console.log(`\nFor Claude Code, add the content to your project's CLAUDE.md.`);
    process.exit(0);
  }

  const content = fs.readFileSync(skillSrc, "utf-8");
  for (const rt of runtimes) {
    fs.mkdirSync(rt.dir, { recursive: true });
    fs.writeFileSync(path.join(rt.dir, "SKILL.md"), content);
    console.log(`✅ ${rt.name}: installed to ${rt.dir}/SKILL.md`);
  }

  console.log(`\nSkill installed. Your agent will load it on next session.`);
  process.exit(0);
}

// ---------------------------------------------------------------------------
// Auto-refresh installed SKILL.md from the bundled copy.
// ---------------------------------------------------------------------------

function refreshInstalledSkills(): void {
  const skillSrc = path.join(__dirname, "..", "skills", "autoresearch", "SKILL.md");
  if (!fs.existsSync(skillSrc)) return;
  const bundled = fs.readFileSync(skillSrc, "utf-8");

  const home = process.env.HOME || process.env.USERPROFILE || "";
  if (!home) return;

  const targets = [
    { name: "OpenClaw", file: path.join(home, ".openclaw", "skills", "autoresearch", "SKILL.md") },
    { name: "Hermes", file: path.join(home, ".hermes", "skills", "autoresearch", "SKILL.md") },
  ];

  for (const t of targets) {
    if (!fs.existsSync(t.file)) continue;
    try {
      const current = fs.readFileSync(t.file, "utf-8");
      if (current === bundled) continue;
      fs.writeFileSync(t.file, bundled);
      console.error(`[simmer-mcp] Refreshed ${t.name} SKILL.md (${t.file})`);
    } catch (e) {
      console.error(`[simmer-mcp] Could not refresh ${t.name} SKILL.md: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
}

refreshInstalledSkills();

// ---------------------------------------------------------------------------
// MCP Server imports
// ---------------------------------------------------------------------------

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

import type { ExperimentResult, ExperimentState } from "./core/types.js";
import {
  formatNum,
  isBetter,
  currentResults,
  findBaselineMetric,
  computeConfidence,
} from "./core/scoring.js";
import {
  defaultState,
  reconstructState,
  appendJsonl,
  writeJsonl,
} from "./core/state.js";
import { runCommand } from "./core/runner.js";
import { gitAutoCommit, gitRevert } from "./core/git.js";
import { SimmerApi } from "./api.js";
import { BackendError } from "./errors.js";
import { discoverSkills } from "./skill-discovery.js";
import { buildToolSchema, buildToolDescription, invokeSkillTool } from "./per-skill-tools.js";
import { listSkills, getSkillDocs } from "./docs-tools.js";
import { troubleshootError } from "./troubleshoot.js";
import { executeTrade, executeCancelOrder } from "./trade-primitives.js";
import { registerTool } from "./tool-registry.js";
import { probeRuntime } from "./runtime-probe.js";
import { BUNDLED_VERSION } from "./version.js";

// ---------------------------------------------------------------------------
// Config from environment
// ---------------------------------------------------------------------------

const apiKey = process.env.SIMMER_API_KEY || "";
const apiUrl = process.env.SIMMER_API_URL || "https://api.simmer.markets";
const maxExperiments = Math.max(0, Number(process.env.AUTORESEARCH_MAX_EXPERIMENTS) || 50);
const workspaceDir = process.cwd();

const simmer = apiKey ? new SimmerApi(apiKey, apiUrl, BUNDLED_VERSION) : null;

if (!apiKey) {
  console.error("[simmer-mcp] No SIMMER_API_KEY set — only free tools available (list_skills, get_skill_docs, troubleshoot_error).");
} else if (!apiKey.startsWith("sk_live_")) {
  console.error("[simmer-mcp] WARNING: SIMMER_API_KEY does not start with sk_live_ — key may be corrupted. Common cause: clipboard contamination during install (pbpaste reading the install command instead of the key). Inspect via: printenv SIMMER_API_KEY | cut -c1-20");
}

// ---------------------------------------------------------------------------
// Skill discovery (bundled at build time)
// ---------------------------------------------------------------------------

const BUNDLED_SKILLS_DIR = path.join(__dirname, "..", "bundled-skills");
const skills = discoverSkills(BUNDLED_SKILLS_DIR);

// ---------------------------------------------------------------------------
// Version check (non-blocking)
// ---------------------------------------------------------------------------

function compareSemver(a: string, b: string): number {
  const parse = (v: string): number[] =>
    v.split("-")[0].split(".").map((p) => parseInt(p, 10) || 0);
  const [aMajor, aMinor = 0, aPatch = 0] = parse(a);
  const [bMajor, bMinor = 0, bPatch = 0] = parse(b);
  if (aMajor !== bMajor) return aMajor - bMajor;
  if (aMinor !== bMinor) return aMinor - bMinor;
  return aPatch - bPatch;
}

async function checkLatestVersion(): Promise<void> {
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 3000);
    const resp = await fetch("https://registry.npmjs.org/simmer-mcp/latest", {
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    if (!resp.ok) return;
    const data = (await resp.json()) as { version?: string };
    const latest = data.version;
    if (!latest) return;
    if (compareSemver(BUNDLED_VERSION, latest) >= 0) return;
    console.error(
      `[simmer-mcp] ⚠ Update available: simmer-mcp ${BUNDLED_VERSION} → ${latest}. ` +
      `Restart your agent session to pick up the latest.`,
    );
  } catch {
    // Registry unreachable — non-fatal.
  }
}

checkLatestVersion();

// ---------------------------------------------------------------------------
// State (autoresearch)
// ---------------------------------------------------------------------------

let state: ExperimentState = reconstructState(workspaceDir);
if (state.results.length > 0) {
  console.error(
    `[simmer-mcp] Restored ${state.results.length} experiments from JSONL (segment ${state.currentSegment})`,
  );
}

// ---------------------------------------------------------------------------
// Pro-gate helper (Task 22 — Codex CRITICAL #1)
// ---------------------------------------------------------------------------

/**
 * Verifies the API key has Pro access before running an experiment.
 * Throws BackendError(403) if not Pro; swallows network failures (non-blocking).
 */
async function assertProForRunExperiment(api: SimmerApi): Promise<void> {
  await api.checkPro();
}

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------

const server = new McpServer({
  name: "simmer-mcp",
  version: BUNDLED_VERSION,
});

// ===========================================================================
// FREE TOOLS — always available, no API key required
// ===========================================================================

server.tool(
  "list_skills",
  "List all Simmer trading skills available in this MCP server. Returns slug, name, version, tier, and whether the skill requires a Pro plan.",
  {},
  async () => {
    const list = listSkills(skills);
    return {
      content: [{
        type: "text" as const,
        text: JSON.stringify(list, null, 2),
      }],
    };
  },
);

server.tool(
  "get_skill_docs",
  "Get the full SKILL.md documentation for a specific Simmer skill. Includes description, parameters, usage examples, and troubleshooting tips.",
  { slug: z.string().describe("Skill slug (e.g. 'polymarket-fast-loop')") },
  async ({ slug }) => {
    const r = getSkillDocs(skills, slug);
    return r;
  },
);

server.tool(
  "troubleshoot_error",
  "Look up a Simmer API error and get a fix. Pass the error message or JSON response from a failed API call. Returns a matched fix or falls back to docs.",
  { error_text: z.string().describe("The error message or response body from a failed Simmer API call") },
  async ({ error_text }) => {
    const r = await troubleshootError(error_text, apiUrl);
    const parts: string[] = [];
    if (r.matched) {
      parts.push(`✅ Matched: ${r.fix}`);
    } else {
      parts.push(`ℹ️ No pattern match. ${r.fix}`);
    }
    if (r.source) parts.push(`(source: ${r.source})`);
    return { content: [{ type: "text" as const, text: parts.join("\n\n") }] };
  },
);

// ===========================================================================
// PRO TOOLS — requires SIMMER_API_KEY
// ===========================================================================

if (simmer) {

  // --- init_experiment ---

  registerTool(server, {
    name: "init_experiment",
    description: "Initialize the autoresearch experiment session (step 1 of the init → run → log loop). Call once before the first run_experiment to set the name, primary metric, unit, and direction. Re-calling archives previous results and starts a new segment.",
    schema: {
      name: z.string().describe('Human-readable name (e.g. "Optimizing polymarket-ai-divergence for P&L")'),
      metric_name: z.string().describe('Primary metric name (e.g. "pnl", "trades", "sharpe")'),
      skill_slug: z.string().describe('Skill slug for API tracking (e.g. "polymarket-fast-loop")'),
      metric_unit: z.string().optional().describe('Unit (e.g. "$", "%", "")'),
      direction: z.enum(["lower", "higher"]).optional().describe('Whether "lower" or "higher" is better. Default: "higher"'),
    },
    mutates: false,
    handler: async ({ name, metric_name, skill_slug, metric_unit, direction }: {
      name: string;
      metric_name: string;
      skill_slug: string;
      metric_unit?: string;
      direction?: "lower" | "higher";
    }, _ctx) => {
      const isReinit = state.results.length > 0;

      state.name = name;
      state.skillSlug = skill_slug;
      state.metricName = metric_name;
      state.consecutiveCrashes = 0;
      state.paused = false;
      state.metricUnit = metric_unit ?? "$";
      if (direction) state.bestDirection = direction;

      if (!isReinit && simmer) {
        try {
          const apiState = await simmer.getResumeState(skill_slug);
          if (apiState && apiState.last_experiment_number > 0) {
            state.bestMetric = apiState.best_metric;
            state.bestDirection = apiState.best_direction ?? state.bestDirection;
            state.currentSegment = apiState.current_segment;
            for (let i = 1; i <= apiState.last_experiment_number; i++) {
              state.results.push({
                commit: "",
                metric: 0,
                metrics: {},
                status: "keep",
                description: "(restored from API)",
                timestamp: 0,
                segment: apiState.current_segment,
                confidence: null,
              });
            }
            return {
              content: [{
                type: "text" as const,
                text: `✅ Resumed from API: "${name}"\n` +
                  `${apiState.last_experiment_number} previous experiments (segment ${apiState.current_segment})\n` +
                  `Best ${state.metricName}: ${formatNum(apiState.best_metric, state.metricUnit)}\n` +
                  `Continuing from experiment #${apiState.last_experiment_number + 1}`,
              }],
            };
          }
        } catch {
          // Fall through to fresh init
        }
      }

      if (isReinit) state.currentSegment++;
      state.bestMetric = null;
      state.secondaryMetrics = [];

      const configData = {
        type: "config",
        name: state.name,
        skillSlug: state.skillSlug,
        metricName: state.metricName,
        metricUnit: state.metricUnit,
        bestDirection: state.bestDirection,
      };

      try {
        if (isReinit) {
          appendJsonl(workspaceDir, configData);
        } else {
          writeJsonl(workspaceDir, configData);
        }
      } catch (e) {
        return {
          content: [{ type: "text" as const, text: `⚠️ Failed to write autoresearch.jsonl: ${e instanceof Error ? e.message : String(e)}` }],
          isError: true,
        };
      }

      const reinitNote = isReinit ? " (re-initialized — previous results archived, new baseline needed)" : "";
      return {
        content: [{
          type: "text" as const,
          text: `✅ Experiment initialized: "${name}"${reinitNote}\n` +
            `Metric: ${state.metricName} (${state.metricUnit || "unitless"}, ${state.bestDirection} is better)\n` +
            (maxExperiments > 0 ? `Budget: ${maxExperiments} experiments this session\n` : "") +
            `Config written to autoresearch.jsonl. Now run the baseline with run_experiment.`,
        }],
      };
    },
  });

  // --- run_experiment (Pro-gated — Task 22, Codex CRITICAL #1) ---

  registerTool(server, {
    name: "run_experiment",
    description: "Run a shell command as an experiment (step 2 of the init → run → log autoresearch loop). Times execution, captures output, detects pass/fail. Not a general shell tool — use only for autoresearch experiments. Requires Pro plan.",
    schema: {
      command: z.string().describe("Shell command to run"),
      timeout_seconds: z.number().optional().describe("Kill after this many seconds (default: 600)"),
    },
    mutates: false,
    handler: async ({ command, timeout_seconds }: { command: string; timeout_seconds?: number }, _ctx) => {
      // Per-call Pro check — Codex CRITICAL #1
      try {
        await assertProForRunExperiment(simmer!);
      } catch (e) {
        if (e instanceof BackendError) return e.toMcpResponse();
        throw e;
      }

      if (state.paused) {
        return {
          content: [{ type: "text" as const, text: "🛑 Autoresearch is paused due to crashes. Fix the issue, then call init_experiment to resume." }],
          isError: true,
        };
      }
      if (maxExperiments > 0 && state.results.length >= maxExperiments) {
        return {
          content: [{
            type: "text" as const,
            text: `🛑 Experiment limit reached (${maxExperiments}). Session complete.\n` +
              `Review results. To continue, start a new session or set AUTORESEARCH_MAX_EXPERIMENTS higher.`,
          }],
        };
      }

      const timeout = (timeout_seconds ?? 600) * 1000;
      const result = await runCommand(command, { timeoutMs: timeout, cwd: workspaceDir });

      let text = "";
      if (result.timedOut) {
        text += `⏰ TIMEOUT after ${result.durationSeconds.toFixed(1)}s\n`;
      } else if (!result.passed) {
        text += `💥 FAILED (exit code ${result.exitCode}) in ${result.durationSeconds.toFixed(1)}s\n`;
      } else {
        text += `✅ PASSED in ${result.durationSeconds.toFixed(1)}s\n`;
      }

      if (state.bestMetric !== null) {
        text += `📊 Current best ${state.metricName}: ${formatNum(state.bestMetric, state.metricUnit)}\n`;
      }

      const output = (result.stdout + "\n" + result.stderr).trim();
      const tail = output.split("\n").slice(-80).join("\n");
      text += `\nLast 80 lines of output:\n${tail}`;

      return { content: [{ type: "text" as const, text }] };
    },
  });

  // --- log_experiment ---

  registerTool(server, {
    name: "log_experiment",
    description: 'Record an experiment result (step 3 of the init → run → log autoresearch loop). Status values: "keep" = improved, auto-commits via git; "discard" = worse, reverts changes; "crash" = skill broke, reverts and pauses after 3 consecutive; "checks_failed" = ran but post-run checks failed, reverts. Reports confidence score after 3+ runs per segment.',
    schema: {
      commit: z.string().describe("Git commit hash (short, 7 chars)"),
      metric: z.number().describe("Primary metric value. 0 for crashes."),
      status: z.enum(["keep", "discard", "crash", "checks_failed"]).describe("keep if improved, discard if worse, crash if skill broke, checks_failed if ran but post-run checks failed"),
      description: z.string().describe("Short description of what this experiment tried"),
      metrics: z.record(z.string(), z.number()).optional().describe('Secondary metrics as { name: value }'),
      asi: z.record(z.string(), z.unknown()).optional().describe('Actionable Side Information — free-form diagnostics'),
      force: z.boolean().optional().describe("Set true to allow adding a new secondary metric not previously tracked"),
    },
    mutates: false,
    handler: async ({ commit, metric, status, description, metrics: secondaryMetrics, asi, force }: {
      commit: string;
      metric: number;
      status: "keep" | "discard" | "crash" | "checks_failed";
      description: string;
      metrics?: Record<string, number>;
      asi?: Record<string, unknown>;
      force?: boolean;
    }, _ctx) => {
      const secMetrics: Record<string, number> = secondaryMetrics ?? {};
      const forceAdd = force ?? false;

      if (state.secondaryMetrics.length > 0) {
        const knownNames = new Set(state.secondaryMetrics.map((m) => m.name));
        const providedNames = new Set(Object.keys(secMetrics));

        const missing = [...knownNames].filter((n) => !providedNames.has(n));
        if (missing.length > 0) {
          return {
            content: [{
              type: "text" as const,
              text: `❌ Missing secondary metrics: ${missing.join(", ")}\n` +
                `Expected: ${[...knownNames].join(", ")}\nGot: ${[...providedNames].join(", ") || "(none)"}\n` +
                `Fix: include ${missing.map((m) => `"${m}": <value>`).join(", ")} in metrics.`,
            }],
            isError: true,
          };
        }

        const newMetrics = [...providedNames].filter((n) => !knownNames.has(n));
        if (newMetrics.length > 0 && !forceAdd) {
          return {
            content: [{
              type: "text" as const,
              text: `❌ New secondary metric(s) not previously tracked: ${newMetrics.join(", ")}\n` +
                `Existing: ${[...knownNames].join(", ")}\nCall again with force: true to add, or remove from metrics.`,
            }],
            isError: true,
          };
        }
      }

      const experiment: ExperimentResult = {
        commit: commit.slice(0, 7),
        metric,
        metrics: secMetrics,
        status,
        description,
        timestamp: Date.now(),
        segment: state.currentSegment,
        confidence: null,
        asi: asi as Record<string, unknown> | undefined,
      };

      state.results.push(experiment);

      if (status === "crash" || status === "checks_failed") {
        state.consecutiveCrashes++;
        const curCount = currentResults(state.results, state.currentSegment).length;
        if (curCount === 1) state.paused = true;
        if (state.consecutiveCrashes >= 3) state.paused = true;
      } else {
        state.consecutiveCrashes = 0;
      }

      for (const name of Object.keys(secMetrics)) {
        if (!state.secondaryMetrics.find((m) => m.name === name)) {
          let unit = "";
          if (name.includes("pnl") || name.includes("budget")) unit = "$";
          else if (name.includes("rate") || name.includes("pct")) unit = "%";
          state.secondaryMetrics.push({ name, unit });
        }
      }

      state.bestMetric = findBaselineMetric(state.results, state.currentSegment);
      state.confidence = computeConfidence(state.results, state.currentSegment, state.bestDirection);
      experiment.confidence = state.confidence;

      const curCount = currentResults(state.results, state.currentSegment).length;
      let text = `Logged #${state.results.length}: ${experiment.status} — ${experiment.description}`;

      if (state.bestMetric !== null) {
        text += `\nBaseline ${state.metricName}: ${formatNum(state.bestMetric, state.metricUnit)}`;
        if (curCount > 1 && status === "keep" && metric !== 0) {
          const delta = metric - state.bestMetric;
          const pct = state.bestMetric !== 0
            ? ((delta / Math.abs(state.bestMetric)) * 100).toFixed(1)
            : "∞";
          const sign = delta > 0 ? "+" : "";
          text += ` | this: ${formatNum(metric, state.metricUnit)} (${sign}${pct}%)`;
        }
      }

      if (Object.keys(secMetrics).length > 0) {
        const parts: string[] = [];
        for (const [name, value] of Object.entries(secMetrics)) {
          const def = state.secondaryMetrics.find((m) => m.name === name);
          parts.push(`${name}: ${formatNum(value, def?.unit ?? "")}`);
        }
        text += `\nSecondary: ${parts.join("  ")}`;
      }

      if (state.confidence !== null) {
        const confStr = state.confidence.toFixed(1);
        if (state.confidence >= 2.0) {
          text += `\n📊 Confidence: ${confStr}× noise floor — improvement is likely real`;
        } else if (state.confidence >= 1.0) {
          text += `\n📊 Confidence: ${confStr}× noise floor — marginal`;
        } else {
          text += `\n⚠️ Confidence: ${confStr}× noise floor — within noise`;
        }
      }

      text += `\n(${state.results.length} experiments total)`;

      if (status === "keep") {
        const gitResult = await gitAutoCommit(workspaceDir, description, state.metricName, metric, secMetrics);
        if (gitResult.committed) {
          text += `\n📝 Git: committed — ${gitResult.message}`;
          if (gitResult.newSha) experiment.commit = gitResult.newSha;
        } else {
          text += `\n📝 Git: ${gitResult.message}`;
        }
      } else {
        await gitRevert(workspaceDir);
        text += `\n📝 Git: reverted (${status})`;
      }

      try {
        appendJsonl(workspaceDir, { run: state.results.length, ...experiment });
      } catch {
        // Don't fail if write fails
      }

      if (simmer) {
        simmer.postExperiment({
          skill_slug: state.skillSlug ?? "unknown",
          experiment_number: state.results.length,
          segment: state.currentSegment,
          status,
          metric_name: state.metricName,
          metric_value: metric === 0 && status === "crash" ? null : metric,
          metric_unit: state.metricUnit,
          best_direction: state.bestDirection,
          secondary_metrics: secMetrics,
          description,
          commit_hash: experiment.commit,
        }).catch(() => { /* Silent — JSONL is primary */ });

        if (status === "keep" && state.metricName?.toLowerCase().includes("pnl")) {
          simmer.getOutcomes(state.skillSlug ?? "unknown", "").then((apiOutcome) => {
            if (apiOutcome && apiOutcome.trades > 0 && metric !== null) {
              const diff = Math.abs(apiOutcome.pnl - metric);
              if (diff > 1.0) {
                console.error(
                  `[simmer-mcp] Metric discrepancy: agent reported ${metric}, API shows ${apiOutcome.pnl} (diff: ${diff.toFixed(2)})`,
                );
              }
            }
          }).catch(() => { /* Silent */ });
        }
      }

      if (maxExperiments > 0) {
        const threshold = Math.floor(maxExperiments * 0.8);
        if (state.results.length === threshold) {
          text += `\n\n⏳ ${maxExperiments - state.results.length} experiments remaining (limit: ${maxExperiments}).`;
        }
      }

      if (state.paused) {
        const pauseCount = currentResults(state.results, state.currentSegment).length;
        if (pauseCount === 1) {
          text += `\n\n🛑 BASELINE CRASHED — autoresearch paused.\nFix the issue, then call init_experiment to start fresh.`;
        } else {
          text += `\n\n⚠️ ${state.consecutiveCrashes} CONSECUTIVE CRASHES — autoresearch paused.\nInvestigate, fix, then call init_experiment to resume.`;
        }
      }

      return { content: [{ type: "text" as const, text }] };
    },
  });

  // --- backtest_experiment (Task 21 — throws BackendError on 4xx/5xx) ---

  registerTool(server, {
    name: "backtest_experiment",
    description: "Replay historical trades against new config params without executing real trades. Returns simulated P&L. Requires Pro plan.",
    schema: {
      skill_slug: z.string().describe("Skill to backtest"),
      config: z.record(z.string(), z.number()).describe("Config overrides to test"),
      days: z.number().optional().describe("Days of history to replay (default 7, max 30)"),
      venue: z.string().optional().describe("'sim' or 'polymarket' (default 'sim')"),
    },
    mutates: false,
    handler: async ({ skill_slug, config, days, venue }: {
      skill_slug: string;
      config: Record<string, number>;
      days?: number;
      venue?: string;
    }, _ctx) => {
      let result;
      try {
        result = await simmer!.backtest({ skill_slug, config, days, venue });
      } catch (e) {
        if (e instanceof BackendError) return e.toMcpResponse();
        return {
          content: [{ type: "text" as const, text: "❌ Backtest failed — API unreachable or unexpected error." }],
          isError: true,
        };
      }

      const text = [
        `📊 Backtest: ${result.trades_included}/${result.trades_total} trades pass new config`,
        `Simulated P&L: ${result.simulated_pnl} (original: ${result.original_pnl})`,
        result.improvement_pct !== null
          ? `Improvement: ${result.improvement_pct > 0 ? "+" : ""}${result.improvement_pct}%`
          : "No baseline P&L for comparison",
        `Win rate: ${(result.win_rate * 100).toFixed(1)}%`,
        `Excluded: ${result.trades_excluded} trades filtered out by new config`,
      ].join("\n");

      return { content: [{ type: "text" as const, text }] };
    },
  });

  // ===========================================================================
  // RAW TRADE PRIMITIVES — direct REST, no Python subprocess
  // ===========================================================================

  // --- simmer_trade ---

  registerTool(server, {
    name: "simmer_trade",
    description: [
      "Execute or dry-run a single direct trade on a Simmer market.",
      "Use this for one-off trades; use per-skill tools (simmer_<slug>) for strategy-driven runs.",
      "",
      "Safety triple-gate: a live trade on a real venue requires (1) dry_run=false,",
      "(2) venue='polymarket' or 'kalshi', AND (3) SIMMER_MCP_ALLOW_LIVE=true env.",
      "Any missing gate coerces to sim with a warning. Default: dry_run=true (paper mode).",
      "",
      "Use 'amount' (USD) for action='buy', 'shares' for action='sell'.",
    ],
    schema: {
      market_id: z.string().describe("Simmer market UUID (from simmer_get_markets)"),
      side: z.enum(["yes", "no"]).describe("Which outcome to trade"),
      action: z.enum(["buy", "sell"]).default("buy").describe("'buy' to open/add to a position (requires amount); 'sell' to close/reduce (requires shares)."),
      amount: z.number().optional().describe("USD to spend. Required for action='buy'."),
      shares: z.number().optional().describe("Shares to sell. Required for action='sell'."),
      venue: z.enum(["sim", "polymarket", "kalshi"]).default("sim").describe("Trading venue. 'sim' = $SIM paper mode."),
      dry_run: z.boolean().default(true).describe("If false (+ SIMMER_MCP_ALLOW_LIVE=true + live venue), places a real order. Default: paper mode."),
      reasoning: z.string().optional().describe("Why you're making this trade (stored for P&L attribution and flip-flop detection)"),
      source: z.string().optional().describe("Source tag for grouping trades (e.g. 'sdk:my-strategy')"),
    },
    mutates: false,
    handler: async (args, ctx) => executeTrade(simmer!, args as Parameters<typeof executeTrade>[1], ctx),
  });

  // --- simmer_get_briefing ---

  registerTool(server, {
    name: "simmer_get_briefing",
    description: [
      "Get a single-call agent briefing: portfolio balance, open positions,",
      "top opportunities, and recent performance. Replaces 5-6 separate API calls.",
      "Ideal for agent heartbeat check-ins.",
    ],
    schema: {
      since: z.string().optional().describe("ISO timestamp — only show changes since this time. Defaults to 24h ago."),
    },
    mutates: false,
    handler: async ({ since }: { since?: string }, _ctx) => {
      try {
        const result = await simmer!.getBriefing(since);
        return {
          content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
        };
      } catch (e) {
        if (e instanceof BackendError) return e.toMcpResponse();
        return {
          content: [{ type: "text" as const, text: `❌ Briefing failed: ${e instanceof Error ? e.message : String(e)}` }],
          isError: true,
        };
      }
    },
  });

  // --- simmer_get_markets ---

  registerTool(server, {
    name: "simmer_get_markets",
    description: [
      "List or search markets available for trading.",
      "Use 'q' for text search. Results include price, volume, and venue.",
      "Unfiltered browse is windowed to the most-recent slice of the catalogue,",
      "so to reach the full set for a category (e.g. World Cup) pass a filter:",
      "tags (e.g. tags='world-cup'), a 'q' query, or sort='volume'. Filters are",
      "applied server-side BEFORE the window, so they reach older-but-active",
      "markets a plain browse would miss.",
      "Default limit is 50; max is 500.",
    ],
    schema: {
      q: z.string().optional().describe("Text search query (min 2 chars, case-insensitive)"),
      limit: z.number().optional().describe("Max markets to return (default 50, max 500)"),
      venue: z.enum(["sim", "polymarket", "kalshi"]).optional().describe("Filter by venue"),
      status: z.string().optional().describe("Filter by status ('active', 'resolved', etc.)"),
      tags: z.string().optional().describe("Comma-separated tags to filter by (e.g. 'weather,crypto')"),
      sort: z.enum(["volume", "created"]).optional().describe("Sort order: 'volume' (24h) or 'created'"),
    },
    mutates: false,
    handler: async (args: {
      q?: string; limit?: number; venue?: "sim" | "polymarket" | "kalshi";
      status?: string; tags?: string; sort?: "volume" | "created";
    }, _ctx) => {
      try {
        const result = await simmer!.getMarkets(args);
        return {
          content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
        };
      } catch (e) {
        if (e instanceof BackendError) return e.toMcpResponse();
        return {
          content: [{ type: "text" as const, text: `❌ Markets fetch failed: ${e instanceof Error ? e.message : String(e)}` }],
          isError: true,
        };
      }
    },
  });

  // --- simmer_get_market_context ---

  registerTool(server, {
    name: "simmer_get_market_context",
    description: [
      "Get rich context for a specific market: price history, your position,",
      "recent trades, flip-flop detection, slippage estimates, and edge analysis.",
      "Optionally pass my_probability (0-1) for edge calculation and a TRADE/HOLD recommendation;",
      "without it, context is returned without a recommendation.",
    ],
    schema: {
      market_id: z.string().describe("Simmer market UUID"),
      my_probability: z.number().min(0).max(1).optional().describe("Your probability estimate (0-1) for edge calculation and TRADE/HOLD recommendation"),
      venue: z.enum(["sim", "polymarket", "kalshi", "all"]).optional().describe("Which venue's positions to include (default 'all')"),
    },
    mutates: false,
    handler: async ({ market_id, my_probability, venue }: {
      market_id: string;
      my_probability?: number;
      venue?: "sim" | "polymarket" | "kalshi" | "all";
    }, _ctx) => {
      try {
        const result = await simmer!.getMarketContext(market_id, { my_probability, venue });
        return {
          content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
        };
      } catch (e) {
        if (e instanceof BackendError) return e.toMcpResponse();
        return {
          content: [{ type: "text" as const, text: `❌ Market context failed: ${e instanceof Error ? e.message : String(e)}` }],
          isError: true,
        };
      }
    },
  });

  // --- simmer_cancel_order ---

  registerTool(server, {
    name: "simmer_cancel_order",
    description: [
      "Cancel a single open order by its order ID.",
      "",
      "Requires SIMMER_MCP_ALLOW_LIVE=true in your MCP env to operate.",
    ],
    schema: {
      order_id: z.string().describe("Order ID to cancel (from GET /api/sdk/orders/open)"),
    },
    mutates: true,
    handler: async (args, ctx) => executeCancelOrder(simmer!, args as { order_id: string }, ctx),
  });

  // --- per-skill tools ---

  // ===========================================================================
  // DATA QUERY TOOLS — read-only portfolio, positions, fleet
  // ===========================================================================

  registerTool(server, {
    name: "get_portfolio",
    description: [
      "Get portfolio summary: balance, total value, realized and unrealized P&L,",
      "position count, and per-venue breakdown.",
    ],
    schema: {},
    mutates: false,
    handler: async (_args, _ctx) => {
      const data = await simmer!.getPortfolio();
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    },
  });

  registerTool(server, {
    name: "get_positions",
    description: [
      "Get open positions with market question, side, size, entry price, current price, and P&L.",
      "Optionally filter by venue.",
    ],
    schema: {
      venue: z.enum(["sim", "polymarket", "kalshi"]).optional().describe("Filter positions by venue"),
    },
    mutates: false,
    handler: async ({ venue }: { venue?: string }, _ctx) => {
      const data = await simmer!.getPositions({ venue });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    },
  });

  registerTool(server, {
    name: "get_expiring_positions",
    description: "Get positions expiring within a time window. Use this to check what's about to resolve so you can exit or hold.",
    schema: {
      hours: z.number().optional().describe("Window in hours to look ahead (default: 24)"),
    },
    mutates: false,
    handler: async ({ hours }: { hours?: number }, _ctx) => {
      const data = await simmer!.getExpiringPositions({ hours });
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    },
  });

  registerTool(server, {
    name: "get_fleet_summary",
    description: [
      "Get fleet overview: all agents' positions, realized + unrealized P&L,",
      "trade counts, and active status. Use this to monitor multi-agent performance.",
    ],
    schema: {},
    mutates: false,
    handler: async (_args, _ctx) => {
      const data = await simmer!.getFleetSummary();
      return { content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }] };
    },
  });

  for (const skill of skills) {
    const capturedSkill = skill; // closure capture
    registerTool(server, {
      name: capturedSkill.toolName,
      description: buildToolDescription(capturedSkill),
      schema: buildToolSchema(capturedSkill),
      mutates: false,
      handler: async (args, _ctx) => {
        return invokeSkillTool(capturedSkill, args as Record<string, unknown>);
      },
    });
  }

} // end if (simmer)

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

async function main() {
  // Runtime probe (startup diagnostic)
  const probe = await probeRuntime();
  const probeLines = [
    `python3: ${probe.python3.detected ? `v${probe.python3.version} (${probe.python3.path})` : `not found at ${probe.python3.path} (${probe.python3.installHint})`}`,
    `simmer-sdk: ${probe.simmerSdk.detected ? `v${probe.simmerSdk.version}` : `not installed (${probe.simmerSdk.installHint})`}`,
    `git: ${probe.git.detected ? `v${probe.git.version}` : `not found`}`,
  ].join(" | ");

  const freeCount = 3;
  const proCount = simmer ? 9 + skills.length : 0; // 4 autoresearch + 5 raw primitives
  const totalTools = freeCount + proCount;
  const tier = simmer ? "free + autoresearch + per-skill" : "free only";

  console.error(
    `[simmer-mcp] v${BUNDLED_VERSION} | tools: ${totalTools} (${tier}) | skills: ${skills.length} bundled`
  );
  console.error(`[simmer-mcp] runtime: ${probeLines}`);

  if (!probe.python3.detected) {
    console.error("[simmer-mcp] ⚠ python3 not found — per-skill execution will fail. Install python3 to use trading skills.");
  }
  if (!probe.simmerSdk.detected && simmer) {
    console.error("[simmer-mcp] ⚠ simmer-sdk not installed — per-skill execution will fail. Run: pip install simmer-sdk>=0.13.0");
  }

  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("[simmer-mcp] MCP server started (stdio)");
}

main().catch((err) => {
  console.error("[simmer-mcp] Fatal:", err);
  process.exit(1);
});
