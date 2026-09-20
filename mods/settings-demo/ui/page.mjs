// settings-demo — the mod's own settings page inside the FloofyCrew App (Requirement 16.4).
//
// The App imports this module same-origin (/api/apps/floofycrew/ui/mods/settings-demo/ui/page.mjs)
// while the mod is active and calls the default export with the page's container and
// `api = floofy.mod("settings-demo")`. The page shows what a mod page can do:
//
//   * a text field and a toggle persisted with api.config.set(patch) — the mod's
//     .floofy/config.json, read back with api.config.get() when the page opens again;
//   * a button calling the mod's own backend route with api.routes.fetch("echo") — the
//     python-hook part (hook/__init__.py) answers with the stored config, proving that the
//     page and the Python side share one store;
//   * the host's theme tokens (api.theme.tokens()) for the accent colour, and api.state()
//     for the mod's Loader verdict.
//
// No framework, no third-party origin, no network beyond the Loader's same-origin routes.
// Return a cleanup function; a throw is caught by the App's error boundary (Disable button).
"use strict";

const DEFAULTS = Object.freeze({ greeting: "Hello from settings-demo", notify: false });

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "style") Object.assign(node.style, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, String(value));
  }
  for (const child of children) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}

export default async function mount(container, api) {
  const tokens = api.theme.tokens();
  const accent = tokens["--accent"] || "#ff6b8b";
  const muted = tokens["--text-muted"] || "inherit";
  const stored = await api.config.get();
  const state = await api.state();

  const status = el("span", { "data-testid": "settings-demo-status", style: { color: muted, marginLeft: "0.6rem" } }, stored && Object.keys(stored).length ? "loaded from .floofy/config.json" : "defaults (nothing stored yet)");
  const greeting = el("input", { type: "text", "data-testid": "settings-demo-greeting", value: stored.greeting ?? DEFAULTS.greeting, style: { minWidth: "18rem" } });
  const notify = el("input", { type: "checkbox", "data-testid": "settings-demo-notify" });
  notify.checked = Boolean(stored.notify ?? DEFAULTS.notify);
  const echoOut = el("pre", { "data-testid": "settings-demo-echo-out", style: { whiteSpace: "pre-wrap", fontFamily: "var(--mono, monospace)", fontSize: "0.8rem", margin: "0.4rem 0 0" } }, "");

  async function save(patch) {
    status.textContent = "saving…";
    const saved = await api.config.set(patch);
    status.textContent = `saved: ${JSON.stringify(saved)}`;
    api.log.info("config saved", saved);
  }

  greeting.addEventListener("change", () => save({ greeting: greeting.value }));
  notify.addEventListener("change", () => save({ notify: notify.checked }));

  const echoButton = el(
    "button",
    {
      type: "button",
      "data-testid": "settings-demo-echo",
      style: { borderColor: accent, color: accent, padding: "0.3rem 0.7rem", borderRadius: "6px", background: "transparent", cursor: "pointer" },
      onclick: async () => {
        echoOut.textContent = "asking the mod's backend…";
        const response = await api.routes.fetch("echo", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ from: "settings page" }) });
        const body = await response.json();
        echoOut.textContent = response.ok ? JSON.stringify(body, null, 2) : `HTTP ${response.status}: ${JSON.stringify(body)}`;
      },
    },
    "Ask the backend what it stored",
  );

  const routes = await api.routes.list();
  const page = el(
    "div",
    { "data-testid": "settings-demo-page", style: { display: "flex", flexDirection: "column", gap: "0.8rem" } },
    el("p", { style: { margin: 0 } }, el("strong", { style: { color: accent } }, "settings-demo"), ` ${api.id} — a mod page hosted by the FloofyCrew App. `, el("span", { style: { color: muted } }, "Unofficial; nothing here is part of KiroCrew.")),
    el("label", { style: { display: "flex", alignItems: "center", gap: "0.5rem" } }, "Greeting", greeting),
    el("label", { style: { display: "flex", alignItems: "center", gap: "0.5rem" } }, notify, "Notify me when the mod's backend answers"),
    el("div", {}, el("span", { style: { color: muted } }, "Persisted through floofy.mod(id).config.set → the mod's .floofy/config.json"), status),
    el("div", {}, echoButton, el("span", { style: { color: muted, marginLeft: "0.6rem" } }, `${routes.length} backend route(s): ${routes.map((r) => `${r.method} ${r.path}`).join(", ") || "none"}`)),
    echoOut,
    el("p", { "data-testid": "settings-demo-state", style: { color: muted, margin: 0, fontSize: "0.8rem" } }, `Loader verdict: ${state && state.active ? "active" : (state && state.reason) || "inactive"} · mod ${state && state.version ? state.version : "?"} · theme ${api.theme.current().theme || "default"}`),
  );
  container.append(page);
  return () => {
    page.remove();
  };
}
