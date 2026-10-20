// FloofyCrew SPA host — the always-on dashboard runtime (Requirement 2.5, 4.1, 4.2).
//
// Served from the Loader app's `ui/` as `/apps/floofycrew/ui/host.mjs` and
// started by the one-line loader tag the Patcher injects into `index.html`
// (design "Spike outcomes" 1.3) or by the manager page's `index.mjs`. Both
// resolve to the same module URL, so this module runs once per document:
// `ensureStarted()` is idempotent and `window.floofy` is installed once.
//
// What it does: read the Loader state from `/api/apps/floofycrew/state` (same
// origin, dashboard cookie; on the cold `?token=` path the first request may
// race the token exchange, so it polls `/health` with backoff for ~10 s and
// then gives up quietly), build the frozen `window.floofy`, then `import()`
// every ACTIVE mod's runtime `spa` parts from `/api/apps/floofycrew/spa/<id>/`
// inside an error boundary — a failing module is reported to
// `POST /mods/<id>/fault` and skipped; the other mods and the host UI are
// unaffected. Same-origin only (Requirement 4.6): nothing is fetched or
// imported from another origin, ever.
//
// Everything that touches a global is injected through `env` so the module is
// unit-testable under plain `node --test` with fakes.
"use strict";

import { createBoundary, describeError } from "./boundary.mjs";
import { createBus, scoped } from "./events.mjs";
import { createModApi } from "./modapi.mjs";
import { createPatches } from "./patches.mjs";
import { createReporter } from "./reporter.mjs";
import { createSurfaces } from "./surfaces.mjs";

/** The SPA host's own version (independent of the Loader and the API version). */
export const SPA_HOST_VERSION = "1.1.0";
/** Where the Loader's routes are mounted (behind the dashboard cookie/token auth). */
export const API_BASE = "/api/apps/floofycrew";
/** Prefix of a mod's served `spa` files: `${SPA_PREFIX}<id>/<file>`. */
export const SPA_PREFIX = `${API_BASE}/spa/`;
/** Backoff schedule (ms) while the dashboard finishes its token exchange; ~10 s total. */
export const AUTH_BACKOFF_MS = Object.freeze([250, 500, 1000, 2000, 3000, 3250]);
/**
 * Once the cold path gave up (no session yet — e.g. the shell shows "Session expired,
 * paste a token URL"), the host stays idle but keeps trying: one `/state` probe every
 * `IDLE_RETRY_MS`, `IDLE_RETRIES` times (~20 min), plus a nudge whenever the window
 * regains focus or the manager page asks for it. Before this the host pinned `null` for
 * the document's lifetime, and a sign-in through the banner (which never reloads the
 * document) left every mod page failing with "the SPA host did not start".
 */
export const IDLE_RETRY_MS = 15000;
export const IDLE_RETRIES = 80;

/**
 * The runtime `spa` parts of every active mod, in Loader order.
 * Boot-activated parts are inlined into `index.html` by the Patcher and are skipped here.
 * @param {any} state the `/state` JSON
 * @returns {{modId: string, version: string, index: number, path: string, url: string}[]}
 */
export function selectRuntimeParts(state) {
  const out = [];
  if (!state || typeof state !== "object" || !state.mods) return out;
  const order = Array.isArray(state.order) ? state.order : Object.keys(state.mods);
  for (const modId of order) {
    const mod = state.mods[modId];
    if (!mod || !mod.active) continue;
    for (const part of mod.parts ?? []) {
      if (part.kind !== "spa" || (part.activation ?? "runtime") !== "runtime" || part.status === "error") continue;
      const file = String(part.path ?? "").split("/").pop();
      if (!file) continue;
      out.push({ modId, version: String(mod.version ?? ""), index: part.index ?? 0, path: part.path, url: `${SPA_PREFIX}${encodeURIComponent(modId)}/${file}` });
    }
  }
  return out;
}

/** A read-only summary of every mod for `window.floofy.mods`. */
export function summarizeMods(state) {
  const mods = [];
  for (const [id, mod] of Object.entries(state?.mods ?? {})) {
    mods.push(
      Object.freeze({
        id,
        version: mod.version,
        name: mod.name,
        active: Boolean(mod.active),
        enabled: Boolean(mod.enabled),
        reason: mod.reason ?? null,
        parts: Object.freeze((mod.parts ?? []).map((p) => Object.freeze({ kind: p.kind, side: p.side, path: p.path, status: p.status, activation: p.activation ?? null }))),
        governance: Object.freeze([...(mod.governance ?? [])]),
      }),
    );
  }
  return Object.freeze(mods);
}

/** The `host` block of `window.floofy` from the Loader's host facts. */
export function hostFacts(state) {
  const h = state?.host ?? {};
  return Object.freeze({
    version: h.version ?? null,
    edition: h.edition ?? null,
    channel: h.channel ?? null,
    build_version: h.build_version ?? null,
    base_version: h.base_version ?? null,
    profile: h.profile ?? null,
  });
}

/** Prefixed console logger for a mod. */
export function modLogger(modId, base = console) {
  const prefix = `[floofy:${modId}]`;
  return Object.freeze({
    debug: (...args) => base.debug(prefix, ...args),
    info: (...args) => base.info(prefix, ...args),
    warn: (...args) => base.warn(prefix, ...args),
    error: (...args) => base.error(prefix, ...args),
  });
}

/**
 * Wrap `history.pushState/replaceState` and listen to `popstate` so `route.changed` fires on SPA navigation.
 * Idempotent per window (marked on the history object).
 */
export function installRouteWatcher(win, bus) {
  const history = win?.history;
  if (!history || history.__floofyRouteWatcher) return () => {};
  history.__floofyRouteWatcher = true;
  const announce = (how) => {
    const loc = win.location ?? {};
    bus.emit("route.changed", { path: loc.pathname ?? "", search: loc.search ?? "", hash: loc.hash ?? "", how });
  };
  for (const method of ["pushState", "replaceState"]) {
    const original = history[method];
    if (typeof original !== "function") continue;
    history[method] = function patched(...args) {
      const result = original.apply(this, args);
      announce(method);
      return result;
    };
  }
  const onPop = () => announce("popstate");
  win.addEventListener?.("popstate", onPop);
  return () => win.removeEventListener?.("popstate", onPop);
}

const sleep = (ms, setTimer = setTimeout) => new Promise((resolve) => setTimer(resolve, ms));

/**
 * Create a host controller. `start()` runs the sequence once and resolves to `window.floofy`
 * (or `null` when the Loader state is unreachable).
 * @param {object} [env]
 * @param {typeof fetch} [env.fetch]
 * @param {(url: string) => Promise<any>} [env.importer] dynamic import, injectable for tests
 * @param {any} [env.window] the document's window (`globalThis` when absent)
 * @param {any} [env.document]
 * @param {Console} [env.console]
 * @param {typeof setTimeout} [env.setTimeout]
 * @param {number[]} [env.backoff] override the auth backoff schedule
 * @param {number} [env.idleRetryMs] override the idle retry period
 * @param {number} [env.idleRetries] override the idle retry count (0 disables resuming)
 * @param {any[]} [env.surfaceDefinitions] override the surface registry (tests)
 */
export function createHost(env = {}) {
  const win = env.window ?? (typeof window !== "undefined" ? window : globalThis);
  const doc = env.document ?? (typeof document !== "undefined" ? document : null);
  const log = env.console ?? console;
  const fetchImpl = env.fetch ?? ((...args) => win.fetch(...args));
  const importer = env.importer ?? ((url) => import(url));
  const setTimer = env.setTimeout ?? ((fn, ms) => setTimeout(fn, ms));
  const backoff = env.backoff ?? AUTH_BACKOFF_MS;
  const idleRetryMs = env.idleRetryMs ?? IDLE_RETRY_MS;
  const idleRetries = env.idleRetries ?? IDLE_RETRIES;

  const bus = createBus({ log: (message, error) => log.warn(`[floofy] ${message}`, error ?? "") });
  /** @type {Map<string, {module: any, ctx: any}>} */
  const loaded = new Map();
  /** The in-flight or successful start; cleared again when a start gave up so it can resume. */
  let started = null;
  let latestState = null;
  let idleTimer = null;
  let idleAttempts = 0;
  let nudgesInstalled = false;

  async function getJson(path) {
    const response = await fetchImpl(`${API_BASE}${path}`, { credentials: "same-origin", headers: { Accept: "application/json" } });
    if (!response.ok) return { ok: false, status: response.status, body: null };
    return { ok: true, status: response.status, body: await response.json() };
  }

  /** Read `/state`, waiting out the token exchange on the cold path (`schedule` = backoff to spend). */
  async function fetchState(schedule = backoff) {
    let reply = await getJson("/state").catch((error) => ({ ok: false, status: 0, body: null, error }));
    for (let attempt = 0; !reply.ok && attempt < schedule.length; attempt += 1) {
      await sleep(schedule[attempt], setTimer);
      const health = await getJson("/health").catch(() => ({ ok: false }));
      if (!health.ok) continue;
      reply = await getJson("/state").catch((error) => ({ ok: false, status: 0, body: null, error }));
    }
    if (!reply.ok) return null;
    latestState = reply.body;
    return reply.body;
  }

  async function postFault(modId, error, where) {
    const { message, stack } = describeError(error);
    const body = JSON.stringify({ message: `${where}: ${message}`, stack, source: "spa" });
    try {
      await fetchImpl(`${API_BASE}/mods/${encodeURIComponent(modId)}/fault`, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body,
      });
    } catch (postError) {
      log.warn(`[floofy] could not report the fault of ${modId}`, postError);
    }
  }

  const boundary = createBoundary({
    target: win,
    log: (message, error) => log.error(`[floofy] ${message}`, error ?? ""),
    onFault(modId, error, where) {
      const entry = loaded.get(modId);
      if (entry?.module && typeof entry.module.deactivate === "function") {
        try {
          entry.module.deactivate(entry.ctx);
        } catch (deactivateError) {
          log.warn(`[floofy:${modId}] deactivate after fault threw`, deactivateError);
        }
      }
      loaded.delete(modId);
      bus.dropOwner(modId);
      bus.emit("mod.faulted", { mod: modId, where, ...describeError(error) });
      void postFault(modId, error, where);
    },
  });

  function buildFloofy(state, surfaces, patches, reporter) {
    const facts = hostFacts(state);
    return Object.freeze({
      version: SPA_HOST_VERSION,
      unofficial: true,
      host: facts,
      api_version: state.api_version ?? state.host?.api_version ?? null,
      framework_version: state.loaderVersion ?? state.host?.framework_version ?? null,
      events: bus,
      surfaces,
      patches,
      report: (options) => reporter.report(options),
      mods: summarizeMods(state),
      /** The API a mod's own page receives (Requirement 16.4): config, routes, theme tokens, state — same-origin, per mod. */
      mod: (modId) => createModApi(modId, { fetch: fetchImpl, document: doc, window: win, console: log, state: async () => (await fetchState()) ?? latestState }),
      loaded: () => [...loaded.keys()],
      faulted: () => boundary.faultedMods(),
      state: () => latestState,
      refresh: async () => {
        const fresh = await fetchState();
        return fresh;
      },
    });
  }

  function contextFor(part, floofy, surfaces) {
    const modId = part.modId;
    return Object.freeze({
      modId,
      version: part.version,
      host: floofy.host,
      api_version: floofy.api_version,
      baseUrl: `${SPA_PREFIX}${encodeURIComponent(modId)}/`,
      events: scoped(bus, modId),
      log: modLogger(modId, log),
      surfaces,
      config: Object.freeze({
        /** The mod's published `exports` (what its Python side put in `ctx.state`). */
        async read() {
          const state = (await fetchState()) ?? latestState;
          return state?.mods?.[modId]?.exports ?? {};
        },
      }),
    });
  }

  async function loadPart(part, floofy, surfaces) {
    const modId = part.modId;
    boundary.watch(modId, `${SPA_PREFIX}${encodeURIComponent(modId)}/`);
    const imported = await boundary.run(modId, () => importer(part.url), `import ${part.path}`);
    if (!imported.ok) return false;
    const module = imported.value;
    const ctx = contextFor(part, floofy, surfaces);
    loaded.set(modId, { module, ctx });
    const activate = typeof module?.default === "function" ? module.default : module?.activate;
    if (typeof activate === "function") {
      const activated = await boundary.run(modId, () => activate(ctx), `activate ${part.path}`);
      if (!activated.ok) return false;
    }
    bus.emit("mod.loaded", { mod: modId, version: part.version, url: part.url });
    return true;
  }

  async function start({ resume = false } = {}) {
    if (win.floofy) return win.floofy;
    // The first attempt waits out the token exchange; a resume is a single cheap probe.
    const state = await fetchState(resume ? [] : backoff);
    if (!state) {
      if (!resume) log.debug?.("[floofy] Loader state unavailable (not authenticated or the Loader is not installed); the SPA host stays idle until a session appears");
      win.__floofyHostStatus = "unavailable";
      return null;
    }
    win.__floofyHostStatus = "started";
    const surfaces = createSurfaces({ document: doc, window: win, bus, definitions: env.surfaceDefinitions, fetch: fetchImpl });
    const patches = createPatches({ document: doc, fetch: fetchImpl, state });
    const reporter = createReporter({ surfaces, patches, state: () => latestState, document: doc, window: win, fetch: fetchImpl });
    const floofy = buildFloofy(state, surfaces, patches, reporter);
    Object.defineProperty(win, "floofy", { value: floofy, writable: false, configurable: false, enumerable: true });
    installRouteWatcher(win, bus);
    surfaces.start?.();
    const parts = selectRuntimeParts(state);
    for (const part of parts) {
      // sequential, in Loader order: loadBefore/loadAfter hold on the dashboard side too
      await loadPart(part, floofy, surfaces);
    }
    bus.emit("host.ready", { mods: parts.map((p) => p.modId), loaded: [...loaded.keys()], faulted: boundary.faultedMods() });
    return floofy;
  }

  /** Run a start attempt; when it gives up, forget it and arrange to resume later. */
  function runStart(options) {
    const attempt = start(options).then(
      (floofy) => {
        if (!floofy) {
          started = null;
          scheduleResume();
        }
        return floofy;
      },
      (error) => {
        started = null;
        throw error;
      },
    );
    started = attempt;
    return attempt;
  }

  /** A resume attempt, unless one is in flight or the host already runs. */
  function nudge() {
    if (win.floofy || started) return;
    void runStart({ resume: true }).catch((error) => log.warn("[floofy] SPA host resume failed", error));
  }

  function scheduleResume() {
    if (win.floofy) return;
    if (!nudgesInstalled) {
      nudgesInstalled = true;
      win.addEventListener?.("focus", nudge);
      doc?.addEventListener?.("visibilitychange", () => {
        if (doc.visibilityState !== "hidden") nudge();
      });
    }
    if (idleTimer !== null || idleAttempts >= idleRetries) return;
    idleAttempts += 1;
    let pending = true;
    idleTimer = setTimer(() => {
      pending = false;
      idleTimer = null;
      nudge();
    }, idleRetryMs);
    if (!pending) idleTimer = null; // a synchronous timer already fired
  }

  return Object.freeze({
    /**
     * Start once; later calls return the same promise while a start is in flight or
     * succeeded. After a start gave up (no session yet) a call tries again at once, so a
     * page opened after signing in gets a live host instead of the stale `null`.
     */
    ensureStarted() {
      return started ?? runStart({ resume: idleAttempts > 0 });
    },
    fetchState,
    boundary,
    bus,
    loadedMods: () => [...loaded.keys()],
  });
}

let defaultHost = null;

/** Start the default host in this document (idempotent); returns the promise of `window.floofy`. */
export function ensureStarted(env) {
  if (!defaultHost) defaultHost = createHost(env);
  return defaultHost.ensureStarted();
}

// Auto-start inside a real document (the loader tag path). Tests import this
// module under node, where there is no document, and drive `createHost` directly.
if (typeof document !== "undefined" && typeof window !== "undefined" && !window.__floofyNoAutostart) {
  ensureStarted().catch((error) => console.error("[floofy] SPA host failed to start", error));
}
