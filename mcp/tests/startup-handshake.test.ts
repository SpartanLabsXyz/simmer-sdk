// The initialize handshake must not wait on the runtime probe.
//
// Every host applies a startup timeout to initialize (Codex: 10s by default,
// and it drops the server silently on a miss). The probe spawns python and
// git; a slow or wedged interpreter must delay only the diagnostic banner,
// never the handshake. This test points SIMMER_MCP_PYTHON at a stub that
// sleeps well past the assertion window and times the initialize reply.
// It fails on the pre-#333 ordering (probe awaited before connect), where
// the reply arrived after the stub finished.
import { test } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, chmodSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const SERVER = fileURLToPath(new URL("../dist/mcp-server.js", import.meta.url));
const STUB_SLEEP_S = 6;
const HANDSHAKE_BUDGET_MS = 3000;

test("initialize is answered before a slow runtime probe finishes", async () => {
  const dir = mkdtempSync(join(tmpdir(), "simmer-mcp-slowpy-"));
  const stub = join(dir, "python");
  writeFileSync(stub, `#!/bin/sh\nsleep ${STUB_SLEEP_S}\necho "Python 3.11.0"\n`);
  chmodSync(stub, 0o755);

  const t0 = Date.now();
  const child = spawn(process.execPath, [SERVER], {
    env: { ...process.env, SIMMER_API_KEY: "", SIMMER_MCP_PYTHON: stub },
    stdio: ["pipe", "pipe", "ignore"],
  });

  const answeredAfterMs = await new Promise<number>((resolve, reject) => {
    let buf = "";
    const timer = setTimeout(() => reject(new Error("no initialize response within 20s")), 20000);
    child.stdout.on("data", (d) => {
      buf += String(d);
      for (const line of buf.split("\n")) {
        if (!line.trim()) continue;
        try {
          const msg = JSON.parse(line);
          if (msg.id === 1) {
            clearTimeout(timer);
            resolve(Date.now() - t0);
            return;
          }
        } catch {
          /* partial line */
        }
      }
    });
    child.on("error", reject);
    child.stdin.write(
      JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
        params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "t", version: "0" } },
      }) + "\n"
    );
  }).finally(() => child.kill());

  assert.ok(
    answeredAfterMs < HANDSHAKE_BUDGET_MS,
    `initialize answered after ${answeredAfterMs}ms; the ${STUB_SLEEP_S}s probe stub must not be on the handshake path`
  );
});
