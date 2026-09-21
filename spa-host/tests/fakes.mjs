// Test doubles for the SPA host: a fake Loader API behind `fetch`, a fake
// dynamic importer, and a minimal window/document. Plain node, no DOM library.
"use strict";

/** A Loader `/state` document with the given mods (all active unless told otherwise). */
export function makeState(mods, { order } = {}) {
  const entries = {};
  for (const mod of mods) {
    entries[mod.id] = {
      id: mod.id,
      version: mod.version ?? "1.0.0",
      name: mod.name ?? mod.id,
      active: mod.active ?? true,
      enabled: mod.enabled ?? true,
      reason: mod.reason ?? null,
      parts: (mod.parts ?? []).map((part, index) => ({ index, status: "active", side: "spa", ...part })),
      governance: mod.governance ?? [],
      exports: mod.exports ?? {},
    };
  }
  return {
    loader: "ok",
    host: { version: "0.7.0.5", edition: "internal", channel: "beta", build_version: "0.7.0.5", base_version: "0.7.0", profile: "enterprise" },
    api_version: "1.0.0",
    loaderVersion: "0.0.0",
    unofficial: true,
    mods: entries,
    order: order ?? mods.map((m) => m.id),
    active: mods.filter((m) => m.active ?? true).map((m) => m.id),
  };
}

/**
 * A fake `fetch` for `/api/apps/floofycrew/*`.
 * `plan.stateFailures` makes the first N `/state` calls answer 401 (the cold token path);
 * every POST to `/mods/<id>/fault` is recorded in `plan.faults`.
 */
export function makeFetch(state, plan = {}) {
  const calls = [];
  const faults = [];
  let stateFailures = plan.stateFailures ?? 0;
  const fetchImpl = async (url, init = {}) => {
    calls.push({ url, method: init.method ?? "GET" });
    const path = String(url);
    const json = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
    const faultMatch = path.match(/\/api\/apps\/floofycrew\/mods\/([^/]+)\/fault$/);
    if (faultMatch && init.method === "POST") {
      faults.push({ mod: decodeURIComponent(faultMatch[1]), ...JSON.parse(init.body) });
      return json(200, { ok: true, faulted: true });
    }
    if (path.endsWith("/api/apps/floofycrew/health")) return json(200, { ok: true, loader: "ok" });
    if (path.endsWith("/api/apps/floofycrew/state")) {
      if (stateFailures > 0) {
        stateFailures -= 1;
        return json(401, { error: "unauthorized" });
      }
      return json(200, state);
    }
    return json(404, { error: "not found" });
  };
  return { fetch: fetchImpl, calls, faults };
}

/** A fake dynamic importer: `modules[url]` is a module namespace object or a function producing one (may throw). */
export function makeImporter(modules) {
  const imported = [];
  return {
    imported,
    importer: async (url) => {
      imported.push(url);
      if (!(url in modules)) throw new TypeError(`Failed to fetch dynamically imported module: ${url}`);
      const entry = modules[url];
      return typeof entry === "function" ? entry() : entry;
    },
  };
}

/** A minimal window: `addEventListener` for the boundary, `history`/`location` for the route watcher. */
export function makeWindow() {
  const listeners = {};
  const win = {
    listeners,
    addEventListener(name, fn) {
      (listeners[name] ??= []).push(fn);
    },
    removeEventListener(name, fn) {
      listeners[name] = (listeners[name] ?? []).filter((f) => f !== fn);
    },
    dispatch(name, event) {
      for (const fn of listeners[name] ?? []) fn(event);
    },
    location: { pathname: "/", search: "", hash: "" },
    history: {
      pushState(_state, _title, url) {
        win.location.pathname = String(url);
      },
      replaceState(_state, _title, url) {
        win.location.pathname = String(url);
      },
    },
  };
  return win;
}

/** A console that records instead of printing. */
export function makeConsole() {
  const lines = [];
  const record = (level) => (...args) => lines.push({ level, text: args.map((a) => (a instanceof Error ? a.message : String(a))).join(" ") });
  return { lines, debug: record("debug"), info: record("info"), warn: record("warn"), error: record("error") };
}

export const immediate = (fn) => {
  fn();
  return 0;
};
