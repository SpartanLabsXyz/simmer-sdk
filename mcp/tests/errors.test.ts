import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { BackendError } from "../dist/errors.js";

describe("BackendError", () => {
  it("is instanceof Error", () => {
    const err = new BackendError("not found", 404, "NOT_FOUND");
    assert.ok(err instanceof Error);
  });

  it("exposes statusCode and code", () => {
    const err = new BackendError("unauthorized", 401, "UNAUTHORIZED");
    assert.equal(err.statusCode, 401);
    assert.equal(err.code, "UNAUTHORIZED");
    assert.equal(err.message, "unauthorized");
  });

  it("has correct name for stack trace", () => {
    const err = new BackendError("server error", 500, "SERVER_ERROR");
    assert.equal(err.name, "BackendError");
  });
});
