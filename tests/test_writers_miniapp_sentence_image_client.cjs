"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const app = fs.readFileSync(
  path.join(__dirname, "..", "writers_submission", "static", "app.js"), "utf8"
);
const html = fs.readFileSync(
  path.join(__dirname, "..", "writers_submission", "static", "index.html"), "utf8"
);

const start = app.indexOf("  function countCompletedSentences(");
const end = app.indexOf("  function refreshCounters(", start);
assert.ok(start > 0 && end > start, "sentence counter implementation must exist");

const sandbox = {};
vm.runInNewContext(
  app.slice(start, end) + "\nthis.countSentences = countCompletedSentences;",
  sandbox, { filename: "app.js" }
);

test("trailing Telegram hashtags and unfinished fourth thought do not erase valid sentences", () => {
  const valid = [
    "Начало истории. Сюжет продолжается. История закончилась. #ФФ #романтика",
    "Это первый эпизод! И вот поворот? Развязка готова. Пока хочется добавить",
    "Сюжет. Герои. Концовка. ✨💖 #слэш",
  ];
  for (const text of valid) {
    assert.equal(sandbox.countSentences(text), 3);
  }
  assert.equal(sandbox.countSentences("Начало. Продолжение. #канон"), 2);
});

test("the visible save action saves files, and upload waits for explicit save or submit", () => {
  assert.match(app, /async function saveDraftAndFiles\(\)/);
  assert.match(app, /const saved = await autosave\(\{ immediate: true \}\)/);
  assert.match(app, /if \(state\.pendingFiles\.length\) \{\s*const uploaded = await uploadQueuedFiles\(\)/);
  assert.match(app, /saveButton"\)\.addEventListener\("click", \(\) => \{ void saveDraftAndFiles\(\); \}\)/);
  assert.match(html, /id="saveButton"[^>]+>Сохранить черновик и файлы</);
  assert.match(html, /id="imageInput"[^>]+type="file"/);
});

test("the Mini App deploys a new cache-busting script to Android and iPhone clients", () => {
  assert.match(html, /app\.js\?v=photo-sentences-contact-v4/);
});
