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
// naming the host build it was written against — a record, not a gate; an
// optional `toBuild` is the gate: the first full host version the surface no
// longer exists on, exclusive, the same field the patch descriptors carry, so a
// site the host removed reports `not-applicable` there instead of a miss).
// Nothing here rewrites the document; this is a locator and a reporter input.
"use strict";

// --- host versions ------------------------------------------------------------------
//
// Mirrors `floofy_core.semver.HostVersion` (kept in lock-step with the Python
// evaluator): `X.Y.Z`, an optional PEP 440 pre-release (`rc1`), an optional
// internal build number (`.5`), an optional `.devN`, an optional `-tag`;
// ordering dev < a < b < rc < tagged < final, then the build number.
const HOST_VERSION_RE = /^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?(?:\.(\d+))?(?:\.dev(\d+))?(?:-([0-9A-Za-z][0-9A-Za-z.-]*))?(?:\+[0-9A-Za-z][0-9A-Za-z.-]*)?$/;
const PHASE_RANK = { dev: 0, a: 1, b: 2, rc: 3, tag: 4, final: 5 };

/**
 * Parse a host version into a comparable key, or `null` when the text is not a host version.
 * @param {unknown} text
 * @returns {number[]|null}
 */
export function hostVersionKey(text) {
  if (typeof text !== "string") return null;
  const match = HOST_VERSION_RE.exec(text.trim());
  if (!match) return null;
  const [, major, minor, patch, preTag, preNum, build, dev, tag] = match;
  let phase = PHASE_RANK.final;
  let number = 0;
  if (dev !== undefined) [phase, number] = [PHASE_RANK.dev, Number(dev)];
  else if (preTag !== undefined) [phase, number] = [PHASE_RANK[preTag], Number(preNum)];
  else if (tag !== undefined) phase = PHASE_RANK.tag;
  // SemVer §11.4 for the tag identifiers: numeric ones sort numerically and below alphanumeric ones
  const tagKey = tag === undefined ? [] : tag.split(".").flatMap((part) => (/^(0|[1-9]\d*)$/.test(part) ? [0, Number(part), ""] : [1, 0, part]));
  return [Number(major), Number(minor), Number(patch), phase, number, tagKey, Number(build ?? 0)];
}

/** `-1`, `0` or `1` for two host version strings; `null` when either does not parse. */
export function compareHostVersions(left, right) {
  const a = hostVersionKey(left);
  const b = hostVersionKey(right);
  if (!a || !b) return null;
  const cmp = (x, y) => {
    if (Array.isArray(x) && Array.isArray(y)) {
      for (let i = 0; i < Math.max(x.length, y.length); i += 1) {
        if (i >= x.length) return -1;
        if (i >= y.length) return 1;
        const inner = cmp(x[i], y[i]);
        if (inner !== 0) return inner;
      }
      return 0;
    }
    if (x === y) return 0;
    return x < y ? -1 : 1;
  };
  return cmp(a, b);
}

/**
 * Whether a surface row applies to `hostVersion`: `null` when it does, else the reason.
 * `toBuild` is exclusive on the full host version (Vencord-style, as on patch descriptors);
 * an unknown host version or an unparsable gate never excludes a row (the miss would then be a
 * real miss to look at, not a silent skip).
 */
export function surfaceApplicability(def, hostVersion) {
  const toBuild = def?.toBuild;
  if (typeof toBuild !== "string" || !toBuild) return null;
  const order = compareHostVersions(hostVersion, toBuild);
  if (order === null) return null;
  return order < 0 ? null : `host ${hostVersion} is not below toBuild ${toBuild}`;
}

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
 * @param {() => (string|null|undefined)} [options.hostVersion] the running host's version (for `toBuild` gates)
 */
export function createSurfaces({ document: doc, window: win, bus, definitions, fetch: fetchImpl, debounce, hostVersion }) {
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
    const version = typeof hostVersion === "function" ? hostVersion() : null;
    let changed = false;
    for (const def of defs) {
      const outside = surfaceApplicability(def, version);
      const next = outside ? { status: "not-applicable", node: null, reason: outside } : resolveDom(def.dom, doc, now);
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
    const version = typeof hostVersion === "function" ? hostVersion() : null;
    for (const def of defs) {
      // a row the host has since removed (`toBuild`) is not-applicable here, for dom and bundle alike
      const outside = surfaceApplicability(def, version);
      if (outside) {
        if (def.dom) rows.push({ name: def.name, kind: "dom", status: "not-applicable", reason: outside });
        if (def.bundle) rows.push({ name: def.name, kind: "bundle", status: "not-applicable", reason: outside, chunk: null });
        continue;
      }
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
