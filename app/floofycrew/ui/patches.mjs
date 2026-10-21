// FloofyCrew SPA host — `window.floofy.patches` (Requirement 4.4, design spike 1.4).
//
// INSPECTION ONLY. Module patches are applied at Patcher time: a hashed chunk
// is never rewritten in the browser because native ES modules have no runtime
// module registry to hook (unlike Vencord's webpack), and Blob/data module
// imports are blocked by the served CSP. Instead the Patcher writes a patched
// copy under `/apps/floofycrew/ui/patched/<chunk>` and adds one import-map key
// (`"/assets/<chunk>": "/apps/floofycrew/ui/patched/<chunk>"`) to `index.html`,
// which swaps the module for every importer. What this module does:
//
// * `list()`   — the remaps the Patcher recorded (`ui/patched/index.json`)
//                joined with the document's live import map;
// * `isApplied(name)` — the import map maps the chunk to the patched copy AND
//                the served copy carries the Patcher's marker comment;
// * `report()` — every recorded patch's fingerprints re-evaluated against the
//                live ORIGINAL chunk text (`/assets/<chunk>`, same origin), so
//                the reporter can say which patch would stop applying on a new
//                host build, plus the import-map key and the served copy.
//
// Regex fingerprints are authored for the Python Patcher; one that the browser
// cannot compile is reported as a miss with that reason (keep them portable).
"use strict";

export const PATCHED_PREFIX = "/apps/floofycrew/ui/patched/";
export const PATCHED_INDEX_URL = `${PATCHED_PREFIX}index.json`;

/** Parse the document's inline import map; `{}` when absent or invalid. */
export function readImportMap(doc) {
  const node = doc?.querySelector?.('script[type="importmap"]');
  if (!node) return {};
  try {
    const parsed = JSON.parse(node.textContent ?? "{}");
    return parsed && typeof parsed.imports === "object" ? parsed.imports : {};
  } catch {
    return {};
  }
}

/** Count the exactly-once matches of one recorded op against text (mirrors the Python Patcher). */
export function evaluateOp(op, text) {
  if (op.marker && text.includes(op.marker)) return { status: "matched", reason: "already applied (marker present)" };
  if (op.op === "append-head") {
    const count = text.split("</head>").length - 1;
    return count === 1 ? { status: "matched", reason: "1 match" } : { status: "missed", reason: count === 0 ? "no </head>" : `ambiguous (${count})` };
  }
  if (typeof op.fingerprint !== "string" || !op.fingerprint) return { status: "missed", reason: "no fingerprint" };
  let count = 0;
  if (op.regex) {
    let pattern;
    try {
      pattern = new RegExp(op.fingerprint, "g");
    } catch (error) {
      return { status: "missed", reason: `regex not evaluable in the browser: ${error.message}` };
    }
    for (const _ of text.matchAll(pattern)) count += 1;
  } else {
    let index = text.indexOf(op.fingerprint);
    while (index >= 0) {
      count += 1;
      index = text.indexOf(op.fingerprint, index + op.fingerprint.length);
    }
  }
  if (count === 1) return { status: "matched", reason: "1 match" };
  return { status: "missed", reason: count === 0 ? "fingerprint miss" : `ambiguous (${count})` };
}

/**
 * @param {object} options
 * @param {Document|null} options.document
 * @param {typeof fetch} options.fetch
 * @param {any} [options.state] the Loader state at start (informational)
 */
export function createPatches({ document: doc, fetch: fetchImpl, state }) {
  /** @type {Map<string, Promise<any>>} */
  const jsonCache = new Map();
  /** @type {Map<string, Promise<string|null>>} */
  const textCache = new Map();

  function getJson(url) {
    if (!jsonCache.has(url)) {
      jsonCache.set(
        url,
        fetchImpl(url, { credentials: "same-origin", cache: "no-cache" })
          .then((response) => (response.ok ? response.json() : null))
          .catch(() => null),
      );
    }
    return jsonCache.get(url);
  }

  function getText(url) {
    if (!textCache.has(url)) {
      textCache.set(
        url,
        fetchImpl(url, { credentials: "same-origin" })
          .then((response) => (response.ok ? response.text() : null))
          .catch(() => null),
      );
    }
    return textCache.get(url);
  }

  /** Recorded remaps joined with the live import map. */
  async function list() {
    const recorded = (await getJson(PATCHED_INDEX_URL))?.patches ?? [];
    const imports = readImportMap(doc);
    const rows = recorded.map((entry) => ({
      ...entry,
      mapped: imports[entry.original] === entry.patched,
    }));
    for (const [key, value] of Object.entries(imports)) {
      if (typeof value === "string" && value.startsWith(PATCHED_PREFIX) && !rows.some((r) => r.original === key)) {
        rows.push({ chunk: key.split("/").pop(), original: key, patched: value, mod: null, part: null, label: null, mapped: true, recorded: false });
      }
    }
    return rows;
  }

  /** `name` is a chunk file name, a mod id or a `mod#part` label. */
  async function isApplied(name) {
    const rows = (await list()).filter((r) => r.chunk === name || r.mod === name || r.label === name);
    if (!rows.length) return false;
    for (const row of rows) {
      if (!row.mapped) return false;
      const served = await getText(row.patched);
      if (!served || (row.marker && !served.includes(row.marker))) return false;
    }
    return true;
  }

  /** Re-evaluate every recorded patch: fingerprints on the live original, the import-map key, the served copy. */
  async function report() {
    const matched = [];
    const missed = [];
    for (const row of await list()) {
      const label = row.label ?? row.chunk;
      if (row.recorded === false) {
        matched.push(`patch:${label}#importMap`);
        continue;
      }
      if (row.mapped) matched.push(`patch:${label}#importMap`);
      else missed.push({ name: `patch:${label}#importMap`, reason: `import map does not map ${row.original} to ${row.patched}` });
      const served = await getText(row.patched);
      if (served && (!row.marker || served.includes(row.marker))) matched.push(`patch:${label}#copy`);
      else missed.push({ name: `patch:${label}#copy`, reason: served ? "served copy lacks the Patcher marker" : `patched copy ${row.patched} not served` });
      const sidecar = row.sidecar ? await getJson(row.sidecar) : null;
      const original = await getText(row.original);
      if (!sidecar || !original) {
        missed.push({ name: `patch:${label}#ops`, reason: !sidecar ? "sidecar unavailable" : `original chunk ${row.original} not served` });
        continue;
      }
      if (sidecar.find && !original.includes(sidecar.find)) {
        missed.push({ name: `patch:${label}#find`, reason: "find literal absent from the live chunk" });
        continue;
      }
      for (const op of sidecar.ops ?? []) {
        const verdict = evaluateOp(op, original);
        const name = `patch:${label}#ops/${op.index}`;
        if (verdict.status === "matched") matched.push(name);
        else missed.push({ name, reason: verdict.reason, chunk: row.chunk });
      }
    }
    return { matched, missed };
  }

  return Object.freeze({ list, isApplied, report, readImportMap: () => readImportMap(doc), state });
}
