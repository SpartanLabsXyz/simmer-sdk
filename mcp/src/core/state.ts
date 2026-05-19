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
