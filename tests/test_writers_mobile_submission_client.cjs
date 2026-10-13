"use strict";

const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const vm = require("node:vm");

const source = readFileSync(
  resolve(__dirname, "../writers_submission/static/app.js"), "utf8"
);

function section(startText, endText) {
  const start = source.indexOf(startText);
  const end = source.indexOf(endText, start);
  assert.ok(start >= 0 && end > start, "Expected helper in Mini App");
  return source.slice(start, end);
}

test("Mini App counts complete sentences regardless of trailing tags and emoji", () => {
  const js = section(
    "  function countDescriptionSentences(",
    "  function countWords("
  );
  const context = vm.createContext({});
  vm.runInContext(js + ";this.counter=countDescriptionSentences;", context);
  for (const input of [
    "Первое предложение. Второе предложение! Третье предложение? #жанр",
    "История началась. Герои встретились. Все закончилось. Последнее не завершено",
    "Одна глава. Другая глава. Третья глава. ❤️ #фанфик",
  ]) {
    assert.equal(context.counter(input), 3, input);
  }
  assert.equal(context.counter("Одна глава. Вторая глава. Конец без точки #тег"), 2);
  assert.equal(context.counter("Начало. Продолжение. #фанфик"), 2);
});

test("Selected mobile photo is uploaded to saved draft immediately", async () => {
  const js = section(
    "  async function persistSelectedFiles(",
    "  async function uploadQueuedFiles("
  );
  const actions = [];
  const context = vm.createContext({
    queueFilesLocally: async () => { actions.push("selected"); return true; },
    updateUploadProgress: () => actions.push("progress"),
    autosave: async () => { actions.push("saved"); return true; },
    uploadQueuedFiles: async () => { actions.push("uploaded"); return true; },
  });
  vm.runInContext(js + ";this.persist=persistSelectedFiles;", context);
  await context.persist(["photo.jpg"], "image");
  assert.deepEqual(actions, ["selected", "progress", "saved", "uploaded"]);
});

test("Failed draft save prevents file upload while retaining selection", async () => {
  const js = section(
    "  async function persistSelectedFiles(",
    "  async function uploadQueuedFiles("
  );
  const actions = [];
  const context = vm.createContext({
    queueFilesLocally: async () => { actions.push("selected"); return true; },
    updateUploadProgress: (source, label, percent, phase) => {
      actions.push(phase || "waiting");
    },
    autosave: async () => { actions.push("save-failed"); return false; },
    uploadQueuedFiles: async () => { actions.push("ERR-UPLOADED"); return true; },
  });
  vm.runInContext(js + ";this.persist=persistSelectedFiles;", context);
  await context.persist(["photo.jpg"], "image");
  assert.deepEqual(actions, ["selected", "processing", "save-failed", "error"]);
});
