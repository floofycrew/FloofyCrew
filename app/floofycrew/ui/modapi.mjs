// FloofyCrew SPA host — `floofy.mod(id)`, the API a mod's own page receives (Requirement 16.4; task 11.5).
//
// The FloofyCrew App mounts a mod's `ui` part with `mount(container, api)` where
// `api = window.floofy.mod(id)`; a runtime `spa` part may call it too. Everything
// is same-origin (Requirement 4.6) and backed by the Loader's routes behind the
// dashboard auth:
//
//   api.config.get()        → GET  /api/apps/floofycrew/mods/<id>/config   the mod's .floofy/config.json
//   api.config.set(patch)   → PUT  /api/apps/floofycrew/mods/<id>/config   merged key by key, null deletes, 64 KiB cap
//   api.routes.list()       → the mod's python-hook routes (ctx.routes.add), from /state
//   api.routes.fetch(p, i)  → fetch(/api/apps/floofycrew/mods/<id>/api/<p>, i)  scoped to the mod's own routes
//   api.theme.tokens()      → the host's CSS custom properties on :root ({"--bg": "…", …})
//   api.theme.current()     → {theme, mode} from data-theme/data-mode and localStorage['mc-color-theme']
//   api.state()             → the mod's Loader verdict (active, reason, parts, exports, tier, routes)
//   api.dialog.confirm/prompt/alert/form → in-document dialogs (dialog.mjs): the desktop shell has no
//                             window.alert/confirm/prompt, so a page never uses those
//   api.baseUrl             → /api/apps/floofycrew/ui/mods/<id>/  (for the page's own assets)
//
// Injectable `env` (fetch, document, window, state) keeps it unit-testable under node.
"use strict";

import { createDialogs } from "./dialog.mjs";

export const API_BASE = "/api/apps/floofycrew";

/** CSS custom properties the host is known to set; `theme.tokens()` reads these plus whatever the stylesheets declare on :root/html. */
export const KNOWN_TOKENS = Object.freeze(["--bg", "--bg-elevated", "--card", "--text", "--text-muted", "--accent", "--border", "--border-strong", "--ok", "--warn", "--danger", "--mono", "--theme-logo"]);

function encodeSegments(path) {
  return String(path || "")
    .replace(/^\/+|\/+$/g, "")
    .split("/")
    .filter(Boolean)
    .map(encodeURIComponent)
    .join("/");
}

/** The custom-property names declared for :root / html in the document's same-origin stylesheets. */
export function declaredTokenNames(doc) {
  const names = new Set(KNOWN_TOKENS);
  const sheets = doc && doc.styleSheets ? Array.from(doc.styleSheets) : [];
  for (const sheet of sheets) {
    let rules;
    try {
      rules = sheet.cssRules;
    } catch {
      continue; // a cross-origin sheet (never ours) cannot be read
    }
    if (!rules) continue;
    for (const rule of Array.from(rules)) {
      const selector = rule.selectorText || "";
      if (!/(^|,)\s*(:root|html)(\[[^\]]*\])?\s*($|,)/.test(selector) || !rule.style) continue;
      for (let i = 0; i < rule.style.length; i += 1) {
        const name = rule.style[i];
        if (name && name.startsWith("--")) names.add(name);
      }
    }
  }
  return Array.from(names).sort();
}

/**
 * Build the API for one mod.
 * @param {string} modId
 * @param {object} [env]
 * @param {typeof fetch} [env.fetch]
 * @param {Document} [env.document]
 * @param {Window} [env.window]
 * @param {() => Promise<any>} [env.state] the Loader state document (fresh); defaults to GET /state
 * @param {Console} [env.console]
 */
export function createModApi(modId, env = {}) {
  if (typeof modId !== "string" || !/^[a-z][a-z0-9_-]{1,63}$/.test(modId)) throw new TypeError(`floofy.mod(id): ${JSON.stringify(modId)} is not a mod id`);
  const win = env.window ?? (typeof window !== "undefined" ? window : globalThis);
  const doc = env.document ?? (typeof document !== "undefined" ? document : null);
  const fetchImpl = env.fetch ?? ((...args) => win.fetch(...args));
  const log = env.console ?? console;
  const base = `${API_BASE}/mods/${encodeURIComponent(modId)}`;
  const prefix = `[floofy:${modId}]`;

  async function getJson(url, init) {
    const response = await fetchImpl(url, { credentials: "same-origin", headers: { Accept: "application/json", ...(init && init.headers ? init.headers : {}) }, ...init });
    let body = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    if (!response.ok) throw new Error(`${prefix} ${init && init.method ? init.method : "GET"} ${url}: HTTP ${response.status}${body && body.error ? ` — ${body.error}` : ""}`);
    return body;
  }

  const fetchState = env.state ?? (async () => getJson(`${API_BASE}/state`));

  const config = Object.freeze({
    /** The mod's config object (schema-free JSON). */
    async get() {
      return (await getJson(`${base}/config`)).config ?? {};
    },
    /** Merge `patch` into the config (a `null` value deletes the key); resolves to the whole document. */
    async set(patch) {
      if (!patch || typeof patch !== "object" || Array.isArray(patch)) throw new TypeError(`${prefix} config.set(patch): patch must be an object`);
      return (await getJson(`${base}/config`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ patch }) })).config ?? {};
    },
  });

  const routes = Object.freeze({
    /** `[{method, path}]` the mod's python-hook registered with ctx.routes.add. */
    async list() {
      const state = await fetchState();
      return (state && state.mods && state.mods[modId] && state.mods[modId].routes) || [];
    },
    /** `fetch()` scoped to the mod's own routes: `path` is relative (`echo`, `items/3`), never absolute. */
    fetch(path, init = {}) {
      const rel = encodeSegments(path);
      if (!rel || /^https?:/i.test(String(path))) throw new TypeError(`${prefix} routes.fetch(path): a relative route path is required, got ${JSON.stringify(path)}`);
      return fetchImpl(`${base}/api/${rel}`, { credentials: "same-origin", ...init });
    },
    /** The absolute URL of one of the mod's routes. */
    url(path) {
      return `${base}/api/${encodeSegments(path)}`;
    },
  });

  const theme = Object.freeze({
    /** `{"--bg": "#…", …}` — the host's theme tokens as computed on :root. */
    tokens() {
      if (!doc || !doc.documentElement || !win.getComputedStyle) return {};
      const computed = win.getComputedStyle(doc.documentElement);
      const out = {};
      for (const name of declaredTokenNames(doc)) {
        const value = computed.getPropertyValue(name);
        if (value && value.trim()) out[name] = value.trim();
      }
      return out;
    },
    /** `{theme, mode}` — the active colour theme (localStorage `mc-color-theme`, else `data-theme`) and `data-mode`. */
    current() {
      const root = doc && doc.documentElement;
      let stored = null;
      try {
        stored = win.localStorage ? win.localStorage.getItem("mc-color-theme") : null;
      } catch {
        stored = null;
      }
      return { theme: stored || (root ? root.getAttribute("data-theme") : null), mode: root ? root.getAttribute("data-mode") : null };
    },
  });

  return Object.freeze({
    id: modId,
    unofficial: true,
    baseUrl: `${API_BASE}/ui/mods/${encodeURIComponent(modId)}/`,
    config,
    routes,
    theme,
    /** In-document dialogs — `confirm(text)`, `prompt(text, {value, validate})`, `alert(text)`, `form({title, text, fields})`. */
    dialog: createDialogs(modId, { document: doc }),
    /** The mod's Loader verdict: active, reason, parts, exports, tier, routes, version, title. */
    async state() {
      const state = await fetchState();
      return (state && state.mods && state.mods[modId]) || null;
    },
    log: Object.freeze({
      debug: (...args) => log.debug(prefix, ...args),
      info: (...args) => log.info(prefix, ...args),
      warn: (...args) => log.warn(prefix, ...args),
      error: (...args) => log.error(prefix, ...args),
    }),
  });
}
