// FloofyCrew SPA host — the error boundary every `spa` part runs in (Requirement 4.1).
//
// Three layers, all fail-open: (1) `run()` wraps the module import and its
// `activate()` call in try/catch; (2) scoped `error` / `unhandledrejection`
// listeners catch what a module does later (timers, event handlers, dangling
// promises) and attribute it to a mod by the URL prefix of its served files
// (`/api/apps/floofycrew/spa/<id>/`) found in the stack; (3) the first fault
// of a mod is reported once (`onFault`), the mod is marked faulted and later
// errors from it are only logged. Nothing here ever rethrows into the host.
"use strict";

/**
 * Which mod an error belongs to, judged by the served-file URL prefixes in its stack.
 * @param {unknown} error an Error, an ErrorEvent-like `{filename, error}` or anything else
 * @param {Iterable<[string, string]>} prefixes `[modId, urlPrefix]` pairs
 * @returns {string|null}
 */
export function attributeError(error, prefixes) {
  const text = errorText(error);
  if (!text) return null;
  let best = null;
  let bestIndex = Infinity;
  for (const [modId, prefix] of prefixes) {
    const index = text.indexOf(prefix);
    if (index >= 0 && index < bestIndex) {
      best = modId;
      bestIndex = index;
    }
  }
  return best;
}

/** Message + stack (+ ErrorEvent filename) as one searchable string. */
export function errorText(error) {
  if (error == null) return "";
  const parts = [];
  if (typeof error === "string") return error;
  if (typeof error.filename === "string") parts.push(error.filename);
  const inner = error.error ?? error.reason ?? error;
  if (inner && typeof inner === "object") {
    if (typeof inner.stack === "string") parts.push(inner.stack);
    if (typeof inner.message === "string") parts.push(inner.message);
  } else if (inner !== undefined) {
    parts.push(String(inner));
  }
  if (typeof error.message === "string" && !parts.includes(error.message)) parts.push(error.message);
  return parts.join("\n");
}

/** `{message, stack}` for the fault route, from anything thrown. */
export function describeError(error) {
  const inner = error && typeof error === "object" ? error.error ?? error.reason ?? error : error;
  if (inner && typeof inner === "object") {
    return {
      message: String(inner.message ?? inner.name ?? "SPA module failed"),
      stack: typeof inner.stack === "string" ? inner.stack.slice(0, 4000) : "",
    };
  }
  return { message: inner === undefined ? "SPA module failed" : String(inner), stack: "" };
}

/**
 * Create a boundary.
 * @param {object} options
 * @param {(modId: string, error: unknown, where: string) => void} options.onFault called once per mod on its first fault
 * @param {{addEventListener?: Function}} [options.target] where the global listeners attach (default `window`)
 * @param {(message: string, error?: unknown) => void} [options.log]
 */
export function createBoundary({ onFault, target = typeof window !== "undefined" ? window : null, log = defaultLog }) {
  /** @type {Map<string, string>} */
  const prefixes = new Map();
  /** @type {Set<string>} */
  const faulted = new Set();
  let listening = false;

  function fault(modId, error, where) {
    if (faulted.has(modId)) {
      log(`${modId}: another error after the fault (${where})`, error);
      return false;
    }
    faulted.add(modId);
    log(`${modId}: fault in ${where}; the mod is disabled, the host and other mods continue`, error);
    try {
      onFault(modId, error, where);
    } catch (reportError) {
      log(`${modId}: fault report itself failed`, reportError);
    }
    return true;
  }

  function handleGlobal(event, kind) {
    const modId = attributeError(event, prefixes);
    if (modId === null) return;
    fault(modId, event.error ?? event.reason ?? event, kind);
    if (typeof event.preventDefault === "function") event.preventDefault();
  }

  function listen() {
    if (listening || !target || typeof target.addEventListener !== "function") return;
    listening = true;
    target.addEventListener("error", (event) => handleGlobal(event, "uncaught error"));
    target.addEventListener("unhandledrejection", (event) => handleGlobal(event, "unhandled rejection"));
  }

  return Object.freeze({
    /** Attribute later async errors carrying `urlPrefix` in their stack to `modId`. */
    watch(modId, urlPrefix) {
      prefixes.set(modId, urlPrefix);
      listen();
    },
    /**
     * Run `fn` (sync or async) for `modId`; a throw becomes a fault, never an exception.
     * @template T
     * @param {string} modId
     * @param {() => T | Promise<T>} fn
     * @param {string} where label for the log ("import", "activate", ...)
     * @returns {Promise<{ok: true, value: T} | {ok: false, error: unknown}>}
     */
    async run(modId, fn, where) {
      if (faulted.has(modId)) return { ok: false, error: new Error(`${modId} already faulted`) };
      try {
        return { ok: true, value: await fn() };
      } catch (error) {
        fault(modId, error, where);
        return { ok: false, error };
      }
    },
    /** Report a fault the caller detected itself (an activate() that returned a rejected promise, ...). */
    fault,
    isFaulted: (modId) => faulted.has(modId),
    faultedMods: () => [...faulted],
    prefixes: () => new Map(prefixes),
  });
}

function defaultLog(message, error) {
  console.error(`[floofy] ${message}`, error ?? "");
}
