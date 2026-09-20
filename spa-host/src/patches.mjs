// FloofyCrew SPA host — `window.floofy.patches` (Requirement 4.4, design spike 1.4).
//
// Inspection only. Module patches are applied at Patcher time (a patched copy
// of the chunk served from `/apps/floofycrew/ui/patched/` and swapped in
// through the host's inline import map), because native ES modules have no
// runtime module registry to rewrite — task 5.3 fills this in.
"use strict";

/**
 * @param {object} options
 * @param {Document|null} options.document
 * @param {typeof fetch} options.fetch
 * @param {any} options.state the Loader state at start
 */
export function createPatches({ document, fetch: fetchImpl, state }) {
  return Object.freeze({
    /** The import-map remaps currently in the document (task 5.3). */
    list: () => [],
    /** Whether the patched copy named `name` is what the document maps to (task 5.3). */
    isApplied: () => false,
    /** Evaluate every known patch fingerprint against the live bundle (task 5.3). */
    report: async () => ({ matched: [], missed: [] }),
    document,
    fetch: fetchImpl,
    state,
  });
}
