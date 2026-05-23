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

function resolveVenue(
  dry_run: boolean,
  venue: string,
  processEnv: Record<string, string | undefined> = process.env as Record<string, string | undefined>,
): { resolvedVenue: string; coercionWarning?: string } {
  const allowLive = processEnv.SIMMER_MCP_ALLOW_LIVE === "true";
  const wantsLive = !dry_run && venue !== "sim";

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
  return { resolvedVenue: venue };
}

export async function executeTrade(
  api: SimmerApi,
  args: {
    market_id: string;
    side: "yes" | "no";
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
  const effectiveDryRun = coercionWarning ? true : args.dry_run;

  const params: TradeParams = {
    market_id: args.market_id,
    side: args.side,
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
