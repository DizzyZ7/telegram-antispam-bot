"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.join(__dirname, "..", "writers_submission", "static", "app.js"), "utf8"
);
const start = source.indexOf("  function updateUploadProgress(");
const end = source.indexOf("  async function uploadFiles(", start);
assert.ok(start >= 0 && end > start, "progress helper must be in app.js");

function harness() {
  const nodes = new Map();
  const requests = [];
  let reload = null;
  function element(id) {
    if (!nodes.has(id)) {
      nodes.set(id, {
        classList: {
          removed: [],
          remove(name) { this.removed.push(name); },
        },
        dataset: {},
        textContent: "",
        value: undefined,
        removeAttribute(name) {
          assert.equal(name, "value");
          delete this.value;
        },
      });
    }
    return nodes.get(id);
  }
  class FakeXHR {
    upload = {
      handlers: {},
      addEventListener(name, handler) { this.handlers[name] = handler; },
    };
    constructor() { requests.push(this); }
    open(method, url, async) {
      this.method = method; this.url = url; this.async = async;
    }
    send(body) { this.body = body; }
  }
  const ctx = {
    XMLHttpRequest: FakeXHR,
    $: element,
    setReloadRequired(value) { reload = value; },
  };
  vm.runInNewContext(
    source.slice(start, end) +
    "\nthis.uploadHelpers={updateUploadProgress,sendFileWithProgress};",
    ctx,
    { filename: "app.js" }
  );
  return {
    ...ctx.uploadHelpers, nodes, requests, element,
    getReload: () => reload,
  };
}

test("real upload progress shows percentage on the correct picker", () => {
  const h = harness();
  h.updateUploadProgress("file", "Передаю PDF", 51.5);
  assert.equal(h.element("fileUploadBar").value, 52);
  assert.equal(h.element("fileUploadPercent").textContent, "52%");
  assert.equal(h.element("fileUploadLabel").textContent, "Передаю PDF");
  assert.equal(h.element("fileUploadProgress").dataset.phase, "active");
  assert.ok(h.element("fileUploadProgress").classList.removed.includes("hidden"));
  assert.equal(h.element("imageUploadProgress").dataset.phase, undefined);

  h.updateUploadProgress("image", "Сохраняем HEIC на сервере", null, "processing");
  assert.equal(h.element("imageUploadBar").value, undefined);
  assert.equal(h.element("imageUploadPercent").textContent, "Подождите");
  assert.equal(h.element("imageUploadProgress").dataset.phase, "processing");
  h.updateUploadProgress("image", "✓ Файл добавлен", 100, "done");
  assert.equal(h.element("imageUploadPercent").textContent, "100%");
  assert.equal(h.element("imageUploadProgress").dataset.phase, "done");
});

test("XHR uses same-origin cookies and does not confirm save at 100 percent", async () => {
  const h = harness(), percents = [], stages = [];
  const submitted = { fakeFormData: true };
  let completed = false;
  const promise = h.sendFileWithProgress(
    "/api/writers/submissions/id/files", submitted,
    (p) => percents.push(p), () => stages.push("server-processing")
  ).then((v) => { completed = true; return v; });
  const xhr = h.requests[0];
  assert.equal(xhr.method, "POST");
  assert.equal(xhr.withCredentials, true);
  assert.equal(xhr.timeout, 300000);
  assert.equal(xhr.body, submitted);
  xhr.upload.handlers.progress({
    lengthComputable: true, loaded: 250, total: 1000,
  });
  xhr.upload.handlers.progress({ lengthComputable: false, loaded: 2, total: 0 });
  xhr.upload.handlers.load();
  assert.deepEqual(percents, [25, null]);
  assert.deepEqual(stages, ["server-processing"]);
  assert.equal(completed, false, "100% network transfer must not equal server success");
  xhr.status = 201;
  xhr.responseText = JSON.stringify({ id: "stored-file", filename: "cover.png" });
  xhr.onload();
  const saved = await promise;
  assert.equal(saved.id, "stored-file");
  assert.equal(completed, true);
});

test("XHR surfaces HTTP 422 and 409 with meaningful errors", async () => {
  const h = harness();
  const bad = h.sendFileWithProgress("/upload", {}, () => {}, () => {});
  h.requests[0].status = 422;
  h.requests[0].responseText = JSON.stringify({
    error: "validation_error", message: "Файл слишком большой",
  });
  h.requests[0].onload();
  await assert.rejects(bad, /Файл слишком большой/);
  const conflict = h.sendFileWithProgress("/upload", {}, () => {}, () => {});
  h.requests[1].status = 409;
  h.requests[1].responseText = JSON.stringify({error:"conflict"});
  h.requests[1].onload();
  await assert.rejects(conflict, /другой вкладке/);
  assert.equal(h.getReload(), true);
});

test("network failure and fake HTTP success are never considered saved", async () => {
  const h = harness();
  const dropped = h.sendFileWithProgress("/upload", {}, () => {}, () => {});
  h.requests[0].onerror();
  await assert.rejects(dropped, /соединение прервано/);

  const invalid = h.sendFileWithProgress("/upload", {}, () => {}, () => {});
  h.requests[1].status = 201;
  h.requests[1].responseText = "<html>Unexpected proxy response</html>";
  h.requests[1].onload();
  await assert.rejects(invalid, /не подтвердил сохранение/);
});

test("upload markup has both accessible progress regions", () => {
  const html = fs.readFileSync(
    path.join(__dirname, "..", "writers_submission", "static", "index.html"), "utf8"
  );
  assert.ok(html.includes('id="fileUploadBar"'));
  assert.ok(html.includes('id="imageUploadBar"'));
  assert.ok(html.includes('role="status"'));
  assert.ok(html.includes('aria-live="polite"'));
  assert.ok(html.includes('app.js?v=upload-progress-v1'));
});
