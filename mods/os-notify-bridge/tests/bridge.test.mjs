// os-notify-bridge — node --test suite for the bridge module (no browser).
//
// Pure helpers are tested directly; activate() runs against a fake window
// (Notification, document, fetch, localStorage, floofy) so the gating rules —
// master switch, per-kind switches, background-only, permission, rate limit,
// turn delegation — are pinned without a dashboard.
"use strict";

import test from "node:test";
import assert from "node:assert/strict";

import { DEFAULT_SETTINGS, KNOWN_KINDS, activate, cleanText, mergeSettings, pickNote, tagFor } from "../spa/main.js";

const tick = () => new Promise((resolve) => setImmediate(resolve));

function fakeEnv({ permission = "granted", hidden = true, focused = false, feed = { notifications: [] }, stored = null } = {}) {
  const shown = [];
  class FakeNotification {
    constructor(title, options = {}) {
      shown.push({ title, ...options });
    }
    close() {}
  }
  FakeNotification.permission = permission;
  const storage = new Map();
  const listeners = new Set();
  const win = {
    Notification: FakeNotification,
    document: { hidden, hasFocus: () => focused },
    localStorage: {
      setItem: (key, value) => storage.set(key, String(value)),
      getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    },
    fetch: async () => ({ ok: true, json: async () => feed }),
    addEventListener: (_name, handler) => listeners.add(handler),
    removeEventListener: (_name, handler) => listeners.delete(handler),
    focus: () => {},
    location: { assign: () => {} },
    floofy: { mod: () => ({ config: { get: async () => stored } }) },
  };
  const ctx = { modId: "os-notify-bridge", log: { warn: () => {} }, events: { on: () => () => {} } };
  const dispatch = async (kind) => {
    for (const handler of [...listeners]) handler({ detail: { kind } });
    await tick();
    await tick();
    await tick();
  };
  return { win, ctx, shown, storage, dispatch, listeners };
}

async function activated(overrides) {
  const env = fakeEnv(overrides);
  globalThis.window = env.win;
  const cleanup = activate(env.ctx);
  await tick(); // let the async settings load settle
  return { ...env, cleanup: () => { cleanup(); delete globalThis.window; } };
}

test("mergeSettings: defaults, stored overrides, kind coercion", () => {
  const merged = mergeSettings({ enabled: false, kinds: { cron: 0, extra: 1 } });
  assert.equal(merged.enabled, false);
  assert.equal(merged.onlyBackground, DEFAULT_SETTINGS.onlyBackground);
  assert.equal(merged.kinds.cron, false);
  assert.equal(merged.kinds.extra, true);
  assert.equal(merged.kinds.approval, true); // untouched default survives
  assert.equal(mergeSettings(null).kinds.heartbeat, false); // noisy kind starts off
});

test("cleanText strips markdown noise and caps the length", () => {
  assert.equal(cleanText("**Bold** [link](/notifications) `code`\n\n# head"), "Bold link code head");
  const long = cleanText("y".repeat(500), 100);
  assert.equal(long.length, 100);
  assert.ok(long.endsWith("…"));
  assert.equal(cleanText(undefined), "");
});

test("pickNote takes the newest fresh matching note and rejects stale ones", () => {
  const now = 1000000;
  const feed = {
    notifications: [
      { kind: "cron", title: "old", ts: String(now - 500) },
      { kind: "agent", title: "other", ts: String(now - 1) },
      { kind: "cron", title: "new", ts: String(now - 5) },
    ],
  };
  assert.equal(pickNote(feed, "cron", now).title, "new");
  assert.equal(pickNote({ notifications: [{ kind: "cron", title: "old", ts: String(now - 500) }] }, "cron", now), null);
  assert.equal(pickNote(feed, "resources", now), null);
});

test("tagFor reuses the host's tags so banners collapse", () => {
  assert.equal(tagFor("approval", { approval_id: "ap-1" }), "ap-1");
  assert.equal(tagFor("approval", null), "kirocrew-approval");
  assert.equal(tagFor("cron", { job_id: "job-9" }), "job-9");
  assert.equal(tagFor("cron", null), "floofy-onb:cron");
});

test("a bus note becomes one enriched banner", async () => {
  const now = Date.now() / 1000;
  const env = await activated({ feed: { notifications: [{ kind: "cron", title: "Cron **failed**", body: "job `x` exited 1", ts: String(now), job_id: "j1", url: "/cron" }] } });
  await env.dispatch("cron");
  assert.equal(env.shown.length, 1);
  assert.deepEqual(env.shown[0], { title: "Cron failed", body: "job x exited 1", tag: "j1" });
  env.cleanup();
});

test("gates: permission, master switch, per-kind switch, background-only", async () => {
  for (const overrides of [
    { permission: "default" },
    { stored: { enabled: false } },
    { stored: { kinds: { cron: false } } },
    { hidden: false, focused: true },
  ]) {
    const env = await activated(overrides);
    await env.dispatch("cron");
    assert.equal(env.shown.length, 0, JSON.stringify(overrides));
    env.cleanup();
  }
  // background-only off => banner even while focused
  const env = await activated({ hidden: false, focused: true, stored: { onlyBackground: false } });
  await env.dispatch("cron");
  assert.equal(env.shown.length, 1);
  env.cleanup();
});

test("turn is delegated to the host flag, never bannered by the bridge", async () => {
  const env = await activated({});
  await env.dispatch("turn");
  assert.equal(env.shown.length, 0);
  assert.equal(env.storage.get("mc-notify-chat-complete"), "1"); // switch on => host flag armed
  env.cleanup();
  const off = await activated({ stored: { kinds: { turn: false } } });
  assert.equal(off.storage.get("mc-notify-chat-complete"), "0");
  off.cleanup();
});

test("unknown kinds ride the otherKinds switch", async () => {
  const on = await activated({});
  await on.dispatch("issue_radar");
  assert.equal(on.shown.length, 1); // enriched from an empty feed => generic banner
  assert.equal(on.shown[0].tag, "floofy-onb:issue_radar");
  on.cleanup();
  const off = await activated({ stored: { otherKinds: false } });
  await off.dispatch("issue_radar");
  assert.equal(off.shown.length, 0);
  off.cleanup();
});

test("per-tag rate limit folds a burst into one banner", async () => {
  const env = await activated({});
  await env.dispatch("cron");
  await env.dispatch("cron");
  await env.dispatch("cron");
  assert.equal(env.shown.length, 1);
  env.cleanup();
});

test("cleanup removes the listener", async () => {
  const env = await activated({});
  env.cleanup();
  assert.equal(env.listeners.size, 0);
});

test("approval stays in the known-kind list (the critical channel)", () => {
  assert.ok(KNOWN_KINDS.includes("approval"));
});
