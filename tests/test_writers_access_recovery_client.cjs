"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const script = fs.readFileSync(
  path.join(__dirname, "..", "writers_submission", "static", "app.js"), "utf8"
);
function part(start, end) {
  const first = script.indexOf(start);
  const last = script.indexOf(end, first);
  assert.ok(first >= 0 && last > first, "missing source segment: " + start);
  return script.slice(first, last);
}
const access = part("  function setEditorError(message) {", "  function setBusy(active)");
const api = part("  async function api(path, options = {}) {", "  async function bootstrapSession()");
const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    elements.set(id, {
      textContent: "",
      classList: {
        hidden: true,
        toggle(name, visible) {
          if (name === "hidden") { this.hidden = visible; }
        },
      },
    });
  }
  return elements.get(id);
}

function harness(response, clipboardImpl = async () => {}) {
  elements.clear();
  const copied = [];
  let requestCount = 0;
  const context = {
    Headers, Date, JSON, Error, RegExp,
    navigator: {
      clipboard: { async writeText(value) { copied.push(value); await clipboardImpl(value); } },
    },
    $: element,
    currentFields: () => ({ title: "Личная неопубликованная работа",
      details: { main_genres: ["Романтика"], warnings_present: "no" } }),
    setReloadRequired() {},
    fetch: async () => { requestCount += 1; return response; },
  };
  vm.runInNewContext(access + api +
    "\nthis.accessActions = {setEditorError, copyDraftToClipboard, api};", context);
  return { ...context.accessActions, copied, requests: () => requestCount };
}

test("a denied create is understandable and does not silently save", async () => {
  const h = harness({
    status: 403, ok: false,
    json: async () => ({ error: "not_eligible" }),
  });
  await assert.rejects(h.api("/api/writers/submissions", {method:"POST"}), (err) => {
    assert.equal(err.code, "not_eligible");
    assert.match(err.message, /писательского чата/);
    return true;
  });
  h.setEditorError("Не удалось сохранить черновик: Фосген не видит тебя участником");
  assert.equal(element("copyDraftButton").classList.hidden, false);
  assert.equal(element("draftBackupHint").classList.hidden, false);
  assert.equal(h.copied.length, 0, "clipboard must require a direct user action");
  await h.copyDraftToClipboard();
  assert.equal(h.copied.length, 1);
  const backup = JSON.parse(h.copied[0]);
  assert.equal(backup.type, "fosgen-writers-draft-backup");
  assert.equal(backup.fields.title, "Личная неопубликованная работа");
  assert.equal(backup.fields.details.main_genres[0], "Романтика");
});

test("Telegram failure explains a retryable access check instead of blaming description", async () => {
  const h = harness({
    status: 503, ok: false,
    json: async () => ({ error: "eligibility_unavailable" }),
  });
  await assert.rejects(h.api("/api/writers/submissions", {method:"POST"}), (err) => {
    assert.equal(err.status, 503);
    assert.equal(err.code, "eligibility_unavailable");
    assert.match(err.message, /недоступна проверка/);
    assert.doesNotMatch(err.message, /описание слишком/);
    return true;
  });
});

test("backup stays hidden without error and reports clipboard failure accurately", async () => {
  const h = harness({}, async () => { throw new Error("denied"); });
  h.setEditorError("");
  assert.equal(element("copyDraftButton").classList.hidden, true);
  h.setEditorError("Не удалось сохранить черновик");
  await h.copyDraftToClipboard();
  assert.match(element("draftBackupHint").textContent, /Буфер обмена недоступен/);
  assert.equal(h.copied.length, 1);
});
