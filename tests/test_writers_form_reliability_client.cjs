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


test("visible Save action retries selected files without requiring finished application", async () => {
  const js = section("  async function saveDraftAndFiles(", "  async function submitCurrent(");
  const calls = [];
  const state = {
    reloadRequired: false,
    autosavePending: false,
    uploadPending: false,
    uploadPreparing: false,
    submitting: false,
    pendingFiles: [{ file: "photo.jpg" }],
  };
  const ctx = vm.createContext({
    state,
    autosave: async () => { calls.push("draft"); return true; },
    uploadQueuedFiles: async () => { calls.push("files"); return true; },
  });
  vm.runInContext(js + ";this.save = saveDraftAndFiles;", ctx);
  assert.equal(await ctx.save(), true);
  assert.deepEqual(calls, ["draft", "files"]);
});

test("server-side validation errors identify the exact failing field in Russian", () => {
  const ctx = vm.createContext({});
  vm.runInContext(transport + ";this.failure=requestFailure;", ctx);
  assert.match(ctx.failure(422, "validation_error",
    "work description needs at least three sentences (completed)").message, /Описание: нужно не менее трех/);
  assert.match(ctx.failure(422, "validation_error",
    "unsupported file extension").message, /Формат файла не поддерживается/);
});

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
    clearEmergencyBackupIfSynced: () => events.push("emergency-cleared"),
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
    clearEmergencyBackupIfSynced: () => {},
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


const draftRecoveryCode = section(
  "  function draftMatchesSavedRequest(", "  async function autosave("
);
const autosaveCode = section(
  "  async function autosave(", "  async function showAcceptedSubmission("
);
const reloadGuardCode = section(
  "  async function reloadDraftSafely(", "  function bindEvents("
);
const inputLockCode = section(
  "  function lockFormDuringSubmission(", "  async function submitCurrent("
);

function exampleDraftFields() {
  return {
    title: "Мой фанфик",
    work_type: "ФФ",
    genre: "Романтика",
    description: "Начало. Продолжение. Финал.",
    body_text: "Первая глава.",
    external_url: "https://ficbook.net/readfic/123",
    details: {
      form_version: 3,
      fandom: "Мой фандом",
      rating: "PG-13",
      main_genres: ["Приключения", "Романтика"],
      warnings_present: "no",
      warnings: [],
      size_category: "мини",
      completion: "в процессе",
      visual_mode: "palette",
      palette_colors: ["#5b67f1", "#ef86ac", "#78cfbc", "#ffc777"],
      cover_file_id: null,
      extra_links: [],
      size_words: null,
      pages: null,
      parts: null,
    },
  };
}

function draftRecoveryHarness(fields, serverRevision) {
  const state = {
    current: { id: "draft-a", version: 1, revision: { id: "revision-a" } },
    editSequence: 1,
    savedSequence: 0,
    reloadRequired: false,
    uploadPending: false,
    autosavePending: false,
    savePromise: null,
    autosaveTimer: null,
  };
  const events = [];
  let patchCalls = 0;
  const ctx = vm.createContext({
    state,
    clearTimeout: () => {},
    currentFields: () => structuredClone(fields),
    clearEmergencyBackupIfSynced: () => {},
    setSaveState: (message) => events.push(["state", message]),
    setEditorError: (message) => events.push(["error", message]),
    setReloadRequired: (value) => { state.reloadRequired = value; },
    updateActionAvailability: () => {},
    api: async (path) => {
      if (path.endsWith("/draft-a")) {
        // PATCH and GET use the same URL; distinguish by the options method.
        throw new Error("Test must provide an API stub with methods.");
      }
      throw new Error("Unexpected API endpoint");
    },
    window: { setTimeout: () => 1 },
  });
  ctx.api = async (_path, options) => {
    if (options && options.method === "PATCH") {
      patchCalls += 1;
      const error = new Error("Request timed out after commit");
      error.code = "request_timeout";
      throw error;
    }
    return {
      submission: {
        id: "draft-a", status: "DRAFT", version: 2,
        revision: { ...structuredClone(serverRevision), id: "revision-a" },
      },
    };
  };
  vm.runInContext(draftRecoveryCode + autosaveCode +
    ";this.save = autosave;this.matches = draftMatchesSavedRequest;", ctx);
  return { ctx, state, events, getPatchCalls: () => patchCalls };
}

test("lost PATCH response is recovered only when entire server draft matches", async () => {
  const fields = exampleDraftFields();
  const server = {
    ...structuredClone(fields),
    details: {
      ...structuredClone(fields.details),
      palette_colors: fields.details.palette_colors.map((color) => color.toUpperCase()),
    },
  };
  const h = draftRecoveryHarness(fields, server);
  assert.equal(await h.ctx.save({ immediate: true }), true);
  assert.equal(h.state.current.version, 2);
  assert.equal(h.state.savedSequence, 1);
  assert.equal(h.state.reloadRequired, false);
  assert.equal(h.getPatchCalls(), 1);
});

test("changes from another editor are not mistakenly adopted as own saved PATCH", async () => {
  const fields = exampleDraftFields();
  const server = structuredClone(fields);
  server.details.rating = "NC-17";
  const h = draftRecoveryHarness(fields, server);
  assert.equal(await h.ctx.save({ immediate: true }), false);
  assert.equal(h.state.savedSequence, 0);
  assert.equal(h.state.current.version, 1);
  assert.match(h.events.filter((event) => event[0] === "error").at(-1)[1], /Не удалось сохранить/);
});

test("reload does not discard dirty text or chosen files without confirmation", async () => {
  const events = [];
  const state = {
    current: { id: "draft-a" },
    editSequence: 3, savedSequence: 2,
    pendingFiles: [{ name: "cover.jpg" }],
    autosavePending: false, savePromise: null,
    uploadPending: false, uploadPreparing: false, submitting: false,
  };
  let approved = false;
  const ctx = vm.createContext({
    state,
    window: { confirm: () => approved },
    openSubmission: async (id) => { events.push(id); },
    setEditorError: () => {},
  });
  vm.runInContext(reloadGuardCode + ";this.reload = reloadDraftSafely;", ctx);
  assert.equal(await ctx.reload(), false);
  assert.deepEqual(events, []);
  approved = true;
  assert.equal(await ctx.reload(), true);
  assert.deepEqual(events, ["draft-a"]);
  state.uploadPending = true;
  assert.equal(await ctx.reload(), false);
  assert.deepEqual(events, ["draft-a"]);
});

test("submit editing lock toggles all form inputs during request", () => {
  const controls = [{ disabled: false }, { disabled: false }, { disabled: false }];
  const ctx = vm.createContext({
    document: { querySelectorAll: (selector) => {
      assert.match(selector, /#editorView/);
      return controls;
    }},
  });
  vm.runInContext(inputLockCode + ";this.lock=lockFormDuringSubmission;", ctx);
  ctx.lock(true);
  assert.ok(controls.every((control) => control.disabled));
  ctx.lock(false);
  assert.ok(controls.every((control) => !control.disabled));
});


test("reopening the same draft preserves uncertain submit key; a new revision resets it", async () => {
  const code = section("  async function openSubmission(", "  async function newDraft(");
  const state = {
    current: { id: "draft-a", revision: { id: "rev-a" } },
    submitKey: "original-submit-key",
    pendingFiles: [],
    editSequence: 5,
    savedSequence: 4,
  };
  let remoteRevision = "rev-a";
  const ctx = vm.createContext({
    state,
    api: async () => ({
      submission: { id: "draft-a", version: 3, status: "DRAFT", revision: { id: remoteRevision } },
      files: [],
    }),
    setBusy: () => {},
    clearImagePreview: () => {},
    loadHistory: async () => {},
    fillForm: () => {},
    setReloadRequired: () => {},
    renderAttachments: () => {},
    show: () => {},
    renderDetail: () => {},
    setBanner: () => {},
  });
  vm.runInContext(code + ";this.open=openSubmission;", ctx);
  await ctx.open("draft-a");
  assert.equal(state.submitKey, "original-submit-key");
  remoteRevision = "rev-b";
  await ctx.open("draft-a");
  assert.equal(state.submitKey, null);
});


test("malformed draft fetch cannot overwrite unsaved editor or chosen photo", async () => {
  const code = section("  async function openSubmission(", "  async function newDraft(");
  const errors = [];
  const original = { id: "draft-a", version: 1, status: "DRAFT" };
  const state = {
    current: original, pendingFiles: [{ name: "cover.jpg" }],
    attachments: [{ id: "old-file" }], editSequence: 3, savedSequence: 2,
  };
  const ctx = vm.createContext({
    state,
    api: async () => ({ submission: { id: "draft-a", status: "DRAFT" }, files: [] }),
    setBusy: () => {},
    clearImagePreview: () => { throw Error("should not clear any preview"); },
    loadHistory: async () => {},
    setBanner: (message) => errors.push(message),
    fillForm: () => { throw Error("must not replace dirty fields"); },
    renderAttachments: () => {},
    renderDetail: () => {},
    show: () => {},
  });
  vm.runInContext(code + ";this.open=openSubmission;", ctx);
  await ctx.open("draft-a");
  assert.equal(state.current, original);
  assert.equal(state.pendingFiles.length, 1);
  assert.equal(state.attachments.length, 1);
  assert.match(errors.at(-1), /неполный черновик/);
});

test("malformed empty workspace response does not hide already loaded drafts", async () => {
  const code = section("  async function loadWorkspace(", "  async function loadHistory(");
  const existing = [{ id: "draft-a" }];
  const state = { items: existing };
  const ctx = vm.createContext({
    state,
    api: async () => ({}),
    renderWorkspace: () => { throw Error("must not render empty invalid list"); },
  });
  vm.runInContext(code + ";this.load=loadWorkspace;", ctx);
  await assert.rejects(ctx.load(), /неполный список работ/);
  assert.equal(state.items, existing);
});


test("moderation delivery text never claims an unread Telegram card was read", () => {
  const src = section("  const deliveryLabels = {", "  const $ = (id)");
  const context = vm.createContext({});
  vm.runInContext(src + ";this.deliveryLabel=deliveryLabel;", context);
  assert.match(context.deliveryLabel({status:"QUEUED"}), /очереди/);
  assert.match(context.deliveryLabel({status:"RETRYING"}), /повторит/);
  assert.match(context.deliveryLabel({status:"DELAYED"}), /задерживается/);
  assert.match(context.deliveryLabel({status:"NEEDS_ATTENTION"}), /администрации/);
  assert.match(context.deliveryLabel({status:"DELIVERED"}), /чат модерации/);
  assert.match(context.deliveryLabel({status:"DELIVERED"}), /не означает прочтение/);
  const html = readFileSync(
    resolve(__dirname, "../writers_submission/static/index.html"), "utf8"
  );
  assert.match(html, /id="refreshDeliveryButton"/);
  assert.match(html, /app\.js\?v=sketch-collection-v8/);
});
