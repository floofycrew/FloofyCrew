import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { BOOT_CSS_ID_PREFIX, BOOT_SCRIPT_ID, floofyBoot } from "../src/boot.mjs";

const SOURCE = readFileSync(new URL("../src/boot.mjs", import.meta.url), "utf8");

/** A minimal fake document: <html> with attributes and style, a <head> that appends nodes, ids. */
function fakeDom() {
  const byId = new Map();
  const makeElement = (tagName) => {
    const attrs = new Map();
    const styleProps = new Map();
    const node = {
      tagName,
      nodeType: 1,
      children: [],
      parentNode: null,
      get id() {
        return attrs.get("id") ?? "";
      },
      set id(value) {
        attrs.set("id", value);
        byId.set(value, node);
      },
      set rel(value) {
        attrs.set("rel", value);
      },
      get rel() {
        return attrs.get("rel");
      },
      set href(value) {
        attrs.set("href", value);
      },
      get href() {
        return attrs.get("href");
      },
      setAttribute: (name, value) => attrs.set(name, String(value)),
      getAttribute: (name) => (attrs.has(name) ? attrs.get(name) : null),
      appendChild(child) {
        node.children.push(child);
        child.parentNode = node;
        return child;
      },
      removeChild(child) {
        node.children = node.children.filter((c) => c !== child);
        child.parentNode = null;
      },
      style: { setProperty: (k, v) => styleProps.set(k, v), getPropertyValue: (k) => styleProps.get(k) ?? "" },
      attrs,
    };
    return node;
  };
  const html = makeElement("HTML");
  const head = makeElement("HEAD");
  html.appendChild(head);
  const doc = { documentElement: html, head, title: "Kiro Crew", createElement: makeElement, getElementById: (id) => byId.get(id) ?? null };
  return { doc, html, head, byId };
}

/** Install globals the template expects; returns a restore function plus hooks to drive observers. */
function installGlobals({ storage = {}, prefersDark = true } = {}) {
  const { doc, html, head, byId } = fakeDom();
  const observers = [];
  const timers = [];
  const saved = {};
  const globals = {
    document: doc,
    localStorage: { getItem: (key) => (key in storage ? storage[key] : null) },
    matchMedia: () => ({ matches: prefersDark }),
    MutationObserver: class {
      constructor(callback) {
        this.callback = callback;
        this.disconnected = false;
        observers.push(this);
      }
      observe(target, options) {
        this.target = target;
        this.options = options;
      }
      disconnect() {
        this.disconnected = true;
      }
    },
    setTimeout: (fn, ms) => {
      timers.push({ fn, ms });
      return timers.length;
    },
    window: null,
  };
  globals.window = { matchMedia: globals.matchMedia };
  for (const [key, value] of Object.entries(globals)) {
    saved[key] = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
  }
  return {
    doc,
    html,
    head,
    byId,
    observers,
    timers,
    window: globals.window,
    restore() {
      for (const [key, descriptor] of Object.entries(saved)) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor);
        else delete globalThis[key];
      }
    },
  };
}

test("the template stays inline-safe ES5 between its markers", () => {
  const start = SOURCE.indexOf("/* floofy-boot-template-start */");
  const end = SOURCE.indexOf("/* floofy-boot-template-end */");
  assert.ok(start > 0 && end > start);
  const template = SOURCE.slice(start, end);
  assert.ok(!template.includes("`"), "no template literals inside the inline script");
  assert.ok(!/<\/script/i.test(template));
  assert.ok(!/\b(let|const)\b/.test(template), "ES5 only: var, no let/const");
  assert.ok(!/=>/.test(template), "ES5 only: no arrow functions");
  assert.equal(BOOT_SCRIPT_ID, "floofy-boot");
  assert.equal(BOOT_CSS_ID_PREFIX, "floofy-boot-css-");
});

test("theme bootstrap mirrors the host formula from localStorage, with the config fallback for a cold client", () => {
  const env = installGlobals({ storage: {}, prefersDark: false });
  try {
    floofyBoot({ defaults: { color: "custom-rimuru", mode: "system" }, mods: [] });
    assert.equal(env.html.getAttribute("data-theme"), "custom-rimuru-light");
    assert.equal(env.html.getAttribute("data-mode"), "light");
    assert.equal(env.html.getAttribute("data-mode-pref"), "system");
    assert.deepEqual(env.window.__floofyBoot.applied, []);
  } finally {
    env.restore();
  }
  const warm = installGlobals({ storage: { "mc-color-theme": "emerald", "mc-theme": "dark" } });
  try {
    floofyBoot({ defaults: { color: "kiro", mode: "system" }, mods: [] });
    assert.equal(warm.html.getAttribute("data-theme"), "dark", "emerald is the host's bare-mode theme");
  } finally {
    warm.restore();
  }
});

test("mods compose in order; when.theme gates favicon/logo/title/attributes; hooks run fail-open", () => {
  const env = installGlobals({ storage: { "mc-color-theme": "custom-alpha", "mc-theme": "dark" } });
  try {
    floofyBoot({
      defaults: { color: "kiro", mode: "system" },
      mods: [
        { id: "alpha", when: { theme: "custom-alpha" }, favicon: "/apps/floofycrew/ui/boot/alpha/favicon.png", logo: "/apps/floofycrew/ui/boot/alpha/logo.png", title: "Alpha", attributes: { "data-alpha": "on" }, hook: (ctx) => ctx.element.setAttribute("data-hooked", ctx.id) },
        { id: "beta", when: { theme: "custom-beta" }, favicon: "/x.png", title: "Beta" },
        { id: "gamma", hook: () => { throw new Error("gamma hook broke"); } },
        { id: "delta", title: "Delta" },
      ],
    });
    const state = env.window.__floofyBoot;
    assert.deepEqual(state.applied, ["alpha", "delta"]);
    assert.deepEqual(state.skipped, ["beta"]);
    assert.equal(state.errors.length, 1);
    assert.match(state.errors[0], /gamma hook broke/);
    assert.equal(env.byId.get("floofy-boot-favicon-alpha").href, "/apps/floofycrew/ui/boot/alpha/favicon.png");
    assert.equal(env.byId.get("floofy-boot-favicon-beta"), undefined);
    assert.equal(env.html.style.getPropertyValue("--theme-logo"), "url('/apps/floofycrew/ui/boot/alpha/logo.png')");
    assert.equal(env.doc.title, "Delta", "later mods win the title");
    assert.equal(env.html.getAttribute("data-alpha"), "on");
    assert.equal(env.html.getAttribute("data-hooked"), "alpha");
  } finally {
    env.restore();
  }
});

test("drift guard re-asserts attributes until the host settles, then drops the boot favicon", () => {
  const env = installGlobals({ storage: { "mc-color-theme": "custom-alpha" } });
  try {
    floofyBoot({ defaults: {}, mods: [{ id: "alpha", favicon: "/f.png", attributes: { "data-alpha": "on" } }] });
    const [headObserver, attrObserver] = env.observers;
    assert.equal(headObserver.target, env.head);
    assert.equal(attrObserver.target, env.html);
    env.html.setAttribute("data-alpha", "off");
    attrObserver.callback([]);
    assert.equal(env.html.getAttribute("data-alpha"), "on", "re-asserted before the host settled");
    // the host's own favicon lands: settled
    const hostLink = env.doc.createElement("LINK");
    hostLink.id = "mc-theme-favicon";
    env.head.appendChild(hostLink);
    headObserver.callback([{ addedNodes: [hostLink] }]);
    assert.equal(env.window.__floofyBoot.settled, true);
    assert.equal(env.byId.get("floofy-boot-favicon-alpha").parentNode, null, "the boot favicon is removed once the host's is present");
    env.html.setAttribute("data-alpha", "off");
    attrObserver.callback([]);
    assert.equal(env.html.getAttribute("data-alpha"), "off", "after settling the host owns the attribute");
    assert.ok(headObserver.disconnected && attrObserver.disconnected);
    assert.equal(env.timers[0].ms, 5000, "a timeout settles too");
  } finally {
    env.restore();
  }
});

test("everything is fail-open: a broken environment leaves a note, never throws", () => {
  const saved = Object.getOwnPropertyDescriptor(globalThis, "document");
  Object.defineProperty(globalThis, "document", { value: null, configurable: true, writable: true });
  const savedWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", { value: {}, configurable: true, writable: true });
  try {
    assert.doesNotThrow(() => floofyBoot({ mods: [] }));
    assert.match(globalThis.window.__floofyBootError, /documentElement|null/);
  } finally {
    if (saved) Object.defineProperty(globalThis, "document", saved);
    else delete globalThis.document;
    if (savedWindow) Object.defineProperty(globalThis, "window", savedWindow);
    else delete globalThis.window;
  }
});
