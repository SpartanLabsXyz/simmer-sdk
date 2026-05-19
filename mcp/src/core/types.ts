// Synced with pi-autoresearch @ 5a29db08 (2026-04-14)

/**
 * Actionable Side Information — free-form diagnostics per experiment run.
 * The agent decides what to record. Any key/value pair is valid.
 */
export interface ASI {
  [key: string]: unknown;
}

export interface ExperimentResult {
  commit: string;
  metric: number;
  metrics: Record<string, number>;
  status: "keep" | "discard" | "crash" | "checks_failed";
  description: string;
  timestamp: number;
  segment: number;
  confidence: number | null;
  /** Actionable Side Information — structured diagnostics for this run */
  asi?: ASI;
}

export interface MetricDef {
  name: string;
  unit: string;
}

export interface ExperimentState {
  results: ExperimentResult[];
  bestMetric: number | null;
  bestDirection: "lower" | "higher";
  metricName: string;
  metricUnit: string;
  secondaryMetrics: MetricDef[];
  name: string | null;
  skillSlug: string | null;
  currentSegment: number;
  consecutiveCrashes: number;
  paused: boolean;
  confidence: number | null;
}

export interface RunResult {
  exitCode: number | null;
  stdout: string;
  stderr: string;
  durationSeconds: number;
  passed: boolean;
  timedOut: boolean;
  killed: boolean;
}

// Skill registry types — added 2026-05-19 for unified MCP (SIM-2045)

export type SkillTier = "trading" | "instruction";

export interface TunableNumber {
  env: string;
  type: "number";
  default: number;
  range?: [number, number];
  step?: number;
  label: string;
}

export interface TunableString {
  env: string;
  type: "string";
  default: string;
  enum?: string[];
  label: string;
}

export interface TunableBoolean {
  env: string;
  type: "boolean";
  default: boolean;
  label: string;
}

export type Tunable = TunableNumber | TunableString | TunableBoolean;

export interface Skill {
  slug: string;
  toolName: string;
  name: string;
  description: string;
  version: string;
  tier: SkillTier;
  status?: string;
  published?: boolean;
  entrypoint?: string;
  tunables: Tunable[];
  skillDir: string;
  hasDisclaimer: boolean;
}
