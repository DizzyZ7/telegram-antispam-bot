"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "writers_submission", "static", "app.js"), "utf8");
function section(start, end) {
  const a = source.indexOf(start);
  const b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, "missing client section " + start);
  return source.slice(a, b);
}
const draftLogic =
  section("  function markChanged() {", "  function renderPalette()") +
  section("  async function newDraft() {", "  async function submitCurrent()");

function createHarness(apiImpl) {
  const status = [], errors = [], timers = [];
  const state = {
    current: null, attachments: [], timeline: [], autosaveTimer: null,
    autosavePending: false, savePromise: null, editSequence: 0,
    savedSequence: 0, createKey: null, uploadPending: false,
    reloadRequired: false,
  };
  const form = { title: { focus() {} } };
  const context = {
    state, form,
    views: { editor: { classList: { contains: () => true } } },
    refreshCounters() {}, renderIkfCover() {}, renderAttachments() {},
    fillForm() {}, show() {}, setReloadRequired(v) { state.reloadRequired = v; },
    setEditorError(m) { errors.push(m); }, setSaveState(v) { status.push(v); },
    updateActionAvailability() {},
    makeIdempotencyKey: () => "create-retry-stable",
    currentFields: () => ({ title: context.textValue }),
    api: apiImpl,
    window: {
      setTimeout(callback) {
        const timer = { callback, canceled: false };
        timers.push(timer);
        return timer;
      },
    },
    clearTimeout(timer) { if (timer) timer.canceled = true; },
    textValue: "",
    console,
  };
  vm.runInNewContext(draftLogic + "\nthis.draftActions={markChanged,newDraft,autosave};", context);
  return { context, state, status, errors, timers, ...context.draftActions };
}
async function drain() {
  for (let i = 0; i < 30; i += 1) { await Promise.resolve(); }
}

test("first edit creates a real server draft automatically", async () => {
  const calls = [];
  const h = createHarness(async (url, opts) => {
    calls.push({ url, opts });
    assert.equal(opts.method, "POST");
    return { submission: { id: "draft-one", version: 1 } };
  });
  await h.newDraft();
  h.context.textValue = "Начатая история";
  h.markChanged();
  // The first edit starts POST immediately without waiting for debounce.
  await drain();
  assert.equal(h.state.current.id, "draft-one");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].opts.json.title, "Начатая история");
  assert.equal(h.state.editSequence, h.state.savedSequence);
  assert.ok(h.status.includes("Сохранено на сервере"));
});

test("edits made during in-flight PATCH are never lost", async () => {
  let resolveFirst;
  const writes = [];
  const h = createHarness((url, opts) => {
    assert.equal(opts.method, "PATCH");
    writes.push(opts.json.title);
    if (writes.length === 1) {
      return new Promise(resolve => { resolveFirst = resolve; });
    }
    return Promise.resolve({ submission: { id: "draft-one", version: 3 } });
  });
  h.state.current = { id: "draft-one", version: 1 };
  h.context.textValue = "Версия первая";
  h.markChanged();
  const pending = h.autosave({ immediate: true });
  await drain();
  assert.deepEqual(writes, ["Версия первая"]);
  h.context.textValue = "Версия вторая";
  h.markChanged();
  resolveFirst({ submission: { id: "draft-one", version: 2 } });
  assert.equal(await pending, true);
  assert.deepEqual(writes, ["Версия первая", "Версия вторая"]);
  assert.equal(h.state.current.version, 3);
  assert.equal(h.state.savedSequence, h.state.editSequence);
});

test("failed POST retains dirty edits and idempotency key on retry", async () => {
  const keys = [];
  let attempt = 0;
  const h = createHarness(async (_url, opts) => {
    keys.push(opts.headers["Idempotency-Key"]);
    attempt += 1;
    if (attempt === 1) { throw Error("Internet disconnected"); }
    return { submission: { id: "resumed", version: 1 } };
  });
  h.context.textValue = "Мой будущий фанфик";
  h.markChanged();
  assert.equal(await h.autosave({ immediate: true }), false);
  assert.equal(h.state.savedSequence, 0);
  assert.equal(h.state.editSequence, 1);
  assert.match(h.errors.at(-1), /Не удалось сохранить/);
  assert.equal(await h.autosave({ immediate: true }), true);
  assert.equal(h.state.current.id, "resumed");
  assert.deepEqual(keys, ["create-retry-stable", "create-retry-stable"]);
});

test("a failed draft-save prevents switching to another blank form", async () => {
  const h = createHarness(async () => { throw Error("No network"); });
  h.context.views.editor.classList.contains = () => false;
  h.state.current = { id: "draft-one", version: 1 };
  h.context.textValue = "Очень важный текст";
  h.markChanged();
  await h.newDraft();
  assert.equal(h.state.current.id, "draft-one");
  assert.match(h.errors.at(-1), /не сохранился/);
  assert.equal(h.context.textValue, "Очень важный текст");
});