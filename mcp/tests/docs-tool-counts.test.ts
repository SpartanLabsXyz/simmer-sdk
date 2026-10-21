/** Guard documented tool counts against the server's real tools/list surface. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const server = new URL("../dist/mcp-server.js", import.meta.url);
// tools/list is local; suppress the server's unrelated npm update check as well.
const offline = "data:text/javascript,globalThis.fetch = async () => { throw new Error('Network disabled in docs tool-count test'); };";

// The protocol test's mcpCall helper is private. Use the same stdio handshake.
async function toolNames(apiKey: string): Promise<string[]> {
  return new Promise((resolve, reject) => {
    const child = spawn("node", ["--import", offline, fileURLToPath(server)], {
      env: { ...process.env, SIMMER_API_KEY: apiKey },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error("MCP tools/list timeout"));
    }, 15_000);
    child.stdout.on("data", (data: Buffer) => { stdout += data.toString(); });
    child.stderr.on("data", (data: Buffer) => { stderr += data.toString(); });
    child.on("error", (error) => { clearTimeout(timer); reject(error); });
    child.on("close", () => {
      clearTimeout(timer);
      try {
        const response = stdout.trim().split("\n").filter(Boolean).map((line) => JSON.parse(line))
          .find((message) => message.id === 2);
        assert.ok(response && !response.error, `tools/list failed: ${stdout.slice(0, 2000)} ${stderr.slice(0, 2000)}`);
        assert.ok(Array.isArray(response.result?.tools), "tools/list must return tools");
        resolve(response.result.tools.map((tool: { name: string }) => tool.name));
      } catch (error) { reject(error); }
    });
    for (const message of [
      { jsonrpc: "2.0", id: 1, method: "initialize", params: {
        protocolVersion: "2024-11-05", capabilities: {},
        clientInfo: { name: "docs-tool-count-test", version: "0.0.1" },
      } },
      { jsonrpc: "2.0", method: "notifications/initialized", params: {} },
      { jsonrpc: "2.0", id: 2, method: "tools/list", params: {} },
    ]) child.stdin.write(JSON.stringify(message) + "\n");
    child.stdin.end();
  });
}

const counts = Promise.all([toolNames(""), toolNames("sk_test_key")]);

function document(file: string): string[] {
  return readFileSync(new URL(`../${file}`, import.meta.url), "utf8").split("\n");
}

function checkCount(lines: string[], file: string, pattern: RegExp, expected: number): void {
  const index = lines.findIndex((line) => pattern.test(line));
  assert.ok(index >= 0, `Edit ${file}:1: missing documented count matching ${pattern}`);
  const match = lines[index].match(pattern)!;
  assert.equal(Number(match[1]), expected, `Edit ${file}:${index + 1}: expected ${expected} tools`);
}

test("README tier counts match tools/list", async () => {
  const [keyless, keyed] = await counts;
  const lines = document("README.md");
  checkCount(lines, "mcp/README.md", /^### Discovery \(no API key — (\d+) tools\)$/, keyless.length);
  checkCount(lines, "mcp/README.md", /^### Pro \(requires SIMMER_API_KEY — (\d+) tools\)$/, keyed.length - keyless.length);
});

test("README discovery table lists every keyless tool", async () => {
  const [keyless] = await counts;
  const lines = document("README.md");
  const start = lines.findIndex((line) => line.startsWith("### Discovery ("));
  assert.ok(start >= 0, "Edit mcp/README.md:1: missing Discovery heading");
  const end = lines.findIndex((line, index) => index > start && line.startsWith("### "));
  const names = lines.slice(start + 1, end < 0 ? undefined : end)
    .flatMap((line) => line.match(/^\| `([^`]+)` \|/)?.[1] ?? []);
  for (const name of keyless) {
    assert.ok(names.includes(name), `Edit mcp/README.md:${start + 1}: add ${name} to the discovery table`);
  }
});

test("CONTRIBUTING smoke counts match tools/list", async () => {
  const [keyless, keyed] = await counts;
  const lines = document("CONTRIBUTING.md");
  checkCount(lines, "mcp/CONTRIBUTING.md", /exactly (\d+) tools/, keyless.length);
  checkCount(lines, "mcp/CONTRIBUTING.md", /(\d+) tools \(with API key\)/, keyed.length);
  checkCount(lines, "mcp/CONTRIBUTING.md", /tools array with (\d+) entries —/, keyless.length);
  checkCount(lines, "mcp/CONTRIBUTING.md", /tools array with (\d+) entries \(/, keyed.length);
  checkCount(lines, "mcp/CONTRIBUTING.md", /entries \((\d+) keyless/, keyless.length);
  checkCount(lines, "mcp/CONTRIBUTING.md", /tools: (\d+) \(discovery only,/, keyless.length);
  checkCount(lines, "mcp/CONTRIBUTING.md", /discovery only, (\d+) keyless/, keyless.length);
});
