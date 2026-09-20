// `floofy.mod(id)` (spa-host/src/modapi.mjs; Requirement 16.4) under plain `node --test` with a fake Loader.
import assert from "node:assert/strict";
import { test } from "node:test";

import { API_BASE, createModApi, declaredTokenNames } from "../src/modapi.mjs";

function fakeLoader(config = {}) {
  const calls = [];
  const state = { mods: { pagey: { id: "pagey", active: true, routes: [{ method: "POST", path: "echo" }], exports: {}, tier: "unlisted" } } };
  const fetchImpl = async (url, init = {}) => {
    calls.push({ url, method: init.method || "GET", body: init.body ? JSON.parse(init.body) : undefined, credentials: init.credentials });
    const reply = (status, body) => ({ ok: status < 300, status, json: async () => body });
    if (url === `${API_BASE}/state`) return reply(200, state);
    if (url === `${API_BASE}/mods/pagey/config` && (init.method || "GET") === "GET") return reply(200, { ok: true, mod: "pagey", config });
    if (url === `${API_BASE}/mods/pagey/config` && init.method === "PUT") {
      for (const [key, value] of Object.entries(JSON.parse(init.body).patch)) {
        if (value === null) delete config[key];
        else config[key] = value;
      }
      return reply(200, { ok: true, mod: "pagey", config });
    }
    if (url.startsWith(`${API_BASE}/mods/pagey/api/`)) return reply(200, { echoed: url.slice(`${API_BASE}/mods/pagey/api/`.length), body: init.body ? JSON.parse(init.body) : null });
    return reply(404, { ok: false, error: "nope" });
  };
  return { calls, fetchImpl, state };
}

test("config.get/set go to the mod's config route; null deletes", async () => {
  const loader = fakeLoader({ greeting: "hi" });
  const api = createModApi("pagey", { fetch: loader.fetchImpl });
  assert.deepEqual(await api.config.get(), { greeting: "hi" });
  assert.deepEqual(await api.config.set({ toggle: true, greeting: null }), { toggle: true });
  assert.equal(loader.calls[1].method, "PUT");
  assert.deepEqual(loader.calls[1].body, { patch: { toggle: true, greeting: null } });
  assert.ok(loader.calls.every((c) => c.credentials === "same-origin"));
  await assert.rejects(() => api.config.set("nope"), /patch must be an object/);
  assert.equal(api.baseUrl, `${API_BASE}/ui/mods/pagey/`);
  assert.equal(Object.isFrozen(api), true);
});

test("routes.list reads the Loader state; routes.fetch stays inside the mod's own api prefix", async () => {
  const loader = fakeLoader();
  const api = createModApi("pagey", { fetch: loader.fetchImpl });
  assert.deepEqual(await api.routes.list(), [{ method: "POST", path: "echo" }]);
  const response = await api.routes.fetch("/echo/", { method: "POST", body: JSON.stringify({ n: 1 }) });
  assert.deepEqual(await response.json(), { echoed: "echo", body: { n: 1 } });
  assert.equal(api.routes.url("items/3"), `${API_BASE}/mods/pagey/api/items/3`);
  assert.equal(api.routes.url("a b/c"), `${API_BASE}/mods/pagey/api/a%20b/c`);
  assert.throws(() => api.routes.fetch("https://evil.example/x"), /relative route path is required/);
  assert.throws(() => api.routes.fetch(""), /relative route path is required/);
  const state = await api.state();
  assert.equal(state.active, true);
});

test("theme.tokens reads :root custom properties; theme.current reads the host's markers", () => {
  const fakeDocument = {
    documentElement: { getAttribute: (name) => ({ "data-theme": "custom-x-dark", "data-mode": "dark" })[name] ?? null },
    styleSheets: [{ cssRules: [{ selectorText: ":root", style: { length: 2, 0: "--bg", 1: "--special" } }, { selectorText: ".x", style: { length: 1, 0: "--ignored" } }] }],
  };
  const fakeWindow = { getComputedStyle: () => ({ getPropertyValue: (name) => ({ "--bg": " #101418 ", "--special": "1px", "--accent": "#ff6b8b" })[name] ?? "" }), localStorage: { getItem: (key) => (key === "mc-color-theme" ? "custom-x" : null) } };
  const api = createModApi("pagey", { fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }), document: fakeDocument, window: fakeWindow });
  assert.ok(declaredTokenNames(fakeDocument).includes("--special") && !declaredTokenNames(fakeDocument).includes("--ignored"));
  assert.deepEqual(api.theme.tokens(), { "--accent": "#ff6b8b", "--bg": "#101418", "--special": "1px" });
  assert.deepEqual(api.theme.current(), { theme: "custom-x", mode: "dark" });
});

test("a bad mod id is refused up front", () => {
  assert.throws(() => createModApi("../etc"), /not a mod id/);
  assert.throws(() => createModApi(""), /not a mod id/);
});
