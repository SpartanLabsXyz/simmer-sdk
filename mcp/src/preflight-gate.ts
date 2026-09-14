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

export function isSkipPreflight(value: unknown): boolean {
  if (value === true) return true;
  if (typeof value !== "string") return false;
  return ["1", "true", "yes"].includes(value.trim().toLowerCase());
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

/** Same default and fallback as SimmerClient._preflight_exposure_cap_usd. */
export function parseExposureCapUsd(raw: unknown): number {
  if (typeof raw === "number" && Number.isFinite(raw)) return raw;
  if (typeof raw !== "string") return 100;
  const trimmed = raw.trim();
  if (!trimmed) return 100;
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : 100;
}

/**
 * Parse a position current_value. Missing/empty matches the SDK (`or 0`).
 * Non-finite values must not become 0 (that understates exposure).
 */
function parseExposureValue(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return 0;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
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
  const exposureCapUsd = opts.exposureCapUsd ?? 100;
  const blockers: string[] = [];

  if (!SUPPORTED_VENUES.has(venue)) {
    blockers.push("VENUE_UNSUPPORTED");
    return { ok_to_trade: false, blockers };
  }

  let realTradingEnabled = false;
  let executionWallet: string | undefined;

  try {
    const me = await api.getAgentMe();
    realTradingEnabled = Boolean(me.real_trading_enabled);
    const perAgent = typeof me.per_agent_wallet_address === "string"
      ? me.per_agent_wallet_address
      : "";
    const wallet = typeof me.wallet_address === "string" ? me.wallet_address : "";
    executionWallet = perAgent || wallet || undefined;
  } catch {
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
  try {
    const briefing = await api.getBriefing();
    const raw = (briefing as { risk_alerts?: unknown }).risk_alerts;
    if (Array.isArray(raw)) pendingAlerts = raw as Array<Record<string, unknown> | string>;
  } catch {
    // SDK treats briefing failures as warnings, not blockers.
  }

  let openExposure = 0;
  let positionsOk = false;
  try {
    const posData = await api.getPositions();
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
      const pVenue = p.venue;
      if (pVenue === undefined || pVenue === "sim") {
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
  } catch {
    if (REAL_VENUES.has(venue) && exposureCapUsd > 0) {
      blockers.push("EXPOSURE_UNKNOWN");
    }
  }

  if (exposureCapUsd > 0 && positionsOk && openExposure + opts.plannedAmount > exposureCapUsd) {
    blockers.push("EXPOSURE_CAP_EXCEEDED");
  }

  for (const alert of pendingAlerts) {
    const rec = asRecord(alert);
    const msg = String(rec.message ?? alert).toLowerCase();
    if (msg.includes("gas") || (msg.includes("pol") && !msg.includes("polymarket"))) {
      if (!blockers.includes("INSUFFICIENT_GAS")) blockers.push("INSUFFICIENT_GAS");
      break;
    }
  }

  return { ok_to_trade: blockers.length === 0, blockers };
}
