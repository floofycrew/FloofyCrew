// FloofyCrew SPA host — `window.floofy.surfaces` (Requirement 4.4).
//
// Stable names for host UI surfaces, located by CONTENT fingerprints (task
// 5.2 ships the registry and the resolution logic; this module is the seam
// `host.mjs` wires). Never by chunk hash or line number.
"use strict";

/**
 * Create the surface registry.
 * @param {object} options
 * @param {Document|null} options.document
 * @param {any} options.window
 * @param {import("./events.mjs").createBus extends (...a: any) => infer R ? R : never} options.bus
 * @param {any[]} [options.definitions] surface definitions (default: the shipped registry, task 5.2)
 */
export function createSurfaces({ document, window: win, bus, definitions = [] }) {
  const defs = Array.isArray(definitions) ? definitions : [];
  return Object.freeze({
    /** Surface names in registry order. */
    names: () => defs.map((d) => d.name),
    /** The registry entries (frozen copies). */
    definitions: () => defs.map((d) => Object.freeze({ ...d })),
    /** Resolve one surface to its live node (task 5.2). */
    get: () => null,
    /** Re-resolve every surface and emit `surfaces.changed` when something moved (task 5.2). */
    refresh: () => ({ changed: false }),
    /** Start observing the document for route changes (task 5.2). */
    start: () => undefined,
    document,
    window: win,
    bus,
  });
}
