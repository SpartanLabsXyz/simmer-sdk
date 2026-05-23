/**
 * Raw trade primitive tools — direct REST calls, no Python subprocess.
 * Safety gate mirrors the per-skill triple gate in env-translation.ts:
 *   1. dry_run === false  (explicit opt-out)
 *   2. venue is a live venue (not "sim")
 *   3. SIMMER_MCP_ALLOW_LIVE === "true"
 * If any gate fails, the trade is coerced to sim with a warning in the result.
 */

import type { SimmerApi, TradeParams } from "./api.js";
import { BackendError } from "./errors.js";

export interface TradePrimitiveResult {
  [key: string]: unknown;
  content: Array<{ type: "text"; text: string }>;
  isError?: boolean;
}

// Allowlist of live venues. Anything outside this list (including "", undefined,
// malformed strings like "polymarketz") is treated as paper/sim — defense-in-depth
// against bypass of the MCP Zod schema by programmatic callers.
const LIVE_VENUES = ["polymarket", "kalshi"] as const;

function resolveVenue(
  dry_run: boolean,
  venue: string,
  processEnv: Record<string, string | undefined> = process.env as Record<string, string | undefined>,
): { resolvedVenue: string; coercionWarning?: string } {
  const allowLive = processEnv.SIMMER_MCP_ALLOW_LIVE === "true";
  // Strict === false: only literal `false` opts into live. Undefined/null/0/""
  // all default to paper. Paired with the venue allowlist below.
  const isLiveVenue = (LIVE_VENUES as readonly string[]).includes(venue);
  const wantsLive = dry_run === false && isLiveVenue;

  if (wantsLive && allowLive) {
    return { resolvedVenue: venue };
  }
  if (wantsLive && !allowLive) {
    return {
      resolvedVenue: "sim",
      coercionWarning:
        `⚠️ Trade coerced to dry_run=true on sim venue. ` +
        `To enable live trading on ${venue}, set SIMMER_MCP_ALLOW_LIVE=true in your MCP env.`,
    };
  }
  // Paper path: pass venue through only if it's sim or a known live venue (for
  // paper-trading on real pricing). Unknown/malformed venues coerce to sim
  // with a warning so the caller knows what happened. This also ensures
  // effectiveDryRun gets forced to true downstream (coercionWarning truthy).
  if (venue === "sim" || isLiveVenue) {
    return { resolvedVenue: venue };
  }
  return {
    resolvedVenue: "sim",
    coercionWarning:
      `⚠️ Unknown venue "${venue}" coerced to sim. ` +
      `Allowed venues: sim, polymarket, kalshi.`,
  };
}

export async function executeTrade(
  api: SimmerApi,
  args: {
    market_id: string;
    side: "yes" | "no";
    action: "buy" | "sell";
    amount?: number;
    shares?: number;
    venue: string;
    dry_run: boolean;
    reasoning?: string;
    source?: string;
  },
  processEnv: Record<string, string | undefined> = process.env as Record<string, string | undefined>,
): Promise<TradePrimitiveResult> {
  const { resolvedVenue, coercionWarning } = resolveVenue(args.dry_run, args.venue, processEnv);
  // Fail-closed: only literal `false` opts out of dry-run. Undefined/null/any
  // other falsy value defaults to paper mode. Paired with resolveVenue's
  // strict === false check above for defense-in-depth.
  const effectiveDryRun = args.dry_run === false && !coercionWarning ? false : true;

  const params: TradeParams = {
    market_id: args.market_id,
    side: args.side,
    action: args.action,
    venue: resolvedVenue,
    dry_run: effectiveDryRun,
  };
  if (args.amount !== undefined) params.amount = args.amount;
  if (args.shares !== undefined) params.shares = args.shares;
  if (args.reasoning) params.reasoning = args.reasoning;
  if (args.source) params.source = args.source;

  try {
    const result = await api.trade(params);
    const parts: string[] = [];
    if (coercionWarning) parts.push(coercionWarning);
    const label = effectiveDryRun ? "Dry-run result" : "Trade result";
    parts.push(`${label}:\n${JSON.stringify(result, null, 2)}`);
    return { content: [{ type: "text" as const, text: parts.join("\n\n") }] };
  } catch (e) {
    if (e instanceof BackendError) return e.toMcpResponse();
    return {
      content: [{ type: "text" as const, text: `❌ Trade failed: ${e instanceof Error ? e.message : String(e)}` }],
      isError: true,
    };
  }
}

/**
 * Cancel an open order. Always a live state-changing action (no paper mode
 * exists for cancellation), so it's gated on SIMMER_MCP_ALLOW_LIVE=true.
 * Mirrors the live-trading posture: explicit env opt-in required.
 */
export async function executeCancelOrder(
  api: SimmerApi,
  orderId: string,
  processEnv: Record<string, string | undefined> = process.env as Record<string, string | undefined>,
): Promise<TradePrimitiveResult> {
  const allowLive = processEnv.SIMMER_MCP_ALLOW_LIVE === "true";
  if (!allowLive) {
    return {
      content: [{
        type: "text" as const,
        text: `⚠️ Cancellation blocked — order cancellation is a live state-changing action. ` +
          `Set SIMMER_MCP_ALLOW_LIVE=true in your MCP env to enable.`,
      }],
      isError: true,
    };
  }

  try {
    const result = await api.cancelOrder(orderId);
    return {
      content: [{ type: "text" as const, text: `✅ Order cancelled:\n${JSON.stringify(result, null, 2)}` }],
    };
  } catch (e) {
    if (e instanceof BackendError) return e.toMcpResponse();
    return {
      content: [{ type: "text" as const, text: `❌ Cancel failed: ${e instanceof Error ? e.message : String(e)}` }],
      isError: true,
    };
  }
}
