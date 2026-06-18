/**
 * MCP JSON-RPC protocol integration tests.
 * Task 25 (tools/list count) + Task 28 (resources/list).
 * Spawns the compiled dist/mcp-server.js and exercises the wire protocol.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(__dirname, "../dist/mcp-server.js");

interface JsonRpcResponse {
  jsonrpc: "2.0";
  id: number;
  result?: Record<string, unknown>;
  error?: { code: number; message: string };
}

/**
 * Run a full MCP session against the server, sending initialize + one method.
 * Returns the response for the second request (id=2).
 */
async function mcpCall(
  method: string,
  params: unknown,
  env: Record<string, string | undefined> = {},
): Promise<JsonRpcResponse> {
  return new Promise((resolve, reject) => {
    const child = spawn("node", [SERVER], {
      env: { ...process.env, ...env },
      stdio: ["pipe", "pipe", "ignore"],
    });

    let stdout = "";
    child.stdout.on("data", (d: Buffer) => { stdout += d.toString(); });
    child.on("error", reject);
    child.on("close", () => {
      const lines = stdout.trim().split("\n");
      for (const line of lines) {
        try {
          const parsed = JSON.parse(line) as JsonRpcResponse;
          if (parsed.id === 2) { resolve(parsed); return; }
        } catch { /* skip non-JSON or incomplete lines */ }
      }
      reject(new Error(`No response for id=2 in output:\n${stdout.slice(0, 2000)}`));
    });

    // MCP handshake: initialize → initialized notification → method call
    const init: Record<string, unknown> = {
      jsonrpc: "2.0", id: 1, method: "initialize",
      params: {
        protocolVersion: "2024-11-05",
        capabilities: {},
        clientInfo: { name: "test-client", version: "0.0.1" },
      },
    };
    const notify: Record<string, unknown> = {
      jsonrpc: "2.0", method: "notifications/initialized", params: {},
    };
    const call: Record<string, unknown> = { jsonrpc: "2.0", id: 2, method, params };

    child.stdin.write(JSON.stringify(init) + "\n");
    child.stdin.write(JSON.stringify(notify) + "\n");
    child.stdin.write(JSON.stringify(call) + "\n");
    child.stdin.end();

    setTimeout(() => { child.kill(); reject(new Error(`MCP call timeout: ${method}`)); }, 15_000);
  });
}

// --- tools/list ---

test("tools/list without SIMMER_API_KEY returns exactly 3 free tools", async () => {
  const resp = await mcpCall("tools/list", {}, { SIMMER_API_KEY: "" });
  assert.ok(!resp.error, `Expected no error, got: ${JSON.stringify(resp.error)}`);
  const tools = (resp.result?.tools ?? []) as Array<{ name: string }>;
  assert.equal(tools.length, 3, `Expected 3 tools, got ${tools.length}: ${tools.map((t) => t.name).join(", ")}`);
  const names = tools.map((t) => t.name);
  assert.ok(names.includes("list_skills"), "list_skills missing");
  assert.ok(names.includes("get_skill_docs"), "get_skill_docs missing");
  assert.ok(names.includes("troubleshoot_error"), "troubleshoot_error missing");
});

test("tools/list with SIMMER_API_KEY returns free, autoresearch, raw, and core per-skill tools", async () => {
  const resp = await mcpCall("tools/list", {}, { SIMMER_API_KEY: "sk_test_key" });
  assert.ok(!resp.error, `Expected no error, got: ${JSON.stringify(resp.error)}`);
  const tools = (resp.result?.tools ?? []) as Array<{ name: string }>;
  // 3 free + 13 Pro/raw/portfolio tools + 5 core bundled skills = 21
  assert.equal(tools.length, 21, `Expected 21 tools with API key, got ${tools.length}: ${tools.map((t) => t.name).join(", ")}`);
  const names = tools.map((t) => t.name);
  // Free tools always present
  assert.ok(names.includes("list_skills"), "list_skills missing");
  assert.ok(names.includes("troubleshoot_error"), "troubleshoot_error missing");
  // Autoresearch tools
  assert.ok(names.includes("init_experiment"), "init_experiment missing");
  assert.ok(names.includes("run_experiment"), "run_experiment missing");
  assert.ok(names.includes("log_experiment"), "log_experiment missing");
  assert.ok(names.includes("backtest_experiment"), "backtest_experiment missing");
  assert.ok(names.includes("simmer_preflight"), "core preflight tool missing");
  assert.ok(!names.includes("simmer_polymarket_combo_builder"), "combo builder should resolve via ClawHub on demand");
  assert.ok(!names.includes("simmer_polymarket_soccer_shock_ladder"), "shock ladder should resolve via ClawHub on demand");
});

// --- resources/list ---

test("server does not register a resources capability (removed in v3.1.0)", async () => {
  // v3.1.0 removed the static simmer://docs/* resources — they shipped frozen
  // markdown snapshots that drifted from docs.simmer.markets. The MCP SDK only
  // registers the resources capability when at least one resource is added, so
  // resources/list correctly returns -32601 method-not-found.
  // Agents should fetch docs.simmer.markets/llms-full.txt directly for the API
  // reference; per-skill docs are still available via the get_skill_docs tool.
  const resp = await mcpCall("resources/list", {}, { SIMMER_API_KEY: "" });
  assert.ok(resp.error, "Expected resources/list to error since no resources are registered");
  assert.equal(resp.error?.code, -32601, "Expected -32601 method-not-found");
});
