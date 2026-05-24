/**
 * Raw trade primitive handlers — direct REST calls, no Python subprocess.
 *
 * Safety gating for always-live operations (executeCancelOrder) is handled upstream by
 * registerTool (mutates:true) in tool-registry.ts. For executeTrade, which supports both
 * paper and live modes, gating is handled internally via ctx.allowLive — the outer gate
 * is skipped (mutates:false) so paper/dry-run trades work without SIMMER_MCP_ALLOW_LIVE.
 *
 * Handlers receive ctx from the middleware and must NOT re-read process.env.
 */

import type { SimmerApi, TradeParams } from "./api.js";
import { BackendError } from "./errors.js";
import type { ToolContext, ToolResult } from "./tool-registry.js";

// Allowlist of live venues. Anything outside this list (including "", undefined,
// malformed strings like "polymarketz") is treated as paper/sim — defense-in-depth
// against bypass of the MCP Zod schema by programmatic callers.
const LIVE_VENUES = ["polymarket", "kalshi"] as const;

function resolveVenue(
  dry_run: boolean,
  venue: string,
  allowLive: boolean,
): { resolvedVenue: string; coercionWarning?: string } {
  const isLiveVenue = (LIVE_VENUES as readonly string[]).includes(venue);
  // Strict === false: only literal `false` opts into live. Undefined/null/0/""
  // all default to paper. Paired with the venue allowlist below.
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
  // paper-trading on real pricing). Unknown/malformed venues coerce to sim with
  // a warning so the caller knows what happened.
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
  ctx: ToolContext,
): Promise<ToolResult> {
  const { resolvedVenue, coercionWarning } = resolveVenue(args.dry_run, args.venue, ctx.allowLive);
  // Fail-closed: only literal `false` opts out of dry-run. Undefined/null/any
  // other falsy value defaults to paper mode. Paired with resolveVenue's strict
  // === false check above for defense-in-depth.
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

export async function executeCancelOrder(
  api: SimmerApi,
  args: { order_id: string },
  _ctx: ToolContext,
): Promise<ToolResult> {
  // Outer gate (mutates:true) already blocked when !allowLive — no re-check needed.
  try {
    const result = await api.cancelOrder(args.order_id);
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
