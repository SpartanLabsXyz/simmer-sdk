/**
 * Live preflight gate for MCP simmer_trade.
 *
 * Mirrors SimmerClient.preflight() blocker codes so the public MCP cannot
 * place a real-money order the Python SDK would refuse. Keep this in sync
 * with simmer_sdk.client.SimmerClient.preflight — do not invent new policy.
 */

import type { SimmerApi } from "./api.js";

export type PreflightVerdict = {
  ok_to_trade: boolean;
  blockers: string[];
};

const SUPPORTED_VENUES = new Set(["sim", "polymarket", "kalshi"]);
const REAL_VENUES = new Set(["polymarket", "kalshi"]);

/** Wall-clock budget for the three parallel preflight reads. */
export const PREFLIGHT_BUDGET_MS = 8_000;

export function isSkipPreflight(value: unknown): boolean {
  if (value === true) return true;
  if (typeof value !== "string") return false;
  return ["1", "true", "yes"].includes(value.trim().toLowerCase());
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

/**
 * Parse EXPOSURE_CAP_USD for the live auto-gate.
 *
 * Unset / empty → 0 (cap disabled; wallet / venue / gas blockers still run).
 * Finite number → that cap.
 * Present but non-finite (NaN / Infinity / 1e309) → NaN so the live gate
 * can reject instead of silently defaulting to $100 or disabling.
 */
export function parseExposureCapUsd(raw: unknown): number {
  if (raw === undefined || raw === null) return 0;
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : Number.NaN;
  if (typeof raw !== "string") return 0;
  const trimmed = raw.trim();
  if (!trimmed) return 0;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : Number.NaN;
}

/**
 * Parse a position current_value. Missing/empty matches the SDK (`or 0`).
 * Non-finite values must not become 0 (that understates exposure).
 */
export function parseExposureValue(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return 0;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

/** venue null / undefined / "sim" is virtual $SIM — same as Python. */
export function isSimLikePositionVenue(venue: unknown): boolean {
  return venue === undefined || venue === null || venue === "sim";
}

/**
 * Structured INSUFFICIENT_GAS only. Do not scan free-text for "gas" / "pol"
 * — those substrings false-positive on "policy", "political", "vegas".
 */
export function alertSignalsInsufficientGas(alert: unknown): boolean {
  if (typeof alert === "string") {
    return alert.trim().toUpperCase() === "INSUFFICIENT_GAS";
  }
  const rec = asRecord(alert);
  for (const key of ["code", "type", "alert_code", "blocker"] as const) {
    const val = rec[key];
    if (typeof val === "string" && val.trim().toUpperCase() === "INSUFFICIENT_GAS") {
      return true;
    }
  }
  return false;
}

/**
 * Compose the same three read endpoints the SDK uses and return the
 * ok_to_trade verdict. Fail-closed on identity / exposure fetch failures
 * for real venues (WALLET_UNVERIFIED / EXPOSURE_UNKNOWN).
 */
export async function evaluateSdkPreflight(
  api: SimmerApi,
  opts: { venue: string; plannedAmount: number; exposureCapUsd?: number },
): Promise<PreflightVerdict> {
  const venue = opts.venue === "simmer" || opts.venue === "sandbox" ? "sim" : opts.venue;
  const exposureCapUsd = opts.exposureCapUsd ?? 0;
  const blockers: string[] = [];

  if (opts.exposureCapUsd !== undefined && !Number.isFinite(opts.exposureCapUsd)) {
    throw new Error("EXPOSURE_CAP_USD must be a finite number");
  }

  if (!SUPPORTED_VENUES.has(venue)) {
    blockers.push("VENUE_UNSUPPORTED");
    return { ok_to_trade: false, blockers };
  }

  const [meOutcome, briefingOutcome, posOutcome] = await Promise.allSettled([
    api.getAgentMe(PREFLIGHT_BUDGET_MS),
    api.getBriefing(undefined, PREFLIGHT_BUDGET_MS),
    api.getPositions({}, PREFLIGHT_BUDGET_MS),
  ]);

  let realTradingEnabled = false;
  let executionWallet: string | undefined;

  if (meOutcome.status === "fulfilled") {
    const me = meOutcome.value;
    realTradingEnabled = Boolean(me.real_trading_enabled);
    const perAgent = typeof me.per_agent_wallet_address === "string"
      ? me.per_agent_wallet_address
      : "";
    const wallet = typeof me.wallet_address === "string" ? me.wallet_address : "";
    executionWallet = perAgent || wallet || undefined;
  } else {
    realTradingEnabled = false;
  }

  if (REAL_VENUES.has(venue)) {
    if (!realTradingEnabled) {
      blockers.push("WALLET_UNVERIFIED");
    } else if (venue === "polymarket" && !executionWallet) {
      blockers.push("WALLET_UNVERIFIED");
    }
  }

  let pendingAlerts: Array<Record<string, unknown> | string> = [];
  if (briefingOutcome.status === "fulfilled") {
    const raw = (briefingOutcome.value as { risk_alerts?: unknown }).risk_alerts;
    if (Array.isArray(raw)) pendingAlerts = raw as Array<Record<string, unknown> | string>;
  }

  let openExposure = 0;
  let positionsOk = false;
  if (posOutcome.status === "fulfilled") {
    const posData = posOutcome.value;
    const positions = Array.isArray((posData as { positions?: unknown }).positions)
      ? (posData as { positions: Array<Record<string, unknown>> }).positions
      : [];
    let realExp = 0;
    let simExp = 0;
    let exposureUnknown = false;
    for (const p of positions) {
      const parsed = parseExposureValue(p.current_value);
      if (parsed === null) {
        exposureUnknown = true;
        break;
      }
      if (isSimLikePositionVenue(p.venue)) {
        simExp += parsed;
      } else {
        realExp += parsed;
      }
    }
    if (exposureUnknown) {
      if (REAL_VENUES.has(venue) && exposureCapUsd > 0) {
        blockers.push("EXPOSURE_UNKNOWN");
      }
    } else {
      openExposure = venue === "sim" ? simExp : realExp;
      positionsOk = true;
    }
  } else if (REAL_VENUES.has(venue) && exposureCapUsd > 0) {
    blockers.push("EXPOSURE_UNKNOWN");
  }

  if (exposureCapUsd > 0 && positionsOk && openExposure + opts.plannedAmount > exposureCapUsd) {
    blockers.push("EXPOSURE_CAP_EXCEEDED");
  }

  for (const alert of pendingAlerts) {
    if (alertSignalsInsufficientGas(alert)) {
      if (!blockers.includes("INSUFFICIENT_GAS")) blockers.push("INSUFFICIENT_GAS");
      break;
    }
  }

  return { ok_to_trade: blockers.length === 0, blockers };
}
