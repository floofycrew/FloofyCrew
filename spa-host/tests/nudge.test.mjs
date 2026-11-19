// The update nudge (spa-host/src/nudge.mjs) under plain `node --test` with a minimal fake DOM: what is due, the
// host-shaped card, "Remind me tomorrow" / "Skip" / the primary action, Escape and the outside click, the App-page
// hold, the modal hold, and the localStorage record with the host's rules.
import assert from "node:assert/strict";
import { test } from "node:test";

import { APP_PATH, NUDGE_STORAGE_KEY, SNOOZE_SECONDS, createUpdateNudge, dueItems, isDue, readRecords, updateItems, writeRecords } from "../src/nudge.mjs";

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.style = {};
    this.attributes = {};
    this.children = [];
    this.listeners = {};
    this.textContent = "";
    this.innerHTML = "";
    this.parent = null;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  append(...nodes) {
    for (const node of nodes) {
      if (typeof node === "string") node = { text: node, walk: function* () {} };
      node.parent = this;
      this.children.push(node);
    }
  }
  remove() {
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  addEventListener(name, fn) {
    (this.listeners[name] ||= []).push(fn);
  }
  fire(name, event = {}) {
    for (const fn of this.listeners[name] || []) fn({ target: this, preventDefault() {}, stopPropagation() {}, ...event });
  }
  focus() {
    this.focused = true;
  }
  *walk() {
    yield this;
    for (const child of this.children) if (child.walk) yield* child.walk();
  }
  find(testid) {
    for (const node of this.walk()) if (node.attributes?.["data-testid"] === testid) return node;
    return null;
  }
  text() {
    let out = this.textContent || "";
    for (const child of this.children) out += child.text ? (typeof child.text === "function" ? child.text() : child.text) : "";
    return out;
  }
  get className() {
    return this.attributes.class ?? "";
  }
}

function fakeDocument({ dialogs = 0 } = {}) {
  const body = new FakeElement("body");
  const listeners = {};
  return {
    body,
    dialogs,
    createElement: (tag) => new FakeElement(tag),
    createTextNode: (text) => ({ text, walk: function* () {} }),
    querySelectorAll(selector) {
      assert.match(selector, /role="dialog"/);
      return new Array(this.dialogs).fill({});
    },
    addEventListener(name, fn) {
      (listeners[name] ||= []).push(fn);
    },
    removeEventListener(name, fn) {
      listeners[name] = (listeners[name] || []).filter((f) => f !== fn);
    },
    key(key) {
      for (const fn of [...(listeners.keydown || [])]) fn({ key, preventDefault() {}, stopPropagation() {} });
    },
    overlay: () => body.find("floofycrew-update-nudge"),
  };
}

function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return { getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => map.set(k, v), dump: () => JSON.parse(map.get(NUDGE_STORAGE_KEY) ?? "{}") };
}

const STATE = {
  loaderVersion: "1.3.17",
  mods: { "custom-themes": { name: "Custom Themes" }, "display-comfort": { name: "Display Comfort" } },
  selfUpdate: { available: true, version: "1.3.18", running: "1.3.17", notes: "https://example.invalid/releases/v1.3.18" },
};
const REGISTRY = {
  updates: {
    "display-comfort": { installed: "1.0.1", candidate: "1.0.2", update: true, verdict: "tested", changelog: "https://example.invalid/dc" },
    "custom-themes": { installed: "1.0.3", candidate: "1.0.4", update: true, verdict: "expected", changelog: null, repo: "https://example.invalid/ct" },
    "settings-demo": { installed: "1.0.1", candidate: "1.0.1", update: false },
  },
};

function fetchFor(registry) {
  return async (url) => {
    assert.equal(url, "/api/apps/floofycrew/registry");
    if (registry === "fail") throw new Error("offline");
    return { ok: registry !== null, status: registry === null ? 404 : 200, json: async () => registry };
  };
}

function build({ state = STATE, registry = REGISTRY, storage = fakeStorage(), path = "/", dialogs = 0, now = 1_000_000, timers = [] } = {}) {
  const doc = fakeDocument({ dialogs });
  const navigated = [];
  const win = { location: { pathname: path } };
  const warnings = [];
  const nudge = createUpdateNudge({
    document: doc,
    window: win,
    fetch: fetchFor(registry),
    storage,
    now: () => now,
    state: async () => state,
    setTimeout: (fn, ms) => timers.push({ fn, ms }),
    navigate: (to) => navigated.push(to),
    console: { warn: (...a) => warnings.push(a), debug() {} },
    delayMs: 1,
  });
  return { nudge, doc, win, storage, navigated, timers, warnings };
}

test("updateItems lists the FloofyCrew release first, then every mod with a newer candidate, sorted", () => {
  const items = updateItems(STATE, REGISTRY);
  assert.deepEqual(items.map((i) => i.key), ["floofycrew@1.3.18", "mod:custom-themes@1.0.4", "mod:display-comfort@1.0.2"]);
  assert.equal(items[0].running, "1.3.17");
  assert.equal(items[1].name, "Custom Themes");
  assert.equal(items[1].notes, "https://example.invalid/ct", "a candidate without a changelog links its repository");
  assert.deepEqual(updateItems({ selfUpdate: { available: false } }, null), []);
  assert.deepEqual(updateItems(null, { updates: { x: { update: true } } }), [], "an update without a candidate version is not an item");
});

test("isDue follows the host's rule: no record, or neither skipped nor snoozed into the future", () => {
  assert.equal(isDue(undefined, 10), true);
  assert.equal(isDue({ snoozed_until: 20, skipped: false }, 10), false);
  assert.equal(isDue({ snoozed_until: 10, skipped: false }, 10), true);
  assert.equal(isDue({ snoozed_until: 0, skipped: true }, 10), false);
  assert.equal(isDue("garbage", 10), true);
});

test("records survive a broken store and drop the keys of updates no longer announced", () => {
  assert.deepEqual(readRecords({ getItem: () => "{not json" }), {});
  assert.deepEqual(readRecords({ getItem: () => "[1,2]" }), {});
  assert.deepEqual(readRecords(null), {});
  const storage = fakeStorage();
  const kept = writeRecords(storage, { a: { skipped: true }, b: { snoozed_until: 5 } }, ["a"]);
  assert.deepEqual(kept, { a: { skipped: true } });
  assert.deepEqual(storage.dump(), { a: { skipped: true } });
  assert.deepEqual(dueItems([{ key: "a" }, { key: "b" }], { a: { skipped: true } }, 1).map((i) => i.key), ["b"]);
});

test("the card is rendered in the host's update-modal shape and names the release and the mods", async () => {
  const { nudge, doc } = build();
  const shown = await nudge.show();
  assert.deepEqual(shown.map((i) => i.key), ["floofycrew@1.3.18", "mod:custom-themes@1.0.4", "mod:display-comfort@1.0.2"]);
  const overlay = doc.overlay();
  assert.ok(overlay, "the overlay is in the body");
  assert.match(overlay.className, /fixed inset-0 z-\[65\]/);
  const card = overlay.find("floofycrew-update-nudge-card");
  assert.equal(card.attributes.role, "dialog");
  assert.equal(card.attributes["aria-modal"], "true");
  assert.match(card.className, /bg-card border border-border rounded-xl shadow-xl w-\[460px\]/);
  assert.equal(card.style.position, undefined, "the card itself is not positioned; the overlay centres it");
  assert.equal(overlay.style.position, "fixed", "inline fallback keeps the overlay fixed should the classes vanish");
  assert.equal(overlay.find("floofycrew-update-nudge-title").textContent, "FloofyCrew and mod updates available");
  assert.match(overlay.find("floofycrew-update-nudge-release").text(), /FloofyCrew 1\.3\.18 is available for this host \(you run 1\.3\.17\)/);
  assert.match(overlay.find("floofycrew-update-nudge-mods-intro").textContent, /^2 installed mods/);
  assert.match(overlay.find("floofycrew-update-nudge-mod-custom-themes").text(), /Custom Themes 1\.0\.3 → 1\.0\.4 \(expected\)/);
  assert.equal(overlay.find("floofycrew-update-nudge-open").textContent, "Update to 1.3.18 in FloofyCrew");
  assert.match(overlay.find("floofycrew-update-nudge-open").className, /bg-accent text-accent-fg/);
  assert.equal(overlay.find("floofycrew-update-nudge-later").textContent, "Remind me tomorrow");
  assert.equal(overlay.find("floofycrew-update-nudge-skip").textContent, "Skip these versions");
  assert.ok(card.focused, "focus moves into the dialog");
  // only mods: the headline and the primary change
  const modsOnly = build({ state: { ...STATE, selfUpdate: { available: false } }, registry: { updates: { "display-comfort": REGISTRY.updates["display-comfort"] } } });
  await modsOnly.nudge.show();
  assert.equal(modsOnly.doc.overlay().find("floofycrew-update-nudge-title").textContent, "Mod update available");
  assert.equal(modsOnly.doc.overlay().find("floofycrew-update-nudge-open").textContent, "Open FloofyCrew");
  assert.equal(modsOnly.doc.overlay().find("floofycrew-update-nudge-skip").textContent, "Skip this version");
  assert.equal(modsOnly.doc.overlay().find("floofycrew-update-nudge-release"), null);
});

test("Remind me tomorrow snoozes every shown item for a day; Escape and the outside click do the same", async () => {
  const now = 1_700_000_000;
  let { nudge, doc, storage } = build({ now });
  await nudge.show();
  doc.overlay().find("floofycrew-update-nudge-later").fire("click");
  assert.equal(doc.overlay(), null, "closed");
  assert.equal(nudge.isOpen(), false);
  const snoozed = { snoozed_until: now + SNOOZE_SECONDS, skipped: false };
  assert.deepEqual(storage.dump(), { "floofycrew@1.3.18": snoozed, "mod:custom-themes@1.0.4": snoozed, "mod:display-comfort@1.0.2": snoozed });
  assert.deepEqual(await nudge.show(), [], "nothing is due any more today");
  assert.equal(doc.overlay(), null);
  // tomorrow it is due again
  const later = build({ storage, now: now + SNOOZE_SECONDS });
  assert.equal((await later.nudge.show()).length, 3);
  // Escape
  later.doc.key("Escape");
  assert.equal(later.doc.overlay(), null);
  // the outside click (the overlay itself, not the card)
  const third = build({ storage, now: now + 2 * SNOOZE_SECONDS });
  await third.nudge.show();
  const overlay = third.doc.overlay();
  overlay.find("floofycrew-update-nudge-card").fire("click");
  assert.ok(third.doc.overlay(), "a click inside the card is not a dismissal");
  overlay.fire("click", { target: overlay });
  assert.equal(third.doc.overlay(), null);
});

test("Skip marks the shown versions skipped for good; a newer version is a new key and shows again", async () => {
  const { nudge, doc, storage } = build();
  await nudge.show();
  doc.overlay().find("floofycrew-update-nudge-skip").fire("click");
  assert.equal(doc.overlay(), null);
  assert.equal(storage.dump()["floofycrew@1.3.18"].skipped, true);
  const again = build({ storage, now: 9_999_999_999 });
  assert.deepEqual(await again.nudge.show(), [], "skipped stays skipped however much time passes");
  const newer = build({ storage, state: { ...STATE, selfUpdate: { ...STATE.selfUpdate, version: "1.3.19" } }, registry: { updates: {} } });
  const shown = await newer.nudge.show();
  assert.deepEqual(shown.map((i) => i.key), ["floofycrew@1.3.19"]);
  newer.doc.overlay().find("floofycrew-update-nudge-later").fire("click");
  assert.deepEqual(Object.keys(newer.storage.dump()), ["floofycrew@1.3.19"], "a decision prunes the records to the versions still announced: the skipped 1.3.18 and the installed mods are gone");
});

test("the primary action opens the FloofyCrew app's Mods page and installs nothing", async () => {
  const { nudge, doc, navigated, storage } = build();
  await nudge.show();
  doc.overlay().find("floofycrew-update-nudge-open").fire("click");
  assert.deepEqual(navigated, [`${APP_PATH}#/mods`]);
  assert.equal(doc.overlay(), null);
  assert.deepEqual(storage.dump(), {}, "opening the App records nothing: the notice is on that page now");
  const anytime = build();
  await anytime.nudge.show();
  anytime.doc.overlay().find("floofycrew-update-nudge-anytime").fire("click");
  assert.deepEqual(anytime.navigated, [`${APP_PATH}#/mods`]);
  assert.equal(anytime.storage.dump()["floofycrew@1.3.18"].skipped, false, "'update anytime' is a snooze plus the App");
});

test("nothing shows on the App page, over another modal, when nothing is due, or without a Loader state", async () => {
  const onApp = build({ path: APP_PATH });
  assert.deepEqual(await onApp.nudge.show(), []);
  const subpage = build({ path: `${APP_PATH}/x` });
  assert.deepEqual(await subpage.nudge.show(), []);
  const busy = build({ dialogs: 1 });
  assert.deepEqual(await busy.nudge.show(), []);
  assert.equal(busy.timers.length, 1, "it waits for the other modal and tries again later");
  busy.doc.dialogs = 0;
  busy.timers[0].fn();
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(busy.doc.overlay(), "shown once the other modal is gone");
  const upToDate = build({ state: { ...STATE, selfUpdate: { available: false } }, registry: { updates: {} } });
  assert.deepEqual(await upToDate.nudge.show(), []);
  const noState = build({ state: null });
  assert.deepEqual(await noState.nudge.show(), []);
  const noRegistry = build({ registry: "fail" });
  assert.deepEqual((await noRegistry.nudge.show()).map((i) => i.key), ["floofycrew@1.3.18"], "an unreachable /registry still announces the release");
});

test("start arms once per document: after the delay, or when the user leaves the App page", async () => {
  const timers = [];
  const { nudge, doc } = build({ timers });
  nudge.start();
  nudge.start();
  assert.equal(timers.length, 1, "armed once");
  timers[0].fn();
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(doc.overlay());
  // on the App page: wait for route.changed off the page
  const handlers = [];
  const bus = { on: (name, fn, owner) => handlers.push({ name, fn, owner }) };
  const onApp = build({ path: APP_PATH, timers: [] });
  const armed = createUpdateNudge({ document: onApp.doc, window: onApp.win, fetch: fetchFor(REGISTRY), storage: fakeStorage(), now: () => 1, state: async () => STATE, setTimeout: (fn) => fn(), navigate() {}, bus, console: { warn() {}, debug() {} }, delayMs: 1 });
  armed.start();
  assert.equal(handlers.length, 1);
  assert.equal(handlers[0].name, "route.changed");
  assert.equal(onApp.doc.overlay(), null, "nothing while on the App page");
  onApp.win.location.pathname = "/";
  handlers[0].fn();
  await new Promise((r) => setTimeout(r, 0));
  assert.ok(onApp.doc.overlay(), "shown once the user left the App page");
});
