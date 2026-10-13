"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { webcrypto } = require("node:crypto");
const vm = require("node:vm");

const app = readFileSync(
  resolve(__dirname, "../writers_submission/static/app.js"), "utf8"
);

function section(start, end) {
  const a = app.indexOf(start);
  const b = app.indexOf(end, a);
  assert.ok(a >= 0 && b > a, "Missing Mini App section: " + start);
  return app.slice(a, b);
}

const transport = section(
  "  function requestFailure(", "  async function bootstrapSession("
);
const recovery = section(
  "  async function showAcceptedSubmission(", "  async function submitCurrent("
);
const submit = section(
  "  async function submitCurrent(", "  const MAX_PHOTO_BYTES"
);
const fileRecovery = section(
  "  async function verifyAmbiguousFileUpload(", "  async function uploadQueuedFiles("
);

test("API errors name causes without leaking raw server internals", () => {
  const ctx = vm.createContext({});
  vm.runInContext(transport + ";this.failure = requestFailure;", ctx);
  assert.match(ctx.failure(401, "unauthorized").message, /Сессия Telegram истекла/);
  assert.match(ctx.failure(429, "rate_limited").message, /Слишком много запросов/);
  assert.match(ctx.failure(413, "payload_too_large").message, /20 МБ/);
  assert.match(ctx.failure(503, "telegram_file_staging_unavailable", "Telegram недоступен").message, /Telegram недоступен/);
  const internal = ctx.failure(500, "internal_error", "secret traceback", "abcd1234");
  assert.match(internal.message, /abcd1234/);
  assert.doesNotMatch(internal.message, /secret traceback/);
});

test("API identifies disconnected network, stale versions and missing JSON", async () => {
  let conflict = false;
  const ctx = vm.createContext({
    Headers,
    AbortController,
    window: { setTimeout, clearTimeout },
    setReloadRequired: (value) => { conflict = value; },
    fetch: async () => { throw new TypeError("Failed to fetch"); },
  });
  vm.runInContext(transport + ";this.request = api;", ctx);
  await assert.rejects(ctx.request("/test"), (err) => err.code === "network_error");
  ctx.fetch = async () => ({
    status: 409,
    ok: false,
    json: async () => ({ error: "conflict" }),
  });
  await assert.rejects(ctx.request("/test"), (err) => err.code === "conflict");
  assert.equal(conflict, true);
  ctx.fetch = async () => ({
    status: 200,
    ok: true,
    json: async () => { throw new SyntaxError("Proxy returned HTML"); },
  });
  await assert.rejects(ctx.request("/test"), (err) => err.code === "invalid_response");
});

function recoveryContext(api) {
  const events = [];
  const state = {
    current: { id: "submission-1", version: 2, status: "DRAFT" },
    submitKey: "submit-original",
  };
  const ctx = vm.createContext({
    state, api,
    setReloadRequired: (value) => events.push(["reload", value]),
    clearPendingFiles: () => events.push("files-cleared"),
    loadHistory: async () => events.push("history"),
    loadWorkspace: async () => { throw new Error("Workspace unavailable"); },
    setBanner: (message, type) => events.push(["banner", message, type]),
    renderDetail: () => events.push("detail"),
    show: (name) => events.push(["show", name]),
    tg: null,
    setEditorError: (message) => events.push(["error", message]),
  });
  vm.runInContext(recovery +
    ";this.confirm = confirmSubmissionAfterFailure;", ctx);
  return { ctx, state, events };
}

test("lost POST response is reconciled as accepted, even if workspace refresh fails", async () => {
  const { ctx, state, events } = recoveryContext(async () => ({
    submission: { id: "submission-1", status: "SUBMITTED" },
  }));
  const handled = await ctx.confirm({ code: "network_error", message: "lost response" });
  assert.equal(handled, true);
  assert.equal(state.current.status, "SUBMITTED");
  assert.equal(state.submitKey, null);
  assert.ok(events.some((x) => Array.isArray(x) && x[0] === "banner" && x[2] === "warning"));
  assert.ok(events.some((x) => Array.isArray(x) && x[0] === "show" && x[1] === "detail"));
});

test("unconfirmed POST keeps the original idempotency key for retry", async () => {
  const { ctx, state } = recoveryContext(async () => ({
    submission: { id: "submission-1", version: 2, status: "DRAFT" },
  }));
  assert.equal(await ctx.confirm({ code: "request_timeout" }), false);
  assert.equal(state.submitKey, "submit-original");
  assert.equal(state.current.status, "DRAFT");
});

test("ambiguous file upload only recovers a new matching SHA-256 attachment", async () => {
  const bytes = Buffer.from("uploaded test bytes", "utf8");
  const digest = await webcrypto.subtle.digest("SHA-256", bytes);
  const hash = Buffer.from(digest).toString("hex");
  const state = { current: { id: "submission-1", version: 1 } };
  const ctx = vm.createContext({
    crypto: webcrypto, Uint8Array,
    state,
    api: async () => ({
      submission: { id: "submission-1", version: 2 },
      files: [
        { id: "old-file", sha256: hash, size: bytes.length },
        { id: "new-file", sha256: hash, size: bytes.length },
        { id: "wrong-hash", sha256: "abc", size: bytes.length },
      ],
    }),
  });
  vm.runInContext(fileRecovery + ";this.recover = verifyAmbiguousFileUpload;", ctx);
  const matched = await ctx.recover(
    { file: { size: bytes.length, arrayBuffer: async () => bytes } },
    new Set(["old-file"])
  );
  assert.equal(matched.id, "new-file");
  assert.equal(state.current.version, 2);
  assert.equal(await ctx.recover(
    { file: { size: bytes.length, arrayBuffer: async () => bytes } },
    new Set(["old-file", "new-file"])
  ), null);
});

test("submit retry reuses the same key and never declares an unconfirmed result successful", async () => {
  const keys = [];
  let accepted = false;
  const state = {
    current: { id: "submission-1", version: 1, status: "DRAFT" },
    submitKey: null,
    reloadRequired: false,
    autosavePending: false,
    uploadPending: false,
    uploadPreparing: false,
    submitting: false,
  };
  const ctx = vm.createContext({
    state,
    makeIdempotencyKey: () => "stable-key",
    validateReadyForm: () => true,
    autosave: async () => true,
    uploadQueuedFiles: async () => true,
    updateActionAvailability: () => {},
    setEditorError: () => {},
    setBusy: () => {},
    api: async (path, opts) => {
      if (opts && opts.method === "POST") {
        keys.push(opts.headers["Idempotency-Key"]);
        if (keys.length === 1) {
          const error = new Error("connection dropped");
          error.code = "network_error";
          throw error;
        }
        return { submission: { id: "submission-1", status: "SUBMITTED" } };
      }
      return { submission: { id: "submission-1", version: 1, status: "DRAFT" } };
    },
    clearPendingFiles: () => {},
    setReloadRequired: () => {},
    loadHistory: async () => {},
    loadWorkspace: async () => {},
    setBanner: () => {},
    renderDetail: () => { accepted = true; },
    show: () => {},
    tg: null,
  });
  vm.runInContext(recovery + submit + ";this.submit = submitCurrent;", ctx);
  await ctx.submit();
  assert.equal(accepted, false);
  assert.equal(state.submitKey, "stable-key");
  await ctx.submit();
  assert.equal(accepted, true);
  assert.deepEqual(keys, ["stable-key", "stable-key"]);
});
