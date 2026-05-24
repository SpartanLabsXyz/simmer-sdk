/**
 * Centralized tool-registration middleware.
 *
 * Every tool that can modify live state MUST be registered through registerTool
 * with `mutates: boolean` explicitly set. The TypeScript compiler fails the build
 * if a new tool omits the field — the bug class that bit simmer_cancel_order
 * (forgetting to gate) is structurally impossible after this module lands.
 *
 * Gate semantics:
 *   mutates:true + SIMMER_MCP_ALLOW_LIVE != "true"  → errorBlocked, handler never called
 *   mutates:true + SIMMER_MCP_ALLOW_LIVE == "true"  → handler called with ctx.live=true
 *   mutates:false                                    → handler always called, ctx.live=false
 *
 * Both branches always receive ctx.allowLive (the raw env flag). "Sometimes-live" tools
 * (e.g. simmer_trade which supports both paper and live modes) should be registered as
 * mutates:false and use ctx.allowLive internally to decide whether a specific call is
 * live, rather than relying on the outer gate which would block paper trades.
 *
 * Handlers receive ctx and must NOT re-read process.env — ctx is the single source
 * of truth for the live-trading decision.
 */

import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

export type ToolContext = {
  /** true iff mutates:true AND SIMMER_MCP_ALLOW_LIVE=true. Always false for mutates:false tools. */
  live: boolean;
  /** Raw SIMMER_MCP_ALLOW_LIVE env flag. Available to all handlers regardless of mutates. */
  allowLive: boolean;
};

export type ToolResult = {
  content: Array<{ type: "text"; text: string }>;
  isError?: boolean;
};

export type ToolDef<A> = {
  name: string;
  description: string | string[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  schema: Record<string, any>;
  /**
   * Required — TS errors if omitted.
   * true  = tool ALWAYS modifies live state (e.g. cancel_order); outer gate blocks when !allowLive.
   * false = tool may be paper-safe (e.g. simmer_trade dry-run); use ctx.allowLive internally.
   */
  mutates: boolean;
  handler: (args: A, ctx: ToolContext) => Promise<ToolResult>;
};

export interface RegistryEntry {
  name: string;
  mutates: boolean;
}

/** Populated at registration time. Useful for introspection and tests. */
export const _toolRegistry: RegistryEntry[] = [];

/**
 * Register a tool with the MCP server, enforcing the live-action gate.
 *
 * @param processEnv - Defaults to process.env. Inject a static object in tests.
 */
export function registerTool<A>(
  server: McpServer,
  tool: ToolDef<A>,
  processEnv: Record<string, string | undefined> = process.env as Record<string, string | undefined>,
): void {
  _toolRegistry.push({ name: tool.name, mutates: tool.mutates });
  const desc = Array.isArray(tool.description) ? tool.description.join("\n") : tool.description;

  // Cast needed because McpServer.tool() has complex generic overloads;
  // our schema type (Record<string, any>) is structurally compatible at runtime.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (server.tool as any)(tool.name, desc, tool.schema, async (args: A) => {
    const allowLive = processEnv.SIMMER_MCP_ALLOW_LIVE === "true";
    if (tool.mutates && !allowLive) {
      return {
        content: [{
          type: "text" as const,
          text:
            `${tool.name} is a live state-changing action. ` +
            `Set SIMMER_MCP_ALLOW_LIVE=true in your MCP env.`,
        }],
        isError: true,
      };
    }
    return tool.handler(args, { live: tool.mutates && allowLive, allowLive });
  });
}
