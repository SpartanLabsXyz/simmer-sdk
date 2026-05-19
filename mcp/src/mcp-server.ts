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

  // Detect runtimes
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
//
// SKILL.md is written to ~/.openclaw/skills/... and ~/.hermes/skills/... at
// install time. It never updates afterwards — agents keep reading a stale
// copy across npm upgrades. On every MCP server boot, if a runtime dir
// already has an installed SKILL.md whose content differs from the bundled
// version, silently overwrite it so the fix propagates on restart.
//
// Only touches dirs the user already opted into (presence of ~/.openclaw or
// ~/.hermes). Never creates new install paths on its own.
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
    if (!fs.existsSync(t.file)) continue; // only refresh what's already installed
    try {
      const current = fs.readFileSync(t.file, "utf-8");
      if (current === bundled) continue;
      fs.writeFileSync(t.file, bundled);
      console.error(`[autoresearch] Refreshed ${t.name} SKILL.md (${t.file})`);
    } catch (e) {
      // Non-fatal — don't block MCP startup on a refresh hiccup
      console.error(`[autoresearch] Could not refresh ${t.name} SKILL.md: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
}

refreshInstalledSkills();

// ---------------------------------------------------------------------------
// MCP Server
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

// ---------------------------------------------------------------------------
// Bundled version — single source of truth for this MCP server build.
// Bumped at every release; compared to npm registry on boot to surface
// stale-install warnings to the agent.
// ---------------------------------------------------------------------------

const BUNDLED_VERSION = "2.3.0";

// ---------------------------------------------------------------------------
// Config from environment
// ---------------------------------------------------------------------------

const apiKey = process.env.SIMMER_API_KEY || "";
const apiUrl = process.env.SIMMER_API_URL || "https://api.simmer.markets";
const maxExperiments = Math.max(0, Number(process.env.AUTORESEARCH_MAX_EXPERIMENTS) || 50);
const workspaceDir = process.cwd();

if (!apiKey) {
  console.error("[autoresearch] WARNING: SIMMER_API_KEY not set. API sync disabled.");
}

const simmer = apiKey ? new SimmerApi(apiKey, apiUrl, BUNDLED_VERSION) : null;

// ---------------------------------------------------------------------------
// Version check — on boot, fetch the latest published version from npm and
// log a noisy warning if the bundled version is behind. Non-blocking; if
// the registry is unreachable, the check silently no-ops. Agents see the
// stderr in their tool-call output and can relay the nudge to the user.
// ---------------------------------------------------------------------------

// Returns positive if a > b, negative if a < b, 0 if equal. Compares the
// numeric major.minor.patch prefix; ignores pre-release / build suffixes.
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
    const resp = await fetch("https://registry.npmjs.org/simmer-autoresearch/latest", {
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    if (!resp.ok) return;
    const data = (await resp.json()) as { version?: string };
    const latest = data.version;
    if (!latest) return;
    // Only warn when bundled is BEHIND latest. Local pre-release builds
    // (bundled ahead of registry) silently no-op.
    if (compareSemver(BUNDLED_VERSION, latest) >= 0) return;
    console.error(
      `[autoresearch] ⚠ Update available: simmer-autoresearch ${BUNDLED_VERSION} → ${latest}. ` +
      `Restart your agent session to pick up the latest. ` +
      `If you pinned a version in your MCP config, update it and restart.`,
    );
  } catch {
    // Registry unreachable, offline, etc. — non-fatal. Skip silently.
  }
}

// Fire-and-forget; don't block server boot on the network call.
checkLatestVersion();

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let state: ExperimentState = reconstructState(workspaceDir);
if (state.results.length > 0) {
  console.error(
    `[autoresearch] Restored ${state.results.length} experiments from JSONL (segment ${state.currentSegment})`,
  );
}

// ---------------------------------------------------------------------------
// MCP Server
// ---------------------------------------------------------------------------

const server = new McpServer({
  name: "simmer-autoresearch",
  version: BUNDLED_VERSION,
});

// --- init_experiment ---

server.tool(
  "init_experiment",
  "Initialize the experiment session. Call once before the first run_experiment to set the name, primary metric, unit, and direction. Writes config to autoresearch.jsonl.",
  {
    name: z.string().describe('Human-readable name (e.g. "Optimizing polymarket-ai-divergence for P&L")'),
    metric_name: z.string().describe('Primary metric name (e.g. "pnl", "trades", "sharpe")'),
    skill_slug: z.string().describe('Skill slug for API tracking (e.g. "polymarket-fast-loop")'),
    metric_unit: z.string().optional().describe('Unit (e.g. "$", "%", "")'),
    direction: z.enum(["lower", "higher"]).optional().describe('Whether "lower" or "higher" is better. Default: "higher"'),
  },
  async ({ name, metric_name, skill_slug, metric_unit, direction }) => {
    const isReinit = state.results.length > 0;

    state.name = name;
    state.skillSlug = skill_slug;
    state.metricName = metric_name;
    state.consecutiveCrashes = 0;
    state.paused = false;
    state.metricUnit = metric_unit ?? "$";
    if (direction) state.bestDirection = direction;

    // If no local history, try API resume
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
);

// --- run_experiment ---

server.tool(
  "run_experiment",
  "Run a shell command as an experiment. Times execution, captures output, detects pass/fail.",
  {
    command: z.string().describe("Shell command to run"),
    timeout_seconds: z.number().optional().describe("Kill after this many seconds (default: 600)"),
  },
  async ({ command, timeout_seconds }) => {
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
);

// --- log_experiment ---

server.tool(
  "log_experiment",
  'Record an experiment result. "keep" auto-commits via git. "discard"/"crash"/"checks_failed" reverts. Reports confidence score after 3+ runs.',
  {
    commit: z.string().describe("Git commit hash (short, 7 chars)"),
    metric: z.number().describe("Primary metric value. 0 for crashes."),
    status: z.enum(["keep", "discard", "crash", "checks_failed"]).describe("keep if improved, discard if worse, crash if skill broke, checks_failed if ran but post-run checks failed"),
    description: z.string().describe("Short description of what this experiment tried"),
    metrics: z.record(z.string(), z.number()).optional().describe('Secondary metrics as { name: value }'),
    asi: z.record(z.string(), z.unknown()).optional().describe('Actionable Side Information — free-form diagnostics (e.g., {"market_liquidity": "low", "api_latency_ms": 340})'),
    force: z.boolean().optional().describe("Set true to allow adding a new secondary metric not previously tracked"),
  },
  async ({ commit, metric, status, description, metrics: secondaryMetrics, asi, force }) => {
    const secMetrics: Record<string, number> = secondaryMetrics ?? {};
    const forceAdd = force ?? false;

    // Validate secondary metrics consistency
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

    // Crash safety (crash and checks_failed both count)
    if (status === "crash" || status === "checks_failed") {
      state.consecutiveCrashes++;
      const curCount = currentResults(state.results, state.currentSegment).length;
      if (curCount === 1) state.paused = true;
      if (state.consecutiveCrashes >= 3) state.paused = true;
    } else {
      state.consecutiveCrashes = 0;
    }

    // Register new secondary metrics
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

    // Confidence display
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

    // Git operations
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

    // Persist to JSONL after git (so commit hash is correct)
    try {
      appendJsonl(workspaceDir, { run: state.results.length, ...experiment });
    } catch {
      // Don't fail if write fails
    }

    // POST to Simmer API (best-effort)
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

      // Verify metric against API (non-blocking)
      if (status === "keep" && state.metricName?.toLowerCase().includes("pnl")) {
        simmer.getOutcomes(state.skillSlug ?? "unknown", "").then((apiOutcome) => {
          if (apiOutcome && apiOutcome.trades > 0 && metric !== null) {
            const diff = Math.abs(apiOutcome.pnl - metric);
            if (diff > 1.0) {
              console.error(
                `[autoresearch] Metric discrepancy: agent reported ${metric}, API shows ${apiOutcome.pnl} (diff: ${diff.toFixed(2)})`,
              );
            }
          }
        }).catch(() => { /* Silent */ });
      }
    }

    // Budget warning
    if (maxExperiments > 0) {
      const threshold = Math.floor(maxExperiments * 0.8);
      if (state.results.length === threshold) {
        text += `\n\n⏳ ${maxExperiments - state.results.length} experiments remaining (limit: ${maxExperiments}).`;
      }
    }

    // Pause messages
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
);

// --- backtest_experiment ---

server.tool(
  "backtest_experiment",
  "Replay historical trades against new config params without executing real trades. Returns simulated P&L.",
  {
    skill_slug: z.string().describe("Skill to backtest"),
    config: z.record(z.string(), z.number()).describe("Config overrides to test"),
    days: z.number().optional().describe("Days of history to replay (default 7, max 30)"),
    venue: z.string().optional().describe("'sim' or 'polymarket' (default 'sim')"),
  },
  async ({ skill_slug, config, days, venue }) => {
    if (!simmer) {
      return {
        content: [{ type: "text" as const, text: "⚠️ No API key configured. Set SIMMER_API_KEY env var." }],
        isError: true,
      };
    }

    const result = await simmer.backtest({ skill_slug, config, days, venue });
    if (!result) {
      return {
        content: [{ type: "text" as const, text: "❌ Backtest failed — API unreachable or no trades found." }],
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
);

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("[autoresearch] MCP server started (stdio)");
}

main().catch((err) => {
  console.error("[autoresearch] Fatal:", err);
  process.exit(1);
});
