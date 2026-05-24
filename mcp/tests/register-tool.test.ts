/**
 * Tests for the registerTool middleware in tool-registry.ts.
 *
 * Covers:
 *   1. Registry accumulation: every call to registerTool records a RegistryEntry
 *      with a boolean mutates field (the compile-time requirement).
 *   2. Gate: mutates=true + no env → errorBlocked, handler never called.
 *   3. Pass-through: mutates=false → handler always called, ctx.live=false.
 *   4. Live path: mutates=true + env → handler called, ctx.live=true.
 */
import { describe, it, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { registerTool, _toolRegistry } from "../dist/tool-registry.js";
import type { ToolResult, ToolContext } from "../dist/tool-registry.js";
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";

// ---------------------------------------------------------------------------
// Minimal mock server
// ---------------------------------------------------------------------------

type CapturedHandler = (args: unknown) => Promise<ToolResult>;

function makeMockServer(): { server: McpServer; getLastHandler: () => CapturedHandler } {
  let lastHandler: CapturedHandler | null = null;
  const server = {
    tool(_name: string, _desc: string, _schema: unknown, handler: CapturedHandler) {
      lastHandler = handler;
    },
  } as unknown as McpServer;
  return {
    server,
    getLastHandler: () => {
      if (!lastHandler) throw new Error("No handler captured — was registerTool called?");
      return lastHandler;
    },
  };
}

// ---------------------------------------------------------------------------
// Helper: build a no-op handler that records calls
// ---------------------------------------------------------------------------

function recordingHandler(): {
  handler: (args: unknown, ctx: ToolContext) => Promise<ToolResult>;
  calls: Array<{ args: unknown; ctx: ToolContext }>;
} {
  const calls: Array<{ args: unknown; ctx: ToolContext }> = [];
  return {
    calls,
    handler: async (args, ctx) => {
      calls.push({ args, ctx });
      return { content: [{ type: "text" as const, text: "handler-ok" }] };
    },
  };
}

// ---------------------------------------------------------------------------
// 1. Registry accumulation
// ---------------------------------------------------------------------------

describe("_toolRegistry accumulation", () => {
  it("adds an entry for each registerTool call with correct mutates type", () => {
    const before = _toolRegistry.length;
    const { server } = makeMockServer();

    registerTool(server, {
      name: "_test_registry_read",
      description: "test",
      schema: {},
      mutates: false,
      handler: async (_args, _ctx) => ({ content: [{ type: "text" as const, text: "ok" }] }),
    }, {});

    registerTool(server, {
      name: "_test_registry_write",
      description: "test",
      schema: {},
      mutates: true,
      handler: async (_args, _ctx) => ({ content: [{ type: "text" as const, text: "ok" }] }),
    }, {});

    const added = _toolRegistry.slice(before);
    assert.equal(added.length, 2, "should have added 2 entries");
    assert.ok(
      added.every(e => typeof e.mutates === "boolean"),
      "every registry entry must have mutates as a boolean",
    );
    assert.equal(added[0].name, "_test_registry_read");
    assert.equal(added[0].mutates, false);
    assert.equal(added[1].name, "_test_registry_write");
    assert.equal(added[1].mutates, true);
  });
});

// ---------------------------------------------------------------------------
// 2. Gate: mutates=true, SIMMER_MCP_ALLOW_LIVE not set → errorBlocked
// ---------------------------------------------------------------------------

describe("registerTool gate — mutates:true without env", () => {
  it("blocks and returns isError=true when SIMMER_MCP_ALLOW_LIVE is not set", async () => {
    const { server, getLastHandler } = makeMockServer();
    const { handler, calls } = recordingHandler();

    registerTool(server, {
      name: "simmer_cancel_order",
      description: "test",
      schema: { order_id: {} },
      mutates: true,
      handler,
    }, { /* SIMMER_MCP_ALLOW_LIVE not present */ });

    const result = await getLastHandler()({ order_id: "o1" });
    assert.equal(result.isError, true, "should be isError=true");
    assert.ok(result.content[0].text.includes("live state-changing action"), "should describe it as live action");
    assert.ok(result.content[0].text.includes("SIMMER_MCP_ALLOW_LIVE"), "should name the env var");
    assert.equal(calls.length, 0, "handler must not be called when gate fires");
  });

  it("blocks when SIMMER_MCP_ALLOW_LIVE is 'false'", async () => {
    const { server, getLastHandler } = makeMockServer();
    const { handler, calls } = recordingHandler();

    registerTool(server, {
      name: "simmer_trade",
      description: "test",
      schema: {},
      mutates: true,
      handler,
    }, { SIMMER_MCP_ALLOW_LIVE: "false" });

    const result = await getLastHandler()({});
    assert.equal(result.isError, true);
    assert.equal(calls.length, 0, "handler must not be called");
  });

  it("error message names the tool", async () => {
    const { server, getLastHandler } = makeMockServer();

    registerTool(server, {
      name: "my_special_tool",
      description: "test",
      schema: {},
      mutates: true,
      handler: async (_args, _ctx) => ({ content: [] }),
    }, {});

    const result = await getLastHandler()({});
    assert.ok(result.content[0].text.includes("my_special_tool"), "error should name the tool");
  });
});

// ---------------------------------------------------------------------------
// 3. Pass-through: mutates=false → handler always called, ctx.live=false
// ---------------------------------------------------------------------------

describe("registerTool pass-through — mutates:false", () => {
  it("calls handler regardless of env, with ctx.live=false", async () => {
    const { server, getLastHandler } = makeMockServer();
    const { handler, calls } = recordingHandler();

    registerTool(server, {
      name: "simmer_get_markets",
      description: "test",
      schema: {},
      mutates: false,
      handler,
    }, { /* no env */ });

    const result = await getLastHandler()({ q: "bitcoin" });
    assert.equal(result.isError, undefined, "should not be error");
    assert.equal(calls.length, 1, "handler should be called");
    assert.equal(calls[0].ctx.live, false, "ctx.live must be false for mutates:false");
    assert.equal(calls[0].ctx.allowLive, false, "ctx.allowLive reflects env (false when unset)");
  });

  it("passes through even when SIMMER_MCP_ALLOW_LIVE=true (ctx.live stays false, allowLive=true)", async () => {
    const { server, getLastHandler } = makeMockServer();
    const { handler, calls } = recordingHandler();

    registerTool(server, {
      name: "simmer_get_briefing",
      description: "test",
      schema: {},
      mutates: false,
      handler,
    }, { SIMMER_MCP_ALLOW_LIVE: "true" });

    await getLastHandler()({});
    assert.equal(calls[0].ctx.live, false, "ctx.live is always false for mutates:false");
    assert.equal(calls[0].ctx.allowLive, true, "ctx.allowLive reflects env (true when set)");
  });
});

// ---------------------------------------------------------------------------
// 4. Live path: mutates=true + env → handler called, ctx.live=true
// ---------------------------------------------------------------------------

describe("registerTool live path — mutates:true with env", () => {
  it("calls handler with ctx.live=true and ctx.allowLive=true when SIMMER_MCP_ALLOW_LIVE=true", async () => {
    const { server, getLastHandler } = makeMockServer();
    const { handler, calls } = recordingHandler();

    registerTool(server, {
      name: "simmer_cancel_order",
      description: "test",
      schema: {},
      mutates: true,
      handler,
    }, { SIMMER_MCP_ALLOW_LIVE: "true" });

    const result = await getLastHandler()({ order_id: "o1" });
    assert.ok(!result.isError, "should not be an error");
    assert.equal(calls.length, 1, "handler should be called");
    assert.equal(calls[0].ctx.live, true, "ctx.live must be true when env gate passes");
    assert.equal(calls[0].ctx.allowLive, true, "ctx.allowLive must be true when env gate passes");
  });

  it("forwards args unchanged to the handler", async () => {
    const { server, getLastHandler } = makeMockServer();
    const { handler, calls } = recordingHandler();

    registerTool(server, {
      name: "simmer_cancel_order",
      description: "test",
      schema: {},
      mutates: true,
      handler,
    }, { SIMMER_MCP_ALLOW_LIVE: "true" });

    const testArgs = { order_id: "ord_abc123" };
    await getLastHandler()(testArgs);
    assert.deepEqual(calls[0].args, testArgs, "handler should receive the original args");
  });
});
