// FloofyCrew SPA host — the reporter behind `window.floofy.report()` (Requirement 4.5).
//
// Evaluates every surface fingerprint and every patch fingerprint against the
// live document and bundle and says which no longer match. `floofy verify
// --spa` drives it headlessly and the Forge runs it per host version (task 5.2).
"use strict";

/**
 * @param {object} options
 * @param {ReturnType<import("./surfaces.mjs").createSurfaces>} options.surfaces
 * @param {ReturnType<import("./patches.mjs").createPatches>} options.patches
 * @param {() => any} options.state the latest Loader state
 */
export function createReporter({ surfaces, patches, state }) {
  return Object.freeze({
    async report() {
      const patchReport = await patches.report();
      const current = state();
      return {
        hostVersion: current?.host?.version ?? null,
        matched: [...patchReport.matched],
        missed: [...patchReport.missed],
        total: patchReport.matched.length + patchReport.missed.length,
        surfaces: surfaces.names(),
        generatedAt: new Date().toISOString(),
      };
    },
  });
}
