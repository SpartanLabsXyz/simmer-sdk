/**
 * Raw trade primitive handlers — direct REST calls, no Python subprocess.
 *
 * Safety gating is handled upstream by registerTool in tool-registry.ts.
 * Handlers receive ctx.live (true iff SIMMER_MCP_ALLOW_LIVE=true) and must NOT
 * re-read process.env — ctx is the single source of truth.
 *
 * executeTrade: ctx.live=true when the middleware allowed the call; args.dry_run
 *   still controls paper vs. live within that envelope.
 * executeCancelOrder: ctx.live=true when the middleware allowed the call;
 *   cancellation is always a live action so this is only reachable when live=true.
 */

import type { SimmerApi, TradeParams } from "./api.js";
import { BackendError } from "./errors.js";
import type { ToolContext, ToolResult } from "./tool-registry.js";

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
  _ctx: ToolContext,
): Promise<ToolResult> {
  const params: TradeParams = {
    market_id: args.market_id,
    side: args.side,
    venue: args.venue,
    dry_run: args.dry_run,
  };
  if (args.amount !== undefined) params.amount = args.amount;
  if (args.shares !== undefined) params.shares = args.shares;
  if (args.reasoning) params.reasoning = args.reasoning;
  if (args.source) params.source = args.source;

  try {
    const result = await api.trade(params);
    const label = args.dry_run ? "Dry-run result" : "Trade result";
    return { content: [{ type: "text" as const, text: `${label}:\n${JSON.stringify(result, null, 2)}` }] };
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
