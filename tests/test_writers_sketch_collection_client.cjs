"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "writers_submission", "static", "app.js"), "utf8");
function section(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, "missing section: " + start);
  return source.slice(a, b);
}
const fieldsCode = section("  function positiveNumberOrNull(", "  function updateDynamicSections()");
const sectionsCode = section("  function updateDynamicSections()", "  function clearImagePreview()");
const validationCode = section("  function validateReadyForm()", "  function refreshCounters()");

function setup(size = "Мини/драббл") {
  const choices = {
    workType: "Зарисовка",
    sketchOrigin: "Ориджинал",
    sketchSize: size,
    warningsPresent: "no",
    visualMode: "editorial",
  };
  const input = (value = "") => ({value, required: false, maxLength: 2048});
  const form = {
    title: input("Название"),
    direction: input("Джен"),
    rating: input("PG-13"),
    fandom: input(),
    description: input("Одна короткая история."),
    mainGenre1: input("Драма"), mainGenre2: input(), mainGenre3: input(),
    warnings: input(),
    sketchProfile: input("https://ficbook.net/authors/1234"),
    sketchCollection: input(""),
    sketchArticle: input("https://telegra.ph/One-Drabble-10-14"),
    body: input(), url: input(), extraLinks: input(),
    characters: input(), notes: input(),
    sizeWords: input(), pages: input(), parts: input(),
  };
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        classList: {hidden: true, toggle(_className, isHidden) {this.hidden = isHidden;}},
        textContent: "",
      });
    }
    return elements.get(id);
  }
  const state = {
    attachments: [], pendingFiles: [], coverFileId: null,
    paletteColors: ["#112233", "#223344", "#334455", "#445566"],
  };
  const ctx = vm.createContext({
    form, state, URL, $: element,
    selectedChoice: (name) => choices[name] || "",
    countWords: (str) => str.trim().split(/\s+/).length,
    countDescriptionSentences: () => 3,
    validDomain: (value, host) => {
      try {
        const u = new URL(value);
        return u.protocol === "https:" && !u.username && !u.password &&
          (u.hostname === host || u.hostname === "www." + host);
      } catch (_) { return false; }
    },
    renderIkfCover: () => {},
    clearImagePreview: () => {},
    renderAttachments: () => {},
    updateImageStatus: () => {},
  });
  vm.runInContext(fieldsCode + sectionsCode + validationCode +
    ";this.helpers={currentFields,updateDynamicSections,validateReadyForm};", ctx);
  return {form, state, choices, elements, ...ctx.helpers};
}

test("standalone Mini/drabble needs only Ficbook author profile + Telegraph article", () => {
  const h = setup();
  const values = h.validateReadyForm();
  assert.equal(values.details.sketch_size, "Мини/драббл");
  assert.equal(values.details.sketch_collection_url, "");
  assert.equal(values.external_url, h.form.sketchArticle.value);
  h.updateDynamicSections();
  assert.equal(h.elements.get("sketchCollectionUrlField").classList.hidden, true);
  assert.equal(h.form.sketchCollection.required, false);
  assert.equal(h.elements.get("sketchArticleLabel").textContent,
    "Статья с текстом зарисовки в Telegraph");
  assert.equal(h.elements.get("sketchSizeSection").classList.hidden, false);
});

test("collection requires Ficbook readfic link below profile and Telegraph one-of label", () => {
  const h = setup("Сборник драбблов");
  h.updateDynamicSections();
  assert.equal(h.elements.get("sketchCollectionUrlField").classList.hidden, false);
  assert.equal(h.form.sketchCollection.required, true);
  assert.equal(h.elements.get("sketchArticleLabel").textContent,
    "Статья с текстом зарисовки в Telegraph (один из драбблов сборника)");
  assert.throws(() => h.validateReadyForm(), /Для сборника нужна HTTPS-ссылка/);
  h.form.sketchCollection.value = "https://ficbook.net/authors/1234";
  assert.throws(() => h.validateReadyForm(), /Для сборника нужна HTTPS-ссылка/);
  h.form.sketchCollection.value = "https://ficbook.net/readfic/56789";
  const result = h.validateReadyForm();
  assert.equal(result.details.sketch_size, "Сборник драбблов");
  assert.equal(result.details.sketch_collection_url, "https://ficbook.net/readfic/56789");
  assert.equal(result.details.sketch_article_url, "https://telegra.ph/One-Drabble-10-14");
});

test("changing from collection to standalone prevents accidentally publishing stale collection URL", () => {
  const h = setup("Сборник драбблов");
  h.form.sketchCollection.value = "https://ficbook.net/readfic/123";
  assert.equal(h.validateReadyForm().details.sketch_collection_url, "https://ficbook.net/readfic/123");
  h.choices.sketchSize = "Мини/драббл";
  h.updateDynamicSections();
  assert.equal(h.form.sketchCollection.required, false);
  assert.equal(h.validateReadyForm().details.sketch_collection_url, "");
});
