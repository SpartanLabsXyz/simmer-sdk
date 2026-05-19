/**
 * Arg → env-var translation with FAIL-CLOSED dry_run gate.
 * Codex review 2026-05-19 CRITICAL #2 + #3.
 *
 * Triple-gate for live trading:
 *   1. args.dry_run === false  (explicit)
 *   2. typeof args.trading_venue === "string" && args.trading_venue !== "sim"
 *   3. processEnv.SIMMER_MCP_ALLOW_LIVE === "true"
 * If any missing, TRADING_VENUE is set to "sim" and the coercion is logged.
 */

import type { Skill } from "./core/types.js";

export function envToArgName(envName: string): string {
  const parts = envName.split("_");
  if (parts.length <= 2) return envName.toLowerCase();
  return parts.slice(2).join("_").toLowerCase();
}

export interface BuildEnvOptions {
  processEnv?: Record<string, string | undefined>;
  logCoercion?: (msg: string) => void;
}

export function buildEnv(
  skill: Skill,
  args: Record<string, unknown>,
  options: BuildEnvOptions = {}
): Record<string, string> {
  const processEnv = options.processEnv ?? process.env;
  const log = options.logCoercion ?? ((msg: string) => console.error(msg));

  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(processEnv)) {
    if (typeof v === "string") env[k] = v;
  }

  env.SIMMER_MANAGED_MODE = "1";
  env.AUTOMATON_MANAGED = "1";
  env.PYTHONUNBUFFERED = "1";

  // FAIL-CLOSED VENUE GATE
  const allowLive = processEnv.SIMMER_MCP_ALLOW_LIVE === "true";
  const callerVenue = typeof args.trading_venue === "string" ? args.trading_venue : null;
  const callerWantsLive = args.dry_run === false && callerVenue !== null && callerVenue !== "sim";

  if (callerWantsLive && allowLive) {
    env.TRADING_VENUE = callerVenue;
  } else {
    env.TRADING_VENUE = "sim";
    if (callerWantsLive && !allowLive) {
      log(
        "[simmer-mcp] COERCED to sim: caller passed dry_run=false but " +
        "SIMMER_MCP_ALLOW_LIVE is not 'true'. Set the env var to enable live."
      );
    }
  }

  // Defense-in-depth: gate-controlled vars must not be overwritten by
  // skill tunables even if skill-discovery failed to strip them.
  const GATE_ENV = new Set([
    "TRADING_VENUE", "SIMMER_MANAGED_MODE", "AUTOMATON_MANAGED",
    "PYTHONUNBUFFERED", "SIMMER_MCP_ALLOW_LIVE", "SIMMER_MCP_ALLOW_EXTRA_ARGS",
  ]);

  // Tunable args → env vars
  for (const tunable of skill.tunables) {
    if (GATE_ENV.has(tunable.env)) continue;
    const argName = envToArgName(tunable.env);
    if (argName in args) {
      env[tunable.env] = String(args[argName]);
    }
  }

  return env;
}
