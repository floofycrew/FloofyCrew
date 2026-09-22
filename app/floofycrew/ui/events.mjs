// FloofyCrew SPA host — the dashboard-side event bus (Requirement 3.8, 4.1).
//
// A tiny fail-open pub/sub: a subscriber that throws is logged and counted,
// the other subscribers still receive the event, and the emitter never sees
// the exception. `scoped(bus, modId)` gives a mod a view that remembers its
// subscriptions so the host can drop them all when the mod faults.
//
// Event names the host publishes: `mod.loaded`, `mod.faulted`,
// `surfaces.changed`, `route.changed`, `host.ready`. `*` subscribes to
// everything (the handler receives the event name as its first argument).
"use strict";

/**
 * @typedef {(name: string, payload: unknown) => void} Handler
 */

/**
 * Create a new bus.
 * @param {{log?: (message: string, error?: unknown) => void, historyLimit?: number}} [options]
 */
export function createBus(options = {}) {
  const log = options.log ?? ((message, error) => console.warn(`[floofy] ${message}`, error ?? ""));
  const historyLimit = options.historyLimit ?? 50;
  /** @type {Map<string, Set<{handler: Handler, owner: string|null}>>} */
  const subscribers = new Map();
  /** @type {{name: string, payload: unknown, at: number}[]} */
  const history = [];
  let errors = 0;

  function bucket(name) {
    let set = subscribers.get(name);
    if (!set) {
      set = new Set();
      subscribers.set(name, set);
    }
    return set;
  }

  /**
   * Subscribe `handler` to `name` (`*` for every event). Returns an unsubscribe function.
   * @param {string} name
   * @param {Handler} handler
   * @param {string|null} [owner] mod id used for bulk removal
   */
  function on(name, handler, owner = null) {
    if (typeof handler !== "function") throw new TypeError("handler must be a function");
    const entry = { handler, owner };
    bucket(name).add(entry);
    return () => {
      bucket(name).delete(entry);
    };
  }

  /** Subscribe for exactly one delivery. */
  function once(name, handler, owner = null) {
    const off = on(
      name,
      (eventName, payload) => {
        off();
        handler(eventName, payload);
      },
      owner,
    );
    return off;
  }

  /** Remove every subscription of `handler` on `name`. */
  function off(name, handler) {
    for (const entry of bucket(name)) {
      if (entry.handler === handler) bucket(name).delete(entry);
    }
  }

  /**
   * Deliver `payload` to the subscribers of `name` and of `*`, synchronously and fail-open.
   * @returns {number} how many handlers ran without throwing
   */
  function emit(name, payload) {
    history.push({ name, payload, at: Date.now() });
    if (history.length > historyLimit) history.shift();
    let delivered = 0;
    for (const set of [subscribers.get(name), subscribers.get("*")]) {
      if (!set) continue;
      for (const entry of [...set]) {
        try {
          entry.handler(name, payload);
          delivered += 1;
        } catch (error) {
          errors += 1;
          log(`event ${name}: subscriber${entry.owner ? ` of ${entry.owner}` : ""} threw`, error);
        }
      }
    }
    return delivered;
  }

  /** Drop every subscription registered with `owner`. Returns how many were dropped. */
  function dropOwner(owner) {
    let dropped = 0;
    for (const set of subscribers.values()) {
      for (const entry of [...set]) {
        if (entry.owner === owner) {
          set.delete(entry);
          dropped += 1;
        }
      }
    }
    return dropped;
  }

  /** Snapshot for `window.floofy.events.inspect()` and the reporter. */
  function inspect() {
    const subscriptions = {};
    for (const [name, set] of subscribers) {
      if (set.size) subscriptions[name] = [...set].map((entry) => entry.owner ?? "host");
    }
    return { subscriptions, history: history.slice(-20), errors };
  }

  return Object.freeze({ on, once, off, emit, dropOwner, inspect });
}

/**
 * A mod-scoped view of `bus`: same `on/once/off/emit`, every subscription attributed to `modId`.
 * @param {ReturnType<typeof createBus>} bus
 * @param {string} modId
 */
export function scoped(bus, modId) {
  return Object.freeze({
    on: (name, handler) => bus.on(name, handler, modId),
    once: (name, handler) => bus.once(name, handler, modId),
    off: (name, handler) => bus.off(name, handler),
    emit: (name, payload) => bus.emit(name, { ...(payload && typeof payload === "object" ? payload : { value: payload }), source: modId }),
    inspect: () => bus.inspect(),
  });
}
