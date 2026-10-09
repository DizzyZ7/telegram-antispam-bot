"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "..", "writers_submission", "static", "app.js"), "utf8");
function section(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, "missing " + start);
  return source.slice(a, b);
}
const logic =
  section("  async function submitCurrent() {", "  const MAX_PHOTO_BYTES") +
  section("  async function queueFilesLocally(", "  async function deleteAttachment(");
function harness() {
  const trace = [], errorMessages = [];
  const state = {
    current: { id: "draft-id", version: 1 },
    attachments: [],
    pendingFiles: [],
    editSequence: 0, savedSequence: 0,
    uploadPending: false, uploadPreparing: false,
    submitting: false, reloadRequired: false,
    coverFileId: null,
  };
  let valid = true, saving = true, failUpload = false;
  const context = {
    state, trace,
    encodeURIComponent,
    FormData: class FakeFormData { append() {} },
    makeIdempotencyKey: () => "local-a",
    prepareUploadFile: async f => f,
    renderAttachments: () => trace.push("render"),
    updateImageStatus: () => {},
    updateActionAvailability: () => {},
    setEditorError: text => errorMessages.push(text),
    updateUploadProgress: () => {},
    setSaveState: () => {},
    clearImagePreview: () => {},
    clearPendingFiles: () => { state.pendingFiles = []; state.coverFileId = null; },
    setReloadRequired: v => { state.reloadRequired = v; },
    setBusy: () => {},
    loadHistory: async () => {},
    loadWorkspace: async () => {},
    renderDetail: () => {},
    show: () => {},
    tg: null,
    $: id => ({ value: "", classList: {add(){},remove(){}}, textContent: "" }),
    URL: {createObjectURL: () => "blob:test"},
    validateReadyForm: () => {
      trace.push("validate");
      if (!valid) throw Error("Заполни анкету");
    },
    autosave: async () => {
      trace.push("autosave");
      return saving;
    },
    sendFileWithProgress: async () => {
      trace.push("upload");
      if (failUpload) throw Error("Telegram storage failed");
      return {id: "saved-photo-id", filename: "cover.jpg", file_class: "jpeg"};
    },
    api: async (url, options) => {
      if (url.endsWith("/submit")) {
        trace.push("submit");
        return {submission: { id: "draft-id", status: "SUBMITTED", version: 4 }};
      }
      trace.push("refresh");
      return {submission: {id: "draft-id", version: 2}};
    },
  };
  vm.runInNewContext(logic + "\nthis.actions={queueFilesLocally,uploadQueuedFiles,submitCurrent};", context, { filename: "app.js" });
  return {
    ...context.actions, state, trace, errorMessages, context,
    setValid: b => { valid = b; },
    setSaving: b => { saving = b; },
    setFailUpload: b => { failUpload = b; },
  };
}
test("selecting cover only queues local file and never posts to Telegram", async () => {
  const h = harness();
  const photo = {name: "private-cover.jpg", size: 1200};
  assert.equal(await h.queueFilesLocally([photo], "image"), true);
  assert.equal(h.state.pendingFiles.length, 1);
  assert.equal(h.state.attachments.length, 0);
  assert.equal(h.state.pendingFiles[0].source, "image");
  assert.ok(!h.trace.includes("upload"));
  assert.ok(!h.trace.includes("submit"));
});
test("invalid or unsaved form never transmits selected private cover", async () => {
  const h = harness();
  await h.queueFilesLocally([{name:"cover.jpg",size:123}], "image");
  h.setValid(false);
  await h.submitCurrent();
  assert.ok(!h.trace.includes("upload"));
  assert.ok(!h.trace.includes("submit"));
  assert.equal(h.state.pendingFiles.length, 1);
  h.setValid(true);
  h.setSaving(false);
  await h.submitCurrent();
  assert.ok(!h.trace.includes("upload"));
  assert.ok(!h.trace.includes("submit"));
});
test("complete form uploads all files before submit; tracks the exact paid-cover UUID", async () => {
  const h = harness();
  await h.queueFilesLocally([{name:"cover.jpg",size:100}], "image");
  await h.queueFilesLocally([{name:"story.txt",size:100}], "file");
  await h.submitCurrent();
  assert.equal(h.trace.filter(x => x === "upload").length, 2);
  assert.equal(h.trace.filter(x => x === "refresh").length, 2);
  assert.ok(h.trace.indexOf("upload") < h.trace.indexOf("submit"));
  assert.ok(h.trace.lastIndexOf("autosave") < h.trace.indexOf("submit"));
  assert.equal(h.state.current.status, "SUBMITTED");
  assert.equal(h.state.pendingFiles.length, 0);
  assert.equal(h.state.attachments.length, 2);
  assert.equal(h.state.coverFileId, null); // completed submission clears local selection
});
test("upload error keeps unsent files and never submits partial packet", async () => {
  const h = harness();
  h.setFailUpload(true);
  await h.queueFilesLocally([{name:"cover.jpg",size:100}], "image");
  await h.submitCurrent();
  assert.equal(h.state.pendingFiles.length, 1);
  assert.equal(h.state.attachments.length, 0);
  assert.ok(h.trace.includes("upload"));
  assert.ok(!h.trace.includes("submit"));
  assert.match(h.errorMessages.at(-1), /Заявка не отправлена/);
});
test("replacing queued paid cover doesn't append multiple paid covers", async () => {
  const h = harness();
  await h.queueFilesLocally([{name:"first.jpg",size:100}], "image");
  await h.queueFilesLocally([{name:"second.jpg",size:100}], "image");
  assert.equal(h.state.pendingFiles.length, 1);
  assert.equal(h.state.pendingFiles[0].file.name, "second.jpg");
});
