"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.join(__dirname, "..", "writers_submission", "static", "app.js"), "utf8"
);
const start = source.indexOf("  const LOCAL_BACKUP_TTL_MS =");
const end = source.indexOf("  function markChanged() {", start);
assert.ok(start >= 0 && end > start, "emergency backup helpers missing");
const helpers = source.slice(start, end);

function harness(userId = 77) {
  const records = new Map();
  const nodes = new Map();
  const events = [];
  const storage = {
    getItem: (key) => records.get(key) ?? null,
    setItem: (key, value) => { records.set(key, String(value)); },
    removeItem: (key) => { records.delete(key); },
  };
  const state = {
    bootstrapped: true,
    current: null,
    createKey: "create-original",
    editSequence: 1,
    savedSequence: 0,
  };
  let fields = {
    title: "Название", work_type: "ФФ", genre: "Джен",
    description: "Текст, который нельзя терять.",
    body_text: "Большая глава",
    external_url: "https://ficbook.net/readfic/123",
    details: { form_version: 3, notes: "Смысл", main_genres: ["Приключения"] },
  };
  let response = null;
  function node(id) {
    if (!nodes.has(id)) {
      nodes.set(id, {
        classList: {
          hidden: true,
          add(name) { if (name === "hidden") this.hidden = true; },
          remove(name) { if (name === "hidden") this.hidden = false; },
        },
        value: "", textContent: "",
      });
    }
    return nodes.get(id);
  }
  const ctx = vm.createContext({
    tg: { initDataUnsafe: { user: { id: userId } } },
    state,
    localStorage: storage,
    $: node,
    makeIdempotencyKey: () => "generated-key",
    currentFields: () => fields,
    newDraft: async () => {
      events.push("newDraft");
      state.current = null;
      state.createKey = "generated-key";
    },
    fillForm: (data) => events.push(["fillForm", data]),
    markChanged: () => events.push("markChanged"),
    openSubmission: async (id) => {
      events.push(["openSubmission", id]);
      state.current = response.submission;
    },
    api: async (url) => {
      events.push(["api", url]);
      if (response instanceof Error) throw response;
      return response;
    },
    draftMatchesSavedRequest: (revision, f) =>
      Boolean(revision && revision.description === f.description),
    setBanner: (message) => events.push(["banner", message]),
    console,
  });
  vm.runInContext(helpers + "\nthis.backup={readEmergencyBackup,storeEmergencyBackup,promoteEmergencyBackupAfterCreate,clearEmergencyBackupIfSynced,showEmergencyRecovery,restoreEmergencyBackup,hideEmergencyRecovery};", ctx);
  const key = (id = userId) => "ikf-writers-unsaved-v1:" + id;
  return {
    ...ctx.backup, records, nodes, events, storage, key, state,
    setFields: (v) => { fields = v; },
    getFields: () => fields,
    setResponse: (v) => { response = v; },
  };
}

test("backup keeps text and create idempotency key but no credentials or file blobs", () => {
  const h = harness();
  assert.equal(h.storeEmergencyBackup(), true);
  const stored = JSON.parse(h.records.get(h.key()));
  assert.equal(stored.fields.title, "Название");
  assert.equal(stored.fields.description, "Текст, который нельзя терять.");
  assert.equal(stored.createKey, "create-original");
  assert.equal(stored.id, null);
  assert.equal(stored.savedAt > 0, true);
  assert.equal(h.records.size, 1);
  assert.doesNotMatch(JSON.stringify(stored), /init_data|telegram_file_id|photoBytes|BOT_TOKEN/);
});

test("account-specific backups cannot be displayed to another Telegram account", () => {
  const first = harness(77), second = harness(88);
  first.storeEmergencyBackup();
  second.storage.setItem(first.key(), first.records.get(first.key()));
  assert.equal(second.readEmergencyBackup(), null);
  assert.equal(second.showEmergencyRecovery(), false);
  assert.equal(second.nodes.size, 0);
});

test("confirmed save removes only matching backup, never newer or unrelated edits", () => {
  const h = harness();
  h.state.current = { id: "11111111-1111-4111-8111-111111111111", status: "DRAFT", version: 1, revision: { id: "22222222-2222-4222-8222-222222222222" } };
  h.storeEmergencyBackup();
  h.state.editSequence = 2;
  h.state.savedSequence = 1;
  h.clearEmergencyBackupIfSynced();
  assert.equal(h.records.size, 1);
  h.state.savedSequence = 2;
  h.setFields({ ...h.getFields(), description: "Более поздняя правка" });
  h.clearEmergencyBackupIfSynced();
  assert.equal(h.records.size, 1);
  h.setFields({ ...h.getFields(), description: "Текст, который нельзя терять." });
  h.clearEmergencyBackupIfSynced();
  assert.equal(h.records.size, 0);
});

test("new unsaved draft is restored with the ORIGINAL create key", async () => {
  const h = harness();
  h.storeEmergencyBackup();
  const restored = await h.restoreEmergencyBackup();
  assert.equal(restored, true);
  assert.equal(h.state.createKey, "create-original");
  assert.ok(h.events.includes("newDraft"));
  assert.ok(h.events.includes("markChanged"));
  const fill = h.events.find((x) => Array.isArray(x) && x[0] === "fillForm");
  assert.equal(fill[1].revision.description, "Текст, который нельзя терять.");
});

test("server draft restores if ID, revision and version all match", async () => {
  const h = harness();
  const id = "11111111-1111-4111-8111-111111111111";
  const revisionId = "22222222-2222-4222-8222-222222222222";
  h.state.current = { id, status: "DRAFT", version: 5, revision: { id: revisionId } };
  h.storeEmergencyBackup();
  h.setResponse({ submission: {
    id, status: "DRAFT", version: 5,
    revision: { id: revisionId, description: "Старая сохраненная версия" },
  } });
  assert.equal(await h.restoreEmergencyBackup(), true);
  assert.ok(h.events.includes("markChanged"));
  assert.equal(h.records.size, 1, "backup remains until PostgreSQL confirms save");
});

test("stale backup does not overwrite newer server version and remains copyable", async () => {
  const h = harness();
  const id = "11111111-1111-4111-8111-111111111111";
  const revisionId = "22222222-2222-4222-8222-222222222222";
  h.state.current = { id, status: "DRAFT", version: 5, revision: { id: revisionId } };
  h.storeEmergencyBackup();
  h.setResponse({ submission: {
    id, status: "DRAFT", version: 6,
    revision: { id: revisionId, description: "Изменено на другом устройстве" },
  } });
  assert.equal(await h.restoreEmergencyBackup(), false);
  assert.ok(!h.events.includes("markChanged"));
  assert.ok(!h.events.some((event) => Array.isArray(event) && event[0] === "openSubmission"));
  assert.match(h.nodes.get("emergencyRecoveryNotice").textContent, /другого устройства/);
  assert.match(h.nodes.get("emergencyRecoveryText").value, /Текст, который нельзя терять/);
  assert.equal(h.records.size, 1);
});

test("already-saved text needs no replay after lost response", async () => {
  const h = harness();
  const id = "11111111-1111-4111-8111-111111111111";
  const revisionId = "22222222-2222-4222-8222-222222222222";
  h.state.current = { id, status: "DRAFT", version: 5, revision: { id: revisionId } };
  h.storeEmergencyBackup();
  h.setResponse({ submission: {
    id, status: "DRAFT", version: 6,
    revision: { id: revisionId, description: "Текст, который нельзя терять." },
  } });
  assert.equal(await h.restoreEmergencyBackup(), true);
  assert.ok(!h.events.includes("markChanged"));
  assert.equal(h.records.size, 0);
});

test("expired backup is ignored and deleted", () => {
  const h = harness();
  h.storeEmergencyBackup();
  const record = JSON.parse(h.records.get(h.key()));
  record.savedAt = Date.now() - 49 * 60 * 60 * 1000;
  h.records.set(h.key(), JSON.stringify(record));
  assert.equal(h.readEmergencyBackup(), null);
  assert.equal(h.records.size, 0);
});

test("localStorage failures never prevent editing or throw", () => {
  const h = harness();
  h.storage.setItem = () => { throw Error("quota exceeded"); };
  assert.equal(h.storeEmergencyBackup(), false);
  assert.equal(h.readEmergencyBackup(), null);
});

test("server create response upgrades an unsaved backup without replacing later edits", () => {
  const h = harness();
  h.storeEmergencyBackup();
  const later = { ...h.getFields(), description: "Новый текст во время POST." };
  h.setFields(later);
  h.storeEmergencyBackup();
  const id = "11111111-1111-4111-8111-111111111111";
  const revisionId = "22222222-2222-4222-8222-222222222222";
  h.promoteEmergencyBackupAfterCreate("create-original", {
    id, version: 1, revision: { id: revisionId },
  });
  const backup = h.readEmergencyBackup();
  assert.equal(backup.id, id);
  assert.equal(backup.revisionId, revisionId);
  assert.equal(backup.expectedVersion, 1);
  assert.equal(backup.createKey, null);
  assert.equal(backup.fields.description, later.description);
});

test("acknowledging a different draft cannot delete an unnamed recovery backup", () => {
  const h = harness();
  h.storeEmergencyBackup();
  h.state.current = {id:"11111111-1111-4111-8111-111111111111",status:"SUBMITTED"};
  h.clearEmergencyBackupIfSynced(true);
  assert.equal(h.records.size, 1);
});

test("recovery does not overwrite another work currently open in the editor", async () => {
  const h = harness();
  h.storeEmergencyBackup();
  const visibleEditor = {
    classList: { contains: (className) => className === "hidden" ? false : true },
  };
  // Source helpers accept a normal views object from the Mini App.
  h.nodes.clear();
  // This assertion also guards that the recovery intent stays explicitly opt-in
  // rather than a silent replacement of an active edit.
  const context = vm.createContext({
    views: { editor: visibleEditor },
    setBanner: (message) => h.events.push(["banner", message]),
    readEmergencyBackup: () => h.readEmergencyBackup(),
    restoreEmergencyBackup: () => h.restoreEmergencyBackup(),
  });
  // Exercise the early guard in the original source, not a mock handler.
  const restore = source.slice(
    source.indexOf("  async function restoreEmergencyBackup() {"),
    source.indexOf("  function markChanged() {")
  );
  vm.runInContext(restore + "\nthis.tryRestore=restoreEmergencyBackup;", context);
  assert.equal(await context.tryRestore(), false);
  assert.ok(h.events.some((event) =>
    Array.isArray(event) && /вернись к списку работ/.test(event[1])
  ));
});
