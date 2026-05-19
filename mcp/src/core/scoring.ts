// Synced with pi-autoresearch @ 5a29db08 (2026-04-14)

import type { ExperimentResult } from "./types.js";

export function formatNum(value: number | null, unit: string): string {
  if (value === null) return "—";
  const u = unit || "";
  if (value === Math.round(value)) return String(value) + u;
  return value.toFixed(2) + u;
}

export function isBetter(
  current: number,
  best: number,
  direction: "lower" | "higher",
): boolean {
  return direction === "lower" ? current < best : current > best;
}

export function currentResults(
  results: ExperimentResult[],
  segment: number,
): ExperimentResult[] {
  return results.filter((r) => r.segment === segment);
}

export function findBaselineMetric(
  results: ExperimentResult[],
  segment: number,
): number | null {
  const cur = currentResults(results, segment);
  return cur.length > 0 ? cur[0].metric : null;
}

function sortedMedian(values: number[]): number {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0
    ? (sorted[mid - 1] + sorted[mid]) / 2
    : sorted[mid];
}

export function computeConfidence(
  results: ExperimentResult[],
  segment: number,
  direction: "lower" | "higher",
): number | null {
  const cur = currentResults(results, segment).filter((r) => r.metric > 0);
  if (cur.length < 3) return null;

  const values = cur.map((r) => r.metric);
  const median = sortedMedian(values);
  const deviations = values.map((v) => Math.abs(v - median));
  const mad = sortedMedian(deviations);

  if (mad === 0) return null;

  const baseline = findBaselineMetric(results, segment);
  if (baseline === null) return null;

  let bestKept: number | null = null;
  for (const r of cur) {
    if (r.status === "keep" && r.metric > 0) {
      if (bestKept === null || isBetter(r.metric, bestKept, direction)) {
        bestKept = r.metric;
      }
    }
  }
  if (bestKept === null || bestKept === baseline) return null;

  const delta = Math.abs(bestKept - baseline);
  return delta / mad;
}
