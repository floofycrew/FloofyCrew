// FloofyCrew SPA host — the reporter behind `window.floofy.report()` (Requirement 4.5).
//
// Evaluates every surface fingerprint (dom + bundle) and every patch
// fingerprint against the live document and bundle and says which no longer
// match. `floofy verify --spa` drives it headlessly (Playwright) and the Forge
// runs it on every new host version; the `spaFingerprints` block is the shape
// the compatibility matrix records (Requirement 9.1: matched/total, misses).
"use strict";

import { createSurfaces } from "./surfaces.mjs";

/**
 * Fold surface rows and patch rows into the report shape.
 * @param {{name: string, kind: string, status: string, reason: string, chunk?: string|null}[]} surfaceRows
 * @param {{matched: any[], missed: any[]}} patchReport
 * @param {{hostVersion?: string|null, route?: string|null, chunks?: Record<string, string>, extra?: object}} [meta]
 */
export function foldReport(surfaceRows, patchReport, meta = {}) {
  const matched = [];
  const missed = [];
  const notApplicable = [];
  for (const row of surfaceRows) {
    const key = `${row.name}#${row.kind}`;
    if (row.status === "matched") matched.push(key);
    else if (row.status === "not-applicable") notApplicable.push({ name: key, kind: row.kind, reason: row.reason });
    else missed.push({ name: key, kind: row.kind, reason: row.reason, ...(row.chunk ? { chunk: row.chunk } : {}) });
  }
  for (const entry of patchReport?.matched ?? []) matched.push(typeof entry === "string" ? entry : `patch:${entry.name}`);
  for (const entry of patchReport?.missed ?? []) {
    missed.push(typeof entry === "string" ? { name: entry, kind: "patch", reason: "fingerprint miss" } : { kind: "patch", ...entry });
  }
  return {
    hostVersion: meta.hostVersion ?? null,
    route: meta.route ?? null,
    matched,
    missed,
    notApplicable,
    total: matched.length + missed.length,
    spaFingerprints: { matched: matched.length, total: matched.length + missed.length, missed: missed.map((m) => m.name) },
    chunks: meta.chunks ?? {},
    generatedAt: new Date().toISOString(),
    ...(meta.extra ?? {}),
  };
}

/**
 * @param {object} options
 * @param {ReturnType<import("./surfaces.mjs").createSurfaces>} options.surfaces
 * @param {ReturnType<import("./patches.mjs").createPatches>} options.patches
 * @param {() => any} options.state the latest Loader state
 * @param {Document|null} [options.document]
 * @param {any} [options.window]
 * @param {typeof fetch} [options.fetch]
 */
export function createReporter({ surfaces, patches, state, document: doc = null, window: win = null, fetch: fetchImpl }) {
  return Object.freeze({
    /**
     * Evaluate everything. `options.extraSurfaces` adds ad-hoc surface definitions for this run
     * (the Playwright test uses it to prove a broken fingerprint is reported).
     */
    async report(options = {}) {
      await surfaces.load?.();
      const rows = await surfaces.check();
      if (Array.isArray(options.extraSurfaces) && options.extraSurfaces.length) {
        const adHoc = createSurfaces({ document: doc, window: win, bus: { on() {}, emit() {} }, definitions: options.extraSurfaces, fetch: fetchImpl });
        rows.push(...(await adHoc.check()));
      }
      const patchReport = await patches.report();
      const current = state();
      const chunks = Object.fromEntries(surfaces.chunks?.() ?? []);
      return foldReport(rows, patchReport, {
        hostVersion: current?.host?.version ?? null,
        route: win?.location?.pathname ?? null,
        chunks,
        extra: { surfaces: surfaces.names(), loaderVersion: current?.loaderVersion ?? null },
      });
    },
  });
}
