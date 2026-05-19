import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { BackendError } from "../dist/errors.js";

describe("BackendError", () => {
  it("is instanceof Error", () => {
    const err = new BackendError(404, "not found");
    assert.ok(err instanceof Error);
  });

  it("exposes statusCode and body", () => {
    const err = new BackendError(401, "unauthorized");
    assert.equal(err.statusCode, 401);
    assert.equal(err.body, "unauthorized");
    assert.equal(err.message, "unauthorized");
  });

  it("has correct name for stack trace", () => {
    const err = new BackendError(500, "server error");
    assert.equal(err.name, "BackendError");
  });

  it("carries upgradeUrl when provided", () => {
    const err = new BackendError(403, "Pro required", "https://simmer.markets/pro");
    assert.equal(err.upgradeUrl, "https://simmer.markets/pro");
  });

  it("toMcpResponse includes body text and upgrade URL", () => {
    const err = new BackendError(403, "Pro required", "https://simmer.markets/pro");
    const r = err.toMcpResponse();
    assert.equal(r.isError, true);
    assert.match(r.content[0].text, /Pro required/);
    assert.match(r.content[0].text, /simmer\.markets\/pro/);
  });

  it("toMcpResponse without upgradeUrl omits upgrade text", () => {
    const err = new BackendError(500, "Internal error");
    const r = err.toMcpResponse();
    assert.equal(r.isError, true);
    assert.match(r.content[0].text, /Internal error/);
    assert.doesNotMatch(r.content[0].text, /pro/i);
  });
});
