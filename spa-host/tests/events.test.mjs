import assert from "node:assert/strict";
import { test } from "node:test";

import { createBus, scoped } from "../src/events.mjs";

test("emit delivers to name and wildcard subscribers in order", () => {
  const bus = createBus({ log: () => {} });
  const seen = [];
  bus.on("mod.loaded", (name, payload) => seen.push(["named", name, payload.mod]));
  bus.on("*", (name) => seen.push(["star", name]));
  const delivered = bus.emit("mod.loaded", { mod: "a" });
  assert.equal(delivered, 2);
  assert.deepEqual(seen, [["named", "mod.loaded", "a"], ["star", "mod.loaded"]]);
});

test("a throwing subscriber is fail-open: logged, counted, the others still run", () => {
  const logged = [];
  const bus = createBus({ log: (message) => logged.push(message) });
  const seen = [];
  bus.on("x", () => {
    throw new Error("boom");
  }, "bad-mod");
  bus.on("x", () => seen.push("ok"));
  assert.equal(bus.emit("x", 1), 1);
  assert.deepEqual(seen, ["ok"]);
  assert.equal(logged.length, 1);
  assert.match(logged[0], /bad-mod/);
  assert.equal(bus.inspect().errors, 1);
});

test("once, off and dropOwner remove subscriptions", () => {
  const bus = createBus({ log: () => {} });
  let count = 0;
  bus.once("e", () => (count += 1));
  bus.emit("e");
  bus.emit("e");
  assert.equal(count, 1);
  const handler = () => (count += 10);
  bus.on("e", handler);
  bus.off("e", handler);
  bus.on("e", () => (count += 100), "mod-a");
  bus.on("other", () => (count += 1000), "mod-a");
  assert.equal(bus.dropOwner("mod-a"), 2);
  bus.emit("e");
  bus.emit("other");
  assert.equal(count, 1);
});

test("history is bounded and inspect() attributes owners", () => {
  const bus = createBus({ log: () => {}, historyLimit: 3 });
  const mod = scoped(bus, "mod-b");
  mod.on("z", () => {});
  for (let i = 0; i < 5; i += 1) bus.emit("z", i);
  const snapshot = bus.inspect();
  assert.deepEqual(snapshot.history.map((h) => h.payload), [2, 3, 4]);
  assert.deepEqual(snapshot.subscriptions, { z: ["mod-b"] });
});

test("a scoped emit stamps the source mod", () => {
  const bus = createBus({ log: () => {} });
  let payload = null;
  bus.on("custom", (_name, p) => (payload = p));
  scoped(bus, "mod-c").emit("custom", { a: 1 });
  assert.deepEqual(payload, { a: 1, source: "mod-c" });
  scoped(bus, "mod-c").emit("custom", 42);
  assert.deepEqual(payload, { value: 42, source: "mod-c" });
});
