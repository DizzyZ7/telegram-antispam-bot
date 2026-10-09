"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const script = fs.readFileSync(
  path.join(__dirname, "..", "writers_submission", "static", "app.js"),
  "utf8"
);
const start = script.indexOf("  const MAX_PHOTO_BYTES =");
const end = script.indexOf("  async function queueFilesLocally(", start);
assert.ok(start >= 0 && end > start, "photo helper source must be present");

const urls = [];
const sandbox = {
  File,
  Blob,
  URL: {
    createObjectURL: () => "blob:mock-photo",
    revokeObjectURL: (url) => urls.push(url),
  },
  Image: class FakeImage {
    naturalWidth = 4000;
    naturalHeight = 2000;
    set src(_url) {
      queueMicrotask(() => this.onload());
    }
  },
  document: {
    createElement(tag) {
      assert.equal(tag, "canvas");
      return {
        width: 0,
        height: 0,
        getContext() {
          return {
            fillRect() {},
            drawImage() {},
            set fillStyle(_color) {},
          };
        },
        toBlob(resolve, type) {
          assert.equal(type, "image/jpeg");
          resolve(new Blob([Buffer.from("fake-jpeg-data")], { type }));
        },
      };
    },
  },
};
vm.runInNewContext(
  script.slice(start, end) +
    "\nthis.photoHelpers = {isPhotoFile, prepareUploadFile};",
  sandbox,
  { filename: "app.js" }
);

const { isPhotoFile, prepareUploadFile } = sandbox.photoHelpers;

test("iOS picker JPEG with empty MIME becomes image/jpeg without altering bytes", async () => {
  const original = new File([Buffer.from("jpeg")], "IMG_0001.JPG");
  assert.equal(isPhotoFile(original), true);
  const result = await prepareUploadFile(original);
  assert.equal(result.type, "image/jpeg");
  assert.equal(result.name, "IMG_0001.JPG");
  assert.equal(await result.text(), "jpeg");
});

test("iPhone HEIC photo is transformed into JPEG that server accepts by filename/MIME", async () => {
  const original = new File([Buffer.from("heic")], "IMG_0001.HEIC", { type: "image/heic" });
  const result = await prepareUploadFile(original);
  assert.equal(result.name, "IMG_0001.jpg");
  assert.equal(result.type, "image/jpeg");
  assert.ok(result.size > 0);
  assert.ok(urls.includes("blob:mock-photo"));
});

test("Android WebP photo is transformed into JPEG", async () => {
  const original = new File([Buffer.from("webp")], "avatar.webp", { type: "image/webp" });
  const result = await prepareUploadFile(original);
  assert.equal(result.name, "avatar.jpg");
  assert.equal(result.type, "image/jpeg");
});

test("rejects unrelated extensions and oversize files", async () => {
  await assert.rejects(
    prepareUploadFile(new File(["x"], "script.exe")),
    /Доступны/
  );
  await assert.rejects(
    prepareUploadFile(new File([Buffer.alloc(20 * 1024 * 1024 + 1)], "huge.heic", { type: "image/heic" })),
    /20 МБ/
  );
});
