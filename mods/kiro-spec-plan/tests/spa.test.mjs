// node --test: the agent-selector badge, against the host's row shape, on a
// minimal DOM stub (no jsdom dependency; only what main.js touches).
import assert from "node:assert/strict";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

class El {
  constructor(tag, cls = "", text = "") { this.tagName = tag; this.className = cls; this.children = []; this.parent = null; this.attrs = {}; this._text = text; this.innerHTML = ""; this.title = ""; this.id = ""; this.textContent = text; }
  appendChild(c) { c.parent = this; this.children.push(c); return c; }
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((x) => x !== this); }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  get parentElement() { return this.parent; }
  set innerHTML(v) { this._html = v; this.textContent = v.replace(/<[^>]*>/g, ""); }
  get innerHTML() { return this._html || ""; }
  all() { return this.children.flatMap((c) => [c, ...c.all()]); }
  matches(sel) {
    if (sel === '[role="option"]') return this.attrs.role === "option";
    if (sel === "span.font-mono") return this.tagName === "span" && /\bfont-mono\b/.test(this.className);
    const m = sel.match(/^\[([\w-]+)(?:="([^"]*)")?\]$/);
    if (m) return m[2] === undefined ? this.attrs[m[1]] !== undefined : this.attrs[m[1]] === m[2];
    return false;
  }
  querySelector(sel) { return this.all().find((e) => e.matches(sel)) || null; }
  querySelectorAll(sel) { return this.all().filter((e) => e.matches(sel)); }
  closest(sel) { let n = this; while (n) { if (n.matches(sel)) return n; n = n.parent; } return null; }
}

function makeDocument(names) {
  const doc = new El("document");
  doc.head = doc.appendChild(new El("head"));
  doc.body = doc.appendChild(new El("body"));
  const list = doc.body.appendChild(new El("div")); list.setAttribute("role", "listbox");
  for (const name of names) {
    const row = list.appendChild(new El("button")); row.setAttribute("role", "option");
    const col = row.appendChild(new El("div", "flex flex-col"));
    const line = col.appendChild(new El("div", "flex items-center gap-1.5"));
    line.appendChild(new El("span", "text-[13px] font-mono font-semibold truncate", name));
  }
  doc.createElement = (tag) => new El(tag);
  doc.getElementById = (id) => doc.all().find((e) => e.id === id) || null;
  return doc;
}

function install(doc) {
  globalThis.document = doc;
  globalThis.MutationObserver = class { observe() {} disconnect() {} };
}

test("decorate badges exactly the four mode agents, once, and deactivate removes everything", async () => {
  install(makeDocument(["spec", "desk-default", "plan", "specialist"]));
  const mod = await import(path.join(here, "..", "spa", "main.js") + "?a");
  mod.activate({ config: { read: () => ({ kas: true }) } });
  const badges = document.querySelectorAll('[data-kiro-spec-plan="badge"]');
  assert.equal(badges.length, 2);
  assert.deepEqual(badges.map((b) => b.closest('[role="option"]').querySelector("span.font-mono").textContent), ["spec", "plan"]);
  assert.equal(badges[0].textContent, "Kiro mode");
  assert.equal(mod.decorate(), 0, "idempotent");
  mod.deactivate();
  assert.equal(document.querySelectorAll("[data-kiro-spec-plan]").length, 0);
  assert.equal(document.getElementById("kiro-spec-plan-style"), null);
});

test("with KAS off the badge says so and carries the fix", async () => {
  install(makeDocument(["bug-fix"]));
  const mod = await import(path.join(here, "..", "spa", "main.js") + "?b");
  mod.activate({ config: { read: () => ({ kas: false, hint: "kirocrew config set agent.acp_backend kas" }) } });
  const b = document.querySelector('[data-kiro-spec-plan="badge"]');
  assert.equal(b.textContent, "needs KAS");
  assert.match(b.title, /acp_backend kas/);
  mod.deactivate();
});
