// Synced with pi-autoresearch @ 5a29db08 (2026-04-14)

import * as fs from "node:fs";
import * as path from "node:path";
import type { ExperimentResult, ExperimentState } from "./types.js";
import { computeConfidence, findBaselineMetric } from "./scoring.js";

export function defaultState(): ExperimentState {
  return {
    results: [],
    bestMetric: null,
    bestDirection: "higher",
    metricName: "pnl",
    metricUnit: "$",
    secondaryMetrics: [],
    name: null,
    skillSlug: null,
    currentSegment: 0,
    consecutiveCrashes: 0,
    paused: false,
    confidence: null,
  };
}

export function reconstructState(workspaceDir: string): ExperimentState {
  const state = defaultState();

  const jsonlPath = path.join(workspaceDir, "autoresearch.jsonl");
  try {
    if (fs.existsSync(jsonlPath)) {
      let segment = 0;
      const lines = fs
        .readFileSync(jsonlPath, "utf-8")
        .trim()
        .split("\n")
        .filter(Boolean);
      for (const line of lines) {
        try {
          const entry = JSON.parse(line);

          if (entry.type === "config") {
            if (entry.name) state.name = entry.name;
            if (entry.skillSlug) state.skillSlug = entry.skillSlug;
            if (entry.metricName) state.metricName = entry.metricName;
            if (entry.metricUnit !== undefined)
              state.metricUnit = entry.metricUnit;
            if (entry.bestDirection) state.bestDirection = entry.bestDirection;
            if (state.results.length > 0) segment++;
            state.currentSegment = segment;
            continue;
          }

          state.results.push({
            commit: entry.commit ?? "",
            metric: entry.metric ?? 0,
            metrics: entry.metrics ?? {},
            status: entry.status ?? "keep",
            description: entry.description ?? "",
            timestamp: entry.timestamp ?? 0,
            segment,
            confidence: entry.confidence ?? null,
            asi: entry.asi ?? undefined,
          });

          for (const name of Object.keys(entry.metrics ?? {})) {
            if (!state.secondaryMetrics.find((m) => m.name === name)) {
              let unit = "";
              if (name.includes("pnl") || name.includes("budget")) unit = "$";
              else if (name.includes("rate") || name.includes("pct"))
                unit = "%";
              state.secondaryMetrics.push({ name, unit });
            }
          }
        } catch {
          // Skip malformed lines
        }
      }
      if (state.results.length > 0) {
        state.bestMetric = findBaselineMetric(
          state.results,
          state.currentSegment,
        );
        state.confidence = computeConfidence(
          state.results,
          state.currentSegment,
          state.bestDirection,
        );
      }
    }
  } catch {
    // Fresh state
  }

  return state;
}

export function appendJsonl(workspaceDir: string, data: Record<string, unknown>): void {
  const jsonlPath = path.join(workspaceDir, "autoresearch.jsonl");
  fs.appendFileSync(jsonlPath, JSON.stringify(data) + "\n");
}

export function writeJsonl(workspaceDir: string, data: Record<string, unknown>): void {
  const jsonlPath = path.join(workspaceDir, "autoresearch.jsonl");
  fs.writeFileSync(jsonlPath, JSON.stringify(data) + "\n");
}

/**
 * Warning appended to every run_experiment result when the session has no
 * skill slug.
 *
 * `init_experiment` takes skill_slug as a required param, but if it was never
 * called (or the jsonl config line is missing) the logger falls back to the
 * placeholder "unknown". That silently breaks dashboard attribution, API
 * resume state, and server-side backtest verification of `keep` runs — a user
 * once logged 45 experiments into the "unknown" bucket with no hint that
 * anything was wrong. Returns "" when the slug is set.
 */
export function skillSlugWarning(skillSlug: string | null): string {
  if (skillSlug) return "";
  return (
    `\n\n⚠️ skill_slug is not set — this run was logged as "unknown".` +
    `\n   Attribution, resume state, and server-side verification are all broken until you fix it.` +
    `\n   Call init_experiment with skill_slug (e.g. "polymarket-weather-trader") to correct it.`
  );
}
