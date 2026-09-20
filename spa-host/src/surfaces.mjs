// FloofyCrew SPA host — `window.floofy.surfaces` (Requirement 4.4).
//
// Stable names for host UI surfaces, located by CONTENT fingerprints and never
// by chunk hash or line number. Two fingerprint kinds per surface:
//
// * `dom` — a CSS selector over attributes the host renders literally and that
//   survive minification (`data-testid`, ids, ARIA roles and labels, Tailwind
//   class names, `rel`/`type`), optionally with a required text substring and
//   a `route` prefix (the surface exists only on that route; elsewhere the DOM
//   check is "not applicable" rather than a miss). Resolved to live nodes and
//   re-resolved on route change and DOM mutation; `surfaces.changed` fires
//   when a node identity or a match status changed.
// * `bundle` — a literal (or regex) expected in the module text of a hashed
//   chunk named by its STEM (`App`, `main`, `useTheme`) — or by a list of
//   fallback stems tried in order, for a module the bundler folds into a
//   differently named shared chunk on another build. The real file name is
//   discovered from the never-cached shell (`<script src>`, `modulepreload`
//   hints) or, for a lazy chunk, from a `./<stem>-<hash>.js` reference inside
//   an already-fetched chunk. Chunk text is fetched same-origin from
//   `/assets/*` (admitted by `connect-src 'self'`) and cached per document.
//
// The shipped registry is `surfaces.json` (one row per surface, `fromBuild`
// naming the host build it was written against). Nothing here rewrites the
// document; this is a locator and a reporter input.
"use strict";

/** Extract `{stem, name}` chunk entries from a list of URLs or module text (`/assets/<stem>-<hash>.js`). */
export const CHUNK_RE = /(?:\/assets\/|\.\/)([A-Za-z][A-Za-z0-9_.]*?)-([A-Za-z0-9_-]{8})\.js\b/g;

/**
 * Discover `stem -> file name` from the shell's script/preload tags.
 * @param {Document|null} doc
 * @returns {Map<string, string>}
 */
export function chunksFromDocument(doc) {
  const found = new Map();
  if (!doc || typeof doc.querySelectorAll !== "function") return found;
  const urls = [];
  for (const node of doc.querySelectorAll('script[type="module"][src], link[rel="modulepreload"][href]')) {
    urls.push(node.getAttribute("src") ?? node.getAttribute("href") ?? "");
  }
  addChunks(found, urls.join("\n"));
  return found;
}

/** Add every `<stem>-<hash>.js` reference in `text` to `into` (first hit per stem wins). */
export function addChunks(into, text) {
  for (const match of String(text).matchAll(CHUNK_RE)) {
    const [, stem, hash] = match;
    if (!into.has(stem)) into.set(stem, `${stem}-${hash}.js`);
  }
  return into;
}

/** The chunk stems a bundle fingerprint may live in, in fallback order (`chunk` is a string or a list). */
export function bundleStems(fingerprint) {
  const chunk = fingerprint?.chunk;
  const stems = Array.isArray(chunk) ? chunk : [chunk];
  return stems.filter((stem) => typeof stem === "string" && stem);
}

/**
 * Evaluate one bundle fingerprint against chunk text.
 * @param {{contains?: string, regex?: string, count?: "some"|"once"}} fingerprint
 * @param {string} text
 * @returns {{status: "matched"|"missed", reason: string}}
 */
export function matchBundle(fingerprint, text) {
  let hits = 0;
  if (typeof fingerprint.regex === "string") {
    const pattern = new RegExp(fingerprint.regex, "g");
    for (const _ of text.matchAll(pattern)) hits += 1;
  } else if (typeof fingerprint.contains === "string" && fingerprint.contains) {
    let index = text.indexOf(fingerprint.contains);
    while (index >= 0) {
      hits += 1;
      index = text.indexOf(fingerprint.contains, index + fingerprint.contains.length);
    }
  } else {
    return { status: "missed", reason: "fingerprint declares neither contains nor regex" };
  }
  const once = (fingerprint.count ?? "some") === "once";
  if (hits === 0) return { status: "missed", reason: "no occurrence in the chunk text" };
  if (once && hits > 1) return { status: "missed", reason: `${hits} occurrences; exactly one required` };
  return { status: "matched", reason: `${hits} occurrence(s)` };
}

/** Whether `route` (a path prefix, or an array of prefixes) covers `pathname`. */
export function routeApplies(route, pathname) {
  if (!route) return true;
  const prefixes = Array.isArray(route) ? route : [route];
  return prefixes.some((prefix) => pathname === prefix || pathname.startsWith(prefix.endsWith("/") ? prefix : `${prefix}/`) || (prefix === "/" && pathname === "/"));
}

/**
 * Resolve one `dom` fingerprint against `doc`.
 * @returns {{status: "matched"|"missed"|"not-applicable", node: Element|null, reason: string}}
 */
export function resolveDom(fingerprint, doc, pathname = "/") {
  if (!fingerprint) return { status: "not-applicable", node: null, reason: "no dom fingerprint" };
  if (!doc) return { status: "not-applicable", node: null, reason: "no document" };
  if (!routeApplies(fingerprint.route, pathname)) return { status: "not-applicable", node: null, reason: `route ${pathname} is outside ${JSON.stringify(fingerprint.route)}` };
  let nodes;
  try {
    nodes = [...doc.querySelectorAll(fingerprint.selector)];
  } catch (error) {
    return { status: "missed", node: null, reason: `invalid selector: ${error.message}` };
  }
  if (typeof fingerprint.text === "string") nodes = nodes.filter((n) => (n.textContent ?? "").includes(fingerprint.text));
  if (nodes.length === 0) return { status: "missed", node: null, reason: `selector ${fingerprint.selector} matches nothing` };
  if ((fingerprint.count ?? "some") === "once" && nodes.length > 1) return { status: "missed", node: null, reason: `${nodes.length} nodes match; exactly one required` };
  return { status: "matched", node: nodes[0], reason: `${nodes.length} node(s)` };
}

/**
 * Create the surface registry.
 * @param {object} options
 * @param {Document|null} options.document
 * @param {any} options.window
 * @param {ReturnType<import("./events.mjs").createBus>} options.bus
 * @param {any[]} [options.definitions] surface definitions (default: the shipped `surfaces.json`, loaded lazily)
 * @param {typeof fetch} [options.fetch]
 * @param {(fn: Function, ms: number) => any} [options.debounce] timer used to coalesce mutations
 */
export function createSurfaces({ document: doc, window: win, bus, definitions, fetch: fetchImpl, debounce }) {
  /** @type {any[]} */
  let defs = Array.isArray(definitions) ? definitions : [];
  /** @type {Promise<any[]>|null} */
  let loading = null;
  /** @type {Map<string, {status: string, node: Element|null, reason: string}>} */
  const resolved = new Map();
  /** @type {Map<string, string>} */
  const chunkText = new Map();
  let observer = null;
  let timer = null;
  const setTimer = debounce ?? ((fn, ms) => setTimeout(fn, ms));

  const pathname = () => win?.location?.pathname ?? "/";

  async function ensureDefinitions() {
    if (defs.length || definitions !== undefined) return defs;
    if (!loading) {
      loading = (async () => {
        try {
          const response = await (fetchImpl ?? win.fetch)(new URL("./surfaces.json", import.meta.url).pathname, { credentials: "same-origin" });
          const body = await response.json();
          defs = Array.isArray(body?.surfaces) ? body.surfaces : [];
        } catch {
          defs = [];
        }
        return defs;
      })();
    }
    return loading;
  }

  function resolveAll() {
    const now = pathname();
    let changed = false;
    for (const def of defs) {
      const next = resolveDom(def.dom, doc, now);
      const prev = resolved.get(def.name);
      if (!prev || prev.status !== next.status || prev.node !== next.node) changed = true;
      resolved.set(def.name, next);
    }
    return changed;
  }

  function refresh(how = "manual") {
    const changed = resolveAll();
    const matched = [...resolved.entries()].filter(([, r]) => r.status === "matched").map(([n]) => n);
    const missed = [...resolved.entries()].filter(([, r]) => r.status === "missed").map(([n]) => n);
    if (changed) bus.emit("surfaces.changed", { how, matched, missed, route: pathname() });
    return { changed, matched, missed };
  }

  function scheduleRefresh(how) {
    if (timer) return;
    timer = setTimer(() => {
      timer = null;
      refresh(how);
    }, 150);
  }

  async function chunkTextFor(stem) {
    const chunks = chunksFromDocument(doc);
    if (!chunks.has(stem)) {
      // a lazy chunk: look for a ./<stem>-<hash>.js reference inside the already-known chunks
      for (const name of [...chunks.values()]) {
        const text = await fetchChunk(name);
        if (text) addChunks(chunks, text);
        if (chunks.has(stem)) break;
      }
    }
    const file = chunks.get(stem);
    if (!file) return { file: null, text: null };
    return { file, text: await fetchChunk(file) };
  }

  async function fetchChunk(name) {
    if (chunkText.has(name)) return chunkText.get(name);
    try {
      const response = await (fetchImpl ?? win.fetch)(`/assets/${name}`, { credentials: "same-origin" });
      const text = response.ok ? await response.text() : null;
      chunkText.set(name, text);
      return text;
    } catch {
      chunkText.set(name, null);
      return null;
    }
  }

  /**
   * Evaluate every surface fingerprint (dom + bundle) — the reporter's input.
   * @returns {Promise<{name: string, kind: "dom"|"bundle", status: string, reason: string, chunk?: string|null}[]>}
   */
  async function check() {
    await ensureDefinitions();
    resolveAll();
    const rows = [];
    for (const def of defs) {
      if (def.dom) {
        const dom = resolved.get(def.name) ?? resolveDom(def.dom, doc, pathname());
        rows.push({ name: def.name, kind: "dom", status: dom.status, reason: dom.reason });
      }
      if (def.bundle) {
        // stems are tried in order: the first referenced chunk whose text matches wins,
        // a miss reports the last stem that was found, no stem found reports them all
        const stems = bundleStems(def.bundle);
        let outcome = null;
        for (const stem of stems) {
          const { file, text } = await chunkTextFor(stem);
          if (!file) continue;
          if (!text) {
            outcome = { name: def.name, kind: "bundle", status: "missed", reason: `chunk ${file} could not be fetched`, chunk: file };
            continue;
          }
          const verdict = matchBundle(def.bundle, text);
          outcome = { name: def.name, kind: "bundle", status: verdict.status, reason: verdict.reason, chunk: file };
          if (verdict.status === "matched") break;
        }
        rows.push(outcome ?? { name: def.name, kind: "bundle", status: "missed", reason: `no chunk with stem ${stems.join("|") || "?"} is referenced by the shell`, chunk: null });
      }
    }
    return rows;
  }

  return Object.freeze({
    /** Surface names in registry order. */
    names: () => defs.map((d) => d.name),
    /** The registry entries (frozen copies). */
    definitions: () => defs.map((d) => Object.freeze({ ...d })),
    /** Make sure the registry is loaded (the shipped `surfaces.json` when none was injected). */
    load: ensureDefinitions,
    /** The live node of a surface, or `null` (missed, not on this route, unknown name). */
    get(name) {
      if (!resolved.has(name)) resolveAll();
      return resolved.get(name)?.node ?? null;
    },
    /** `{status, node, reason}` for one surface. */
    resolve(name) {
      const def = defs.find((d) => d.name === name);
      if (!def) return { status: "missed", node: null, reason: `unknown surface ${name}` };
      const result = resolveDom(def.dom, doc, pathname());
      resolved.set(name, result);
      return result;
    },
    refresh,
    check,
    chunks: () => chunksFromDocument(doc),
    /** Observe the document and the route so surfaces are re-resolved as the host re-renders. */
    start() {
      void ensureDefinitions().then(() => refresh("start"));
      bus.on("route.changed", () => scheduleRefresh("route"));
      if (doc?.body && typeof win?.MutationObserver === "function" && !observer) {
        observer = new win.MutationObserver(() => scheduleRefresh("mutation"));
        observer.observe(doc.body, { childList: true, subtree: true });
      }
    },
    stop() {
      observer?.disconnect?.();
      observer = null;
    },
  });
}
