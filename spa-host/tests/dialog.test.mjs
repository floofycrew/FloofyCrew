// `floofy.mod(id).dialog` (spa-host/src/dialog.mjs) under plain `node --test` with a minimal fake DOM:
// the dialogs never touch window.alert/confirm/prompt (the desktop shell has none), render into the document,
// answer on Enter/Escape/click, validate fields, and run one at a time.
import assert from "node:assert/strict";
import { test } from "node:test";

import { createDialogs } from "../src/dialog.mjs";
import { createModApi } from "../src/modapi.mjs";

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.style = {};
    this.attributes = {};
    this.children = [];
    this.listeners = {};
    this.textContent = "";
    this.value = "";
    this.disabled = false;
    this.parent = null;
    this.focused = false;
    this.selected = false;
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
  append(...nodes) {
    for (const node of nodes) {
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
  select() {
    this.selected = true;
  }
  *walk() {
    yield this;
    for (const child of this.children) yield* child.walk();
  }
  find(testid) {
    for (const node of this.walk()) if (node.attributes["data-testid"] === testid) return node;
    return null;
  }
}

function fakeDocument() {
  const body = new FakeElement("body");
  const listeners = {};
  return {
    body,
    createElement: (tag) => new FakeElement(tag),
    addEventListener(name, fn) {
      (listeners[name] ||= []).push(fn);
    },
    removeEventListener(name, fn) {
      listeners[name] = (listeners[name] || []).filter((f) => f !== fn);
    },
    key(key, target) {
      for (const fn of [...(listeners.keydown || [])]) fn({ key, target, preventDefault() {}, stopPropagation() {} });
    },
    overlay: () => body.children.find((c) => c.attributes["data-testid"] === "floofycrew-modal") || null,
  };
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

test("confirm renders the App's overlay and answers on Yes / No / Escape", async () => {
  const doc = fakeDocument();
  const dialog = createDialogs("pagey", { document: doc });
  let pending = dialog.confirm("Sure?", { title: "Check" });
  await tick();
  const overlay = doc.overlay();
  assert.ok(overlay, "rendered into the body");
  assert.equal(overlay.attributes["data-kind"], "mod-confirm");
  assert.equal(overlay.attributes["data-mod"], "pagey");
  assert.equal(overlay.find("floofycrew-mod-dialog-text").textContent, "Sure?");
  assert.equal(overlay.find("floofycrew-mod-dialog-ok").textContent, "Yes");
  assert.equal(overlay.find("floofycrew-mod-dialog-cancel").textContent, "No");
  assert.equal(overlay.find("floofycrew-mod-dialog-ok").focused, true, "the primary button has focus");
  overlay.find("floofycrew-mod-dialog-ok").fire("click");
  assert.equal(await pending, true);
  assert.equal(doc.overlay(), null, "closed");

  pending = dialog.confirm("Again?");
  await tick();
  doc.overlay().find("floofycrew-mod-dialog-cancel").fire("click");
  assert.equal(await pending, false);

  pending = dialog.confirm("Once more?");
  await tick();
  doc.key("Escape");
  assert.equal(await pending, false);
  assert.equal(doc.overlay(), null);
});

test("prompt validates, follows Enter, and returns null when cancelled", async () => {
  const doc = fakeDocument();
  const dialog = createDialogs("pagey", { document: doc });
  const pending = dialog.prompt("Slug?", { value: "My Theme", validate: (v) => (/^[a-z-]+$/.test(v) ? null : "letters and dashes") });
  await tick();
  const overlay = doc.overlay();
  assert.equal(overlay.attributes["data-kind"], "mod-prompt");
  const field = overlay.find("floofycrew-mod-dialog-field-value");
  assert.equal(field.value, "My Theme");
  assert.equal(field.focused && field.selected, true, "the field is focused with its text selected");
  const ok = overlay.find("floofycrew-mod-dialog-ok");
  assert.equal(ok.disabled, true, "invalid on open");
  assert.equal(overlay.find("floofycrew-mod-dialog-error-value").textContent, "", "no error shown before the user touched the field");
  doc.key("Enter", field);
  assert.ok(doc.overlay(), "Enter on an invalid value keeps the dialog open");
  assert.equal(overlay.find("floofycrew-mod-dialog-error-value").textContent, "letters and dashes", "a submit attempt shows the error");
  field.value = "my-theme";
  field.fire("input");
  assert.equal(ok.disabled, false);
  doc.key("Enter", field);
  assert.equal(await pending, "my-theme");

  const cancelled = dialog.prompt("Slug?");
  await tick();
  doc.overlay().fire("click", { target: doc.overlay() });
  assert.equal(await cancelled, null, "a click on the backdrop cancels");
});

test("form: several fields, transform links them, the answer is keyed by name", async () => {
  const doc = fakeDocument();
  const dialog = createDialogs("pagey", { document: doc });
  const pending = dialog.form({ title: "New", okLabel: "Create", fields: [{ name: "name", label: "Name", transform: (v) => ({ slug: v.toLowerCase().replace(/\s+/g, "-") }) }, { name: "slug", label: "Slug", validate: (v) => (v ? null : "required") }] });
  await tick();
  const overlay = doc.overlay();
  const name = overlay.find("floofycrew-mod-dialog-field-name");
  const slug = overlay.find("floofycrew-mod-dialog-field-slug");
  assert.equal(overlay.find("floofycrew-mod-dialog-ok").disabled, true);
  name.value = "Dialog Smoke";
  name.fire("input");
  assert.equal(slug.value, "dialog-smoke");
  assert.equal(overlay.find("floofycrew-mod-dialog-ok").disabled, false);
  overlay.find("floofycrew-mod-dialog-ok").fire("click");
  assert.deepEqual(await pending, { name: "Dialog Smoke", slug: "dialog-smoke" });
  await assert.rejects(() => dialog.form({ fields: [{}] }), /needs a name/);
});

test("alert has one button; dialogs run one at a time; the mod API exposes dialog", async () => {
  const doc = fakeDocument();
  const dialog = createDialogs("pagey", { document: doc });
  const first = dialog.alert("Heads up");
  const second = dialog.confirm("Then?");
  await tick();
  let overlay = doc.overlay();
  assert.equal(overlay.attributes["data-kind"], "mod-alert");
  assert.equal(overlay.find("floofycrew-mod-dialog-cancel"), null, "no cancel button on an alert");
  assert.equal(doc.body.children.length, 1, "the second dialog waits");
  overlay.find("floofycrew-mod-dialog-ok").fire("click");
  await first;
  await tick();
  overlay = doc.overlay();
  assert.equal(overlay.attributes["data-kind"], "mod-confirm", "the queued dialog opens after the first closed");
  overlay.find("floofycrew-mod-dialog-ok").fire("click");
  assert.equal(await second, true);

  const api = createModApi("pagey", { fetch: async () => ({ ok: true, status: 200, json: async () => ({}) }), document: doc });
  assert.equal(typeof api.dialog.confirm, "function");
  assert.equal(typeof api.dialog.prompt, "function");
  assert.equal(typeof api.dialog.alert, "function");
  assert.equal(typeof api.dialog.form, "function");
  await assert.rejects(() => createDialogs("pagey", { document: null }).confirm("x"), /no document/);
});
