import assert from "node:assert/strict";
import { test } from "node:test";

import { PATCHED_INDEX_URL, PATCHED_PREFIX, createPatches, evaluateOp, readImportMap } from "../src/patches.mjs";

const CHUNK = "TunnelQrCard-C7TY_tEZ.js";
const MARKER = `/* floofy-patched: qr-mod#0 on ${CHUNK} (host 0.7.0.5) */`;

function fakeDocument(imports) {
  return { querySelector: (selector) => (selector === 'script[type="importmap"]' && imports ? { textContent: JSON.stringify({ imports }) } : null) };
}

function fakeFetch(files) {
  const calls = [];
  return {
    calls,
    fetch: async (url) => {
      calls.push(url);
      const body = files[url];
      return { ok: body !== undefined, json: async () => JSON.parse(body), text: async () => body };
    },
  };
}

const INDEX = JSON.stringify({
  schema: 1,
  patches: [{ mod: "qr-mod", part: "0", label: "qr-mod#0", chunk: CHUNK, original: `/assets/${CHUNK}`, patched: `${PATCHED_PREFIX}${CHUNK}`, hostVersion: "0.7.0.5", marker: MARKER, sidecar: `${PATCHED_PREFIX}${CHUNK}.floofy.json` }],
});
const SIDECAR = JSON.stringify({
  find: "Phone access code",
  ops: [
    { index: 0, op: "replace", fingerprint: '"aria-label":`Phone access code`', regex: true, marker: "Phone access code (floofy)" },
    { index: 1, op: "insert-after", fingerprint: "does-not-exist", regex: false, marker: null },
  ],
});
const ORIGINAL = 'import{o as e}from"./rolldown-runtime-C0FnF6B9.js";x("aria-label":`Phone access code`)';
const PATCHED = `${MARKER}\nimport{o as e}from"/assets/rolldown-runtime-C0FnF6B9.js";x("aria-label":\`Phone access code (floofy)\`)`;

test("readImportMap tolerates a missing or broken map", () => {
  assert.deepEqual(readImportMap(fakeDocument(null)), {});
  assert.deepEqual(readImportMap({ querySelector: () => ({ textContent: "{not json" }) }), {});
  assert.deepEqual(readImportMap(fakeDocument({ react: "/vendor/react.mjs" })), { react: "/vendor/react.mjs" });
});

test("evaluateOp mirrors the Patcher: exactly once, marker means applied, bad regex is a miss with a reason", () => {
  assert.equal(evaluateOp({ op: "replace", fingerprint: "a", regex: false }, "xa").status, "matched");
  assert.equal(evaluateOp({ op: "replace", fingerprint: "a", regex: false }, "aa").reason, "ambiguous (2)");
  assert.equal(evaluateOp({ op: "replace", fingerprint: "z", regex: false }, "aa").reason, "fingerprint miss");
  assert.equal(evaluateOp({ op: "replace", fingerprint: "a+", regex: true }, "aa").status, "matched");
  assert.match(evaluateOp({ op: "replace", fingerprint: "(?P<x>a)", regex: true }, "aa").reason, /not evaluable/);
  assert.equal(evaluateOp({ op: "replace", fingerprint: "q", marker: "done" }, "done").reason, "already applied (marker present)");
  assert.equal(evaluateOp({ op: "append-head" }, "<head></head>").status, "matched");
  assert.equal(evaluateOp({ op: "append-head" }, "nothing").status, "missed");
});

test("list() joins the recorded index with the live import map; isApplied checks the served marker", async () => {
  const files = {
    [PATCHED_INDEX_URL]: INDEX,
    [`${PATCHED_PREFIX}${CHUNK}`]: PATCHED,
    [`${PATCHED_PREFIX}${CHUNK}.floofy.json`]: SIDECAR,
    [`/assets/${CHUNK}`]: ORIGINAL,
  };
  const { fetch } = fakeFetch(files);
  const patches = createPatches({ document: fakeDocument({ [`/assets/${CHUNK}`]: `${PATCHED_PREFIX}${CHUNK}`, "/assets/Other-AAAAAAAA.js": `${PATCHED_PREFIX}Other-AAAAAAAA.js` }), fetch });
  const rows = await patches.list();
  assert.equal(rows.length, 2);
  assert.equal(rows[0].label, "qr-mod#0");
  assert.equal(rows[0].mapped, true);
  assert.equal(rows[1].recorded, false, "a map entry without a record is still listed");
  assert.equal(await patches.isApplied(CHUNK), true);
  assert.equal(await patches.isApplied("qr-mod"), true);
  assert.equal(await patches.isApplied("nope"), false);

  const report = await patches.report();
  assert.deepEqual(report.matched, ["patch:qr-mod#0#importMap", "patch:qr-mod#0#copy", "patch:qr-mod#0#ops/0", "patch:Other-AAAAAAAA.js#importMap"]);
  assert.deepEqual(report.missed, [{ name: "patch:qr-mod#0#ops/1", reason: "fingerprint miss", chunk: CHUNK }]);
});

test("a remap the document dropped and a copy without the marker are misses", async () => {
  const files = {
    [PATCHED_INDEX_URL]: INDEX,
    [`${PATCHED_PREFIX}${CHUNK}`]: "// someone replaced the copy\n",
    [`${PATCHED_PREFIX}${CHUNK}.floofy.json`]: SIDECAR,
    [`/assets/${CHUNK}`]: ORIGINAL.replace("Phone access code", "Phone access token"),
  };
  const { fetch } = fakeFetch(files);
  const patches = createPatches({ document: fakeDocument({}), fetch });
  assert.equal(await patches.isApplied(CHUNK), false);
  const report = await patches.report();
  assert.deepEqual(report.matched, []);
  assert.deepEqual(report.missed.map((m) => m.name), ["patch:qr-mod#0#importMap", "patch:qr-mod#0#copy", "patch:qr-mod#0#find"]);
});

test("without any record the API is empty and quiet", async () => {
  const { fetch } = fakeFetch({});
  const patches = createPatches({ document: fakeDocument(null), fetch });
  assert.deepEqual(await patches.list(), []);
  assert.deepEqual(await patches.report(), { matched: [], missed: [] });
});
