"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const vm = require("node:vm");

const source = readFileSync(resolve(__dirname, "../writers_submission/static/app.js"), "utf8");
const draftId = "c16a5cb9-fe58-4747-af68-dcd5c2d61905";

function harness({ workflow = false } = {}) {
  const nodes = new Map(), alerts = [], xhrs = [];
  const node = (id) => {
    if (!nodes.has(id)) nodes.set(id, {
      classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
      setAttribute() {}, addEventListener() {}, textContent: "", disabled: false,
    });
    return nodes.get(id);
  };
  class FakeXHR {
    upload = { addEventListener() {} };
    constructor() { xhrs.push(this); }
    open() {}
    send() {}
    getResponseHeader(name) { return name === "X-Request-ID" ? "123456abcdef" : null; }
  }
  const ctx = vm.createContext({
    Headers, AbortController, XMLHttpRequest: FakeXHR, clearTimeout,
    navigator: { onLine: true },
    document: { getElementById: node, querySelectorAll: () => [] },
    window: {
      Telegram: { WebApp: { platform: "webk", version: "8.0", initData: "private-init-data" } },
      setTimeout, clearTimeout,
      confirm: () => true, alert: (text) => alerts.push(text),
    },
    fetch: async () => new Response(JSON.stringify({ error: "database_unavailable" }), {
      status: 503, headers: { "X-Request-ID": "aabbccddeeff" },
    }),
    Response,
  });
  const dependencies = workflow
    ? `currentFields = () => ({title:"private work", work_type:"Оридж", genre:"Слэш", description:"One. Two. Three.", body_text:"private body", details:{}});
       validateReadyForm = () => true; clearEmergencyBackupIfSynced = () => {};`
    : "";
  vm.runInContext(source.replace('  document.addEventListener("DOMContentLoaded", boot);',
    dependencies + "  window.testApp = {api, state, sendSupportReport, supportReportPayload, sendFileWithProgress, setEditorError, autosave, submitCurrent};"), ctx);
  return { ctx, app: ctx.window.testApp, alerts, xhrs, node };
}

test("failed save retains cause/reference through successful and failed recovery reads", async () => {
  const { ctx, app } = harness();
  app.state.bootstrapped = true;
  app.state.current = { id: draftId, version: 3, status: "DRAFT", revision: { body_text: "private text" } };
  app.state.editSequence = 4;
  app.state.savedSequence = 2;
  app.state.pendingFiles = [{ file: { name: "private-story.docx" } }];
  await assert.rejects(app.api(`/api/writers/submissions/${draftId}`, {
    method: "PATCH", json: { body_text: "private-form-body", expected_version: 3 },
  }));
  ctx.fetch = async () => new Response(JSON.stringify({ submission: {} }));
  await app.api(`/api/writers/submissions/${draftId}`, { diagnosticRecovery: true });
  ctx.fetch = async () => new Response('{"error":"upstream_unavailable"}', { status: 502 });
  await assert.rejects(app.api(`/api/writers/submissions/${draftId}`, { diagnosticRecovery: true }));
  const report = JSON.parse(JSON.stringify(app.supportReportPayload()));
  assert.equal(report.category, "save_failed");
  assert.equal(report.platform, "webk");
  const d = report.diagnostics;
  assert.equal(d.last_failure.operation, "save");
  assert.equal(d.last_failure.status, 503);
  assert.equal(d.last_failure.code, "database_unavailable");
  assert.equal(d.last_failure.reference, "aabbccddeeff");
  assert.equal(d.recent_requests.length, 3);
  assert.equal(d.dirty, true);
  assert.equal(d.pending_file_count, 1);
  assert.equal(d.submission_id, draftId);
  assert.equal(d.submission_version, 3);
  assert.equal(d.telegram_version, "8.0");
  assert.ok(d.last_failure.at_ms > 0);
  assert.ok(d.last_failure.elapsed_ms >= 0);
  const serialized = JSON.stringify(report);
  for (const secret of ["private text", "private-story.docx", "private-form-body"]) {
    assert.ok(!serialized.includes(secret));
  }
});

test("report categorizes actual submit failure and sends bounded history, never exception text", async () => {
  const { ctx, app, alerts } = harness();
  app.state.bootstrapped = true;
  ctx.fetch = async () => new Response(JSON.stringify({ error: "validation_error", message: "work description needs at least three sentences" }), { status: 422 });
  await assert.rejects(app.api(`/api/writers/submissions/${draftId}/submit`, { method: "POST", json: {} }));
  ctx.fetch = async () => new Response('{"items":[]}');
  for (let i = 0; i < 12; i++) await app.api("/api/writers/submissions");
  let sent;
  ctx.fetch = async (path, options) => {
    assert.equal(path, "/api/writers/support");
    sent = JSON.parse(options.body);
    return new Response('{"status":"sent","reference":"112233aabbcc"}');
  };
  await app.sendSupportReport();
  assert.equal(sent.category, "submit_failed");
  assert.equal(sent.diagnostics.last_failure.field, "description");
  assert.equal(sent.diagnostics.last_failure.rule, "description_sentences");
  assert.equal(sent.diagnostics.recent_requests.length, 8);
  assert.ok(!JSON.stringify(sent).includes("at least three sentences"));
  assert.match(alerts[0], /112233aabbcc/);
});

test("restored session preserves its specific code and does not silently replay mutations", async () => {
  const { ctx, app } = harness();
  const calls = [];
  ctx.fetch = async (path) => {
    calls.push(path);
    return path === "/api/writers/session"
      ? new Response(null, { status: 204 })
      : new Response('{"error":"unauthorized"}', { status: 401, headers: { "X-Request-ID": "111122223333" } });
  };
  await assert.rejects(app.api(`/api/writers/submissions/${draftId}`, { method: "PATCH", json: {} }),
    (error) => error.code === "session_restored_unconfirmed" && error.status === 401);
  assert.equal(calls.length, 2);
  assert.equal(app.supportReportPayload().diagnostics.last_failure.reference, "111122223333");
});

test("upload failures share diagnostics and keep response references", async () => {
  const { app, xhrs } = harness();
  const pending = app.sendFileWithProgress(`/api/writers/submissions/${draftId}/files`, {}, () => {}, () => {});
  xhrs[0].status = 503;
  xhrs[0].responseText = '{"error":"telegram_file_staging_unavailable"}';
  xhrs[0].onload();
  await assert.rejects(pending);
  const report = app.supportReportPayload();
  assert.equal(report.category, "other");
  assert.equal(report.diagnostics.last_failure.operation, "upload");
  assert.equal(report.diagnostics.last_failure.reference, "123456abcdef");
  assert.equal(report.diagnostics.last_failure.code, "telegram_file_staging_unavailable");
});

test("no observed failure is not falsely labeled as a failed save", () => {
  const { app } = harness();
  app.state.bootstrapped = true;
  assert.equal(app.supportReportPayload().category, "other");
  assert.equal(app.supportReportPayload().diagnostics.last_failure, null);
});

test("local validation is reported without copying the user's form or error text", () => {
  const { app } = harness();
  const message = "Нужно минимум 3 законченных предложения (с точкой, ! или ?). private-secret";
  app.setEditorError(message, new Error(message), "submit");
  const report = app.supportReportPayload();
  assert.equal(report.category, "submit_failed");
  assert.equal(report.diagnostics.last_failure.code, "client_validation_failed");
  assert.equal(report.diagnostics.last_failure.field, "description");
  assert.equal(report.diagnostics.last_failure.rule, "description_sentences");
  assert.ok(!JSON.stringify(report.diagnostics).includes("private-secret"));
});

test("invalid draft confirmation retains HTTP 200 and the originating server reference", async () => {
  const { ctx, app } = harness();
  ctx.fetch = async () => new Response('{"submission":{}}', { headers: { "X-Request-ID": "abcdefabcdef" } });
  await app.api(`/api/writers/submissions/${draftId}`, { method: "PATCH", json: {} });
  const error = new Error("private invalid draft confirmation");
  error.code = "invalid_response";
  app.setEditorError(error.message, error, "save");
  const failure = app.supportReportPayload().diagnostics.last_failure;
  assert.equal(failure.status, 200);
  assert.equal(failure.reference, "abcdefabcdef");
  assert.equal(failure.code, "invalid_response");
});

test("autosave schema failure keeps originating PATCH metadata through actual reconciliation", async () => {
  const { ctx, app } = harness({ workflow: true });
  app.state.current = { id: draftId, version: 1, status: "DRAFT", revision: { id: "revision-a" } };
  app.state.editSequence = 1;
  ctx.fetch = async (_path, options) => options.method === "PATCH"
    ? new Response('{"submission":{}}', { headers: { "X-Request-ID": "aaaa11112222" } })
    : new Response(JSON.stringify({ submission: app.state.current }), { headers: { "X-Request-ID": "bbbb11112222" } });
  assert.equal(await app.autosave({ immediate: true }), false);
  const failure = app.supportReportPayload().diagnostics.last_failure;
  assert.equal(failure.status, 200);
  assert.equal(failure.reference, "aaaa11112222");
  assert.equal(failure.operation, "save");
  assert.equal(failure.code, "invalid_response");
});

test("submit schema failure is recorded before actual status reconciliation", async () => {
  const { ctx, app } = harness({ workflow: true });
  app.state.current = { id: draftId, version: 1, status: "DRAFT" };
  ctx.fetch = async (_path, options) => options.method === "POST"
    ? new Response(JSON.stringify({ submission: { id: draftId, status: "DRAFT" } }), { headers: { "X-Request-ID": "aaaa33334444" } })
    : new Response(JSON.stringify({ submission: app.state.current }));
  await app.submitCurrent();
  const report = app.supportReportPayload();
  assert.equal(report.category, "submit_failed");
  assert.equal(report.diagnostics.last_failure.status, 200);
  assert.equal(report.diagnostics.last_failure.reference, "aaaa33334444");
  assert.equal(report.diagnostics.last_failure.code, "invalid_response");
});

test("failed session refresh during save reconciliation cannot replace the initiating failure", async () => {
  const { ctx, app } = harness({ workflow: true });
  app.state.current = { id: draftId, version: 1, status: "DRAFT", revision: { id: "revision-a" } };
  app.state.editSequence = 1;
  ctx.fetch = async (_path, options) => {
    if (options.method === "PATCH") throw new TypeError("private network failure");
    return new Response('{"error":"unauthorized"}', { status: 401 });
  };
  assert.equal(await app.autosave({ immediate: true }), false);
  const d = app.supportReportPayload().diagnostics;
  assert.equal(d.last_failure.operation, "save");
  assert.equal(d.last_failure.code, "network_error");
  assert.ok(d.recent_requests.some((event) => event.operation === "session" && event.status === 401));
});

test("an independent later open failure becomes current after a successful save retry", async () => {
  const { ctx, app } = harness();
  await assert.rejects(app.api(`/api/writers/submissions/${draftId}`, { method: "PATCH", json: {} }));
  ctx.fetch = async () => new Response('{"submission":{}}');
  await app.api(`/api/writers/submissions/${draftId}`, { method: "PATCH", json: {} });
  ctx.fetch = async () => new Response('{"error":"not_found"}', { status: 404 });
  await assert.rejects(app.api(`/api/writers/submissions/${draftId}`));
  const failure = app.supportReportPayload().diagnostics.last_failure;
  assert.equal(failure.operation, "open");
  assert.equal(failure.status, 404);
});
