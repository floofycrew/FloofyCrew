import assert from "node:assert/strict";
import { test } from "node:test";

import { attributeError, createBoundary, describeError, errorText } from "../src/boundary.mjs";
import { makeWindow } from "./fakes.mjs";

const PREFIX = (id) => `/api/apps/floofycrew/spa/${id}/`;

test("attributeError picks the mod whose served-file prefix appears first in the stack", () => {
  const error = new Error("x");
  error.stack = `Error: x\n    at hop (http://127.0.0.1:1/api/apps/floofycrew/spa/mod-b/lib.js:1:1)\n    at run (http://127.0.0.1:1/api/apps/floofycrew/spa/mod-a/main.js:2:2)`;
  const prefixes = new Map([["mod-a", PREFIX("mod-a")], ["mod-b", PREFIX("mod-b")]]);
  assert.equal(attributeError(error, prefixes), "mod-b");
  assert.equal(attributeError(new Error("host code"), prefixes), null);
  assert.equal(attributeError({ filename: `http://x${PREFIX("mod-a")}main.js`, error: null }, prefixes), "mod-a");
  assert.equal(attributeError(null, prefixes), null);
});

test("errorText and describeError cope with ErrorEvent-like shapes, rejections and non-errors", () => {
  assert.equal(errorText("plain"), "plain");
  assert.match(errorText({ reason: new Error("rejected") }), /rejected/);
  assert.deepEqual(describeError(undefined), { message: "SPA module failed", stack: "" });
  assert.deepEqual(describeError(7), { message: "7", stack: "" });
  const described = describeError({ reason: Object.assign(new Error("why"), { stack: "s".repeat(5000) }) });
  assert.equal(described.message, "why");
  assert.equal(described.stack.length, 4000);
});

test("run() turns a throw into a fault reported once; a faulted mod does not run again", async () => {
  const faults = [];
  const boundary = createBoundary({ onFault: (id, error, where) => faults.push([id, error.message, where]), target: null, log: () => {} });
  const ok = await boundary.run("m", () => 41 + 1, "import");
  assert.deepEqual(ok, { ok: true, value: 42 });
  const failed = await boundary.run("m", async () => {
    throw new Error("import exploded");
  }, "import");
  assert.equal(failed.ok, false);
  assert.deepEqual(faults, [["m", "import exploded", "import"]]);
  const again = await boundary.run("m", () => 1, "activate");
  assert.equal(again.ok, false);
  assert.equal(boundary.isFaulted("m"), true);
  assert.deepEqual(boundary.faultedMods(), ["m"]);
  assert.equal(faults.length, 1, "the second failure is not reported again");
});

test("global error and unhandledrejection events are attributed by prefix and reported once", () => {
  const win = makeWindow();
  const faults = [];
  const boundary = createBoundary({ onFault: (id, _error, where) => faults.push([id, where]), target: win, log: () => {} });
  boundary.watch("mod-a", PREFIX("mod-a"));
  boundary.watch("mod-b", PREFIX("mod-b"));
  let prevented = 0;
  const event = (stack) => ({ error: Object.assign(new Error("late"), { stack }), preventDefault: () => (prevented += 1) });
  win.dispatch("error", event(`Error: late\n at ${PREFIX("mod-a")}main.js:1:1`));
  win.dispatch("error", event(`Error: late\n at /assets/main-abc.js:1:1`)); // host code: ignored
  win.dispatch("unhandledrejection", { reason: Object.assign(new Error("rej"), { stack: `at ${PREFIX("mod-b")}x.js:3:3` }), preventDefault: () => (prevented += 1) });
  win.dispatch("error", event(`Error: again\n at ${PREFIX("mod-a")}main.js:9:9`));
  assert.deepEqual(faults, [["mod-a", "uncaught error"], ["mod-b", "unhandled rejection"]]);
  assert.equal(prevented, 3, "attributed events are marked handled");
  assert.equal(win.listeners.error.length, 1, "listeners are installed once");
});

test("a throwing onFault never propagates", async () => {
  const boundary = createBoundary({
    onFault: () => {
      throw new Error("reporter broken");
    },
    target: null,
    log: () => {},
  });
  const result = await boundary.run("m", () => {
    throw new Error("first");
  }, "activate");
  assert.equal(result.ok, false);
});
