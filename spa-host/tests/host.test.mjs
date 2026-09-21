import assert from "node:assert/strict";
import { test } from "node:test";

import { API_BASE, SPA_PREFIX, createHost, hostFacts, installRouteWatcher, selectRuntimeParts, summarizeMods } from "../src/host.mjs";
import { createBus } from "../src/events.mjs";
import { immediate, makeConsole, makeFetch, makeImporter, makeState, makeWindow } from "./fakes.mjs";

const spaPart = (path, extra = {}) => ({ kind: "spa", path, ...extra });

test("selectRuntimeParts keeps active mods' runtime spa parts in Loader order and skips boot/errored parts", () => {
  const state = makeState([
    { id: "b", parts: [spaPart("spa/main.js"), spaPart("boot/first.js", { activation: "boot" })] },
    { id: "a", parts: [spaPart("ui/a.mjs", { activation: "runtime" }), spaPart("ui/broken.js", { status: "error" })] },
    { id: "off", active: false, parts: [spaPart("spa/x.js")] },
    { id: "py", parts: [{ kind: "python-hook", path: "hook/", side: "gateway" }] },
  ], { order: ["a", "b", "off", "py"] });
  const parts = selectRuntimeParts(state);
  assert.deepEqual(parts.map((p) => [p.modId, p.url]), [
    ["a", `${SPA_PREFIX}a/a.mjs`],
    ["b", `${SPA_PREFIX}b/main.js`],
  ]);
  assert.deepEqual(selectRuntimeParts(null), []);
});

test("hostFacts and summarizeMods are frozen read-only views", () => {
  const state = makeState([{ id: "m", parts: [spaPart("spa/m.js")] }]);
  const facts = hostFacts(state);
  assert.equal(facts.version, "0.7.0.5");
  assert.equal(facts.edition, "internal");
  assert.ok(Object.isFrozen(facts));
  const mods = summarizeMods(state);
  assert.equal(mods[0].id, "m");
  assert.equal(mods[0].parts[0].activation, null, "an unannotated spa part reports no activation");
  assert.ok(Object.isFrozen(mods) && Object.isFrozen(mods[0]));
});

test("start(): reads the state, installs a frozen window.floofy and loads mods in order with a shared ctx shape", async () => {
  const state = makeState([
    { id: "one", parts: [spaPart("spa/one.js")] },
    { id: "two", version: "2.1.0", parts: [spaPart("spa/two.mjs")], exports: { answer: 42 } },
  ]);
  const { fetch, calls } = makeFetch(state);
  const seen = [];
  const { importer, imported } = makeImporter({
    [`${SPA_PREFIX}one/one.js`]: { default: (ctx) => seen.push(["one", ctx.modId, ctx.host.version, typeof ctx.log.info]) },
    [`${SPA_PREFIX}two/two.mjs`]: { activate: async (ctx) => seen.push(["two", ctx.version, await ctx.config.read()]) },
  });
  const win = makeWindow();
  const host = createHost({ fetch, importer, window: win, document: null, console: makeConsole(), setTimeout: immediate });
  const floofy = await host.ensureStarted();
  assert.equal(win.floofy, floofy);
  assert.ok(Object.isFrozen(floofy));
  assert.equal(floofy.unofficial, true);
  assert.equal(floofy.api_version, "1.0.0");
  assert.equal(floofy.host.version, "0.7.0.5");
  assert.deepEqual(imported, [`${SPA_PREFIX}one/one.js`, `${SPA_PREFIX}two/two.mjs`]);
  assert.deepEqual(seen, [["one", "one", "0.7.0.5", "function"], ["two", "2.1.0", { answer: 42 }]]);
  assert.deepEqual(floofy.loaded(), ["one", "two"]);
  assert.deepEqual(floofy.faulted(), []);
  assert.equal(calls[0].url, `${API_BASE}/state`);
  assert.equal(await host.ensureStarted(), floofy, "idempotent");
  assert.throws(() => {
    win.floofy = {};
  }, "window.floofy is not writable");
});

test("a mod failing on import and one failing in activate are reported and isolated; the healthy one runs", async () => {
  const state = makeState([
    { id: "import-fail", parts: [spaPart("spa/x.js")] },
    { id: "activate-fail", parts: [spaPart("spa/y.js")] },
    { id: "healthy", parts: [spaPart("spa/z.js")] },
  ]);
  const { fetch, faults } = makeFetch(state);
  let healthyRan = false;
  const { importer } = makeImporter({
    // import-fail: absent from the map -> the importer rejects like a 404 would
    [`${SPA_PREFIX}activate-fail/y.js`]: {
      activate() {
        throw new Error("activate blew up");
      },
      deactivate() {
        throw new Error("and deactivate too");
      },
    },
    [`${SPA_PREFIX}healthy/z.js`]: { default: () => (healthyRan = true) },
  });
  const win = makeWindow();
  const events = [];
  const host = createHost({ fetch, importer, window: win, document: null, console: makeConsole(), setTimeout: immediate });
  host.bus.on("mod.faulted", (_n, p) => events.push(p.mod));
  const floofy = await host.ensureStarted();
  assert.equal(healthyRan, true);
  assert.deepEqual(floofy.loaded(), ["healthy"]);
  assert.deepEqual(floofy.faulted(), ["import-fail", "activate-fail"]);
  await Promise.resolve();
  assert.deepEqual(faults.map((f) => [f.mod, f.source]), [["import-fail", "spa"], ["activate-fail", "spa"]]);
  assert.match(faults[0].message, /import spa\/x\.js/);
  assert.match(faults[1].message, /activate spa\/y\.js: activate blew up/);
  assert.deepEqual(events, ["import-fail", "activate-fail"]);
});

test("a late uncaught error from a loaded mod is attributed by URL prefix, the mod is deactivated and reported", async () => {
  const state = makeState([{ id: "late", parts: [spaPart("spa/late.js")] }, { id: "other", parts: [spaPart("spa/o.js")] }]);
  const { fetch, faults } = makeFetch(state);
  let deactivated = 0;
  const { importer } = makeImporter({
    [`${SPA_PREFIX}late/late.js`]: { default: () => {}, deactivate: () => (deactivated += 1) },
    [`${SPA_PREFIX}other/o.js`]: { default: () => {} },
  });
  const win = makeWindow();
  const host = createHost({ fetch, importer, window: win, document: null, console: makeConsole(), setTimeout: immediate });
  const floofy = await host.ensureStarted();
  win.dispatch("error", { error: Object.assign(new Error("timer"), { stack: `Error: timer\n at ${SPA_PREFIX}late/timer.js:1:1` }), preventDefault() {} });
  await Promise.resolve();
  assert.equal(deactivated, 1);
  assert.deepEqual(floofy.loaded(), ["other"]);
  assert.deepEqual(faults.map((f) => f.mod), ["late"]);
  assert.match(faults[0].message, /uncaught error/);
});

test("the cold token path: 401 on /state is retried after /health answers, then gives up quietly", async () => {
  const state = makeState([{ id: "m", parts: [spaPart("spa/m.js")] }]);
  const ok = makeFetch(state, { stateFailures: 2 });
  const { importer } = makeImporter({ [`${SPA_PREFIX}m/m.js`]: { default: () => {} } });
  const host = createHost({ fetch: ok.fetch, importer, window: makeWindow(), document: null, console: makeConsole(), setTimeout: immediate, backoff: [1, 1, 1] });
  const floofy = await host.ensureStarted();
  assert.ok(floofy && floofy.loaded().includes("m"));
  assert.equal(ok.calls.filter((c) => c.url.endsWith("/state")).length, 3);

  const never = makeFetch(state, { stateFailures: 99 });
  const console2 = makeConsole();
  const win = makeWindow();
  const idle = createHost({ fetch: never.fetch, importer, window: win, document: null, console: console2, setTimeout: immediate, backoff: [1, 1] });
  assert.equal(await idle.ensureStarted(), null);
  assert.equal(win.floofy, undefined);
  assert.equal(win.__floofyHostStatus, "unavailable");
  assert.equal(console2.lines.filter((l) => l.level === "error").length, 0, "giving up is quiet");
});

test("installRouteWatcher emits route.changed on pushState/replaceState/popstate, once per window", () => {
  const win = makeWindow();
  const bus = createBus({ log: () => {} });
  const routes = [];
  bus.on("route.changed", (_n, p) => routes.push([p.path, p.how]));
  installRouteWatcher(win, bus);
  installRouteWatcher(win, bus);
  win.history.pushState({}, "", "/apps");
  win.history.replaceState({}, "", "/settings");
  win.dispatch("popstate", {});
  assert.deepEqual(routes, [["/apps", "pushState"], ["/settings", "replaceState"], ["/settings", "popstate"]]);
});
