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
 * Handlers receive ctx.live and must NOT re-read process.env — ctx is the single
 * source of truth for the live-trading decision.
 */

import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

export type ToolContext = { live: boolean };

export type ToolResult = {
  content: Array<{ type: "text"; text: string }>;
  isError?: boolean;
};

export type ToolDef<A> = {
  name: string;
  description: string | string[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  schema: Record<string, any>;
  /** Required — TS errors if omitted. True = tool can place live orders / delete state. */
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
    return tool.handler(args, { live: tool.mutates && allowLive });
  });
}
