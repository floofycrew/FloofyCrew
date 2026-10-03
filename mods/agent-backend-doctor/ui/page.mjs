// Agent Backend Doctor — the page.
//
// Mounted by the FloofyCrew App with `mount(container, api)`, `api = floofy.mod("agent-backend-doctor")`
// (docs/spa-api.md). Everything shown comes from the hook's `status` route; the three actions
// (re-resolve, CODEX_PATH, symlink repair) are its POST routes. No framework, no third-party
// origin, no browser pop-ups: confirmations use api.dialog, the App's own overlay.
"use strict";

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "style") Object.assign(node.style, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, String(value));
  }
  for (const child of children) if (child !== null && child !== undefined) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}

const SVG_NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) if (value !== null && value !== undefined) node.setAttribute(key, String(value));
  for (const child of children) node.append(child);
  return node;
}

/** A 14px status glyph: ok (check in a circle), warn (triangle), bad (cross), off (hollow). */
function glyph(kind, color) {
  const root = svg("svg", { viewBox: "0 0 16 16", width: 14, height: 14, fill: "none", stroke: color, "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", style: "vertical-align:-2px;margin-right:0.3rem" });
  if (kind === "ok") root.append(svg("circle", { cx: 8, cy: 8, r: 6.2 }), svg("path", { d: "M5 8.2l2 2 4-4.4" }));
  else if (kind === "warn") root.append(svg("path", { d: "M8 2.5 14.5 13.5H1.5Z" }), svg("path", { d: "M8 6.5v3.2M8 12h.01" }));
  else if (kind === "bad") root.append(svg("circle", { cx: 8, cy: 8, r: 6.2 }), svg("path", { d: "M5.5 5.5l5 5M10.5 5.5l-5 5" }));
  else root.append(svg("circle", { cx: 8, cy: 8, r: 6.2, "stroke-dasharray": "2 2" }));
  return root;
}

const COLORS = { ok: "#54b27a", warn: "#e0a03a", bad: "#e06060", off: "currentColor" };
const basename = (p) => (p ? String(p).split("/").pop() : "");
const short = (p, home) => (p && home && String(p).startsWith(home) ? "~" + String(p).slice(home.length) : p || "");

export default async function mount(container, api) {
  const tokens = api.theme.tokens();
  const accent = tokens["--accent"] || "#ff6b8b";
  const muted = tokens["--text-muted"] || "inherit";
  const border = tokens["--border"] || "rgba(127,127,127,0.35)";
  const mono = { fontFamily: "var(--mono, monospace)", fontSize: "0.78rem" };
  const btn = (label, onclick, attrs = {}) =>
    el("button", { type: "button", onclick, ...attrs, style: { borderColor: accent, color: accent, padding: "0.2rem 0.55rem", borderRadius: "4px", background: "transparent", cursor: "pointer", font: "inherit", fontSize: "0.8rem" } }, label);
  const h = (text) => el("h3", { style: { margin: "1.1rem 0 0.4rem", fontSize: "0.95rem" } }, text);
  const note = el("span", { "data-testid": "abd-note", style: { color: muted, marginLeft: "0.6rem", fontSize: "0.8rem" } }, "");
  const say = (text) => { note.textContent = text; };

  let status = null;
  let busy = false;
  const home = () => (status && status.local_bin ? status.local_bin.replace(/\/\.local\/bin$/, "") : "");

  async function call(path, body) {
    const response = await api.routes.fetch(path, body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let payload = null;
    try { payload = await response.json(); } catch (_) { payload = null; }
    if (!response.ok) throw new Error((payload && payload.error) || `${path}: HTTP ${response.status}`);
    return payload;
  }

  async function refresh() {
    say("reading…");
    try {
      status = await call("status");
      render();
      say(`updated ${new Date().toLocaleTimeString()}`);
    } catch (err) {
      say(`status failed: ${err.message || err}`);
    }
  }

  // --- layout --------------------------------------------------------------------------------------------
  const header = el("div", { "data-testid": "abd-env", style: { ...mono, color: muted, lineHeight: 1.5 } });
  const table = el("table", { "data-testid": "abd-backends", style: { borderCollapse: "collapse", width: "100%", fontSize: "0.82rem" } });
  const repairs = el("div", { "data-testid": "abd-repairs" });
  const codexBox = el("div", { "data-testid": "abd-codex" });
  const findings = el("div", { "data-testid": "abd-findings", style: { ...mono, color: muted } });
  const searchPath = el("details", { style: { ...mono, color: muted, marginTop: "0.4rem" } });

  const page = el("div", { "data-testid": "abd-page", style: { maxWidth: "72rem" } },
    el("p", { style: { color: muted, margin: "0 0 0.6rem" } },
      "Where each agent backend's harness is on this machine, what the running gateway believes about it, and the two things it keeps getting wrong: npm installs under a prefix it does not search, and a cached 'not found' that only a restart would clear."),
    el("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" } },
      btn("Refresh", refresh), btn("Re-resolve all backends", () => reresolve(null), { "data-testid": "abd-reresolve-all" }), note),
    header, searchPath,
    h("Backends"), el("div", { style: { overflowX: "auto" } }, table),
    h("Codex binary"), codexBox,
    h("Make it permanent"), repairs,
    h("Recent resolutions through this mod"), findings);
  container.replaceChildren(page);

  // --- actions -------------------------------------------------------------------------------------------
  async function reresolve(backend) {
    if (busy) return;
    busy = true;
    say(backend === null ? "re-resolving every backend…" : `re-resolving ${backend || "kiro"}…`);
    try {
      const out = await call("reresolve", backend === null ? {} : { backend });
      const flipped = Object.entries(out.probes || {}).map(([name, p]) => `${name}: ${p.installed}${p.restart_required ? " (restart still required)" : ""}, cache ${p.cache ? p.cache.state : "?"}`);
      say(flipped.join(" · ") || "done");
      await refresh();
    } catch (err) {
      say(`re-resolve failed: ${err.message || err}`);
    } finally { busy = false; }
  }

  async function setCodexPath(enabled, path) {
    try {
      const out = await call("codex-path", { enabled, path });
      say(out.enabled ? `CODEX_PATH=${out.codex_path} for every new Codex session` : "CODEX_PATH unset; codex-acp runs its own Codex");
      await refresh();
    } catch (err) {
      say(`CODEX_PATH not changed: ${err.message || err}`);
      await refresh();
    }
  }

  async function applyRepairs(entries) {
    const lines = entries.map((e) => `${short(e.link, home())} → ${short(e.target, home())}`).join("\n");
    const ok = await api.dialog.confirm(`Create these symlinks in your own ~/.local/bin? Nothing is overwritten, and nothing in the KiroCrew install is touched.\n\n${lines}`, { title: "Link launchers into ~/.local/bin", okLabel: "Create links" });
    if (!ok) return;
    try {
      const out = await call("symlink", { confirm: true, entries: entries.map((e) => ({ name: e.name })) });
      const failed = (out.results || []).filter((r) => !r.ok);
      say(failed.length ? `${failed.length} link(s) not created: ${failed.map((r) => `${r.name}: ${r.error}`).join("; ")}` : `${out.results.length} link(s) created — re-resolve, and the host's own search finds them from now on`);
      await refresh();
    } catch (err) {
      say(`symlink repair failed: ${err.message || err}`);
    }
  }

  // --- rendering -----------------------------------------------------------------------------------------
  function cell(...children) { return el("td", { style: { padding: "0.3rem 0.5rem", borderBottom: `1px solid ${border}`, verticalAlign: "top" } }, ...children); }
  function head(text) { return el("th", { style: { textAlign: "left", padding: "0.3rem 0.5rem", borderBottom: `1px solid ${border}`, color: muted, fontWeight: 500 } }, text); }
  function code(text) { return el("code", { style: { ...mono, wordBreak: "break-all" } }, text); }

  function verdict(row) {
    const p = row.probe || {};
    const kind = p.installed === "installed" ? (p.restart_required ? "warn" : "ok") : p.installed === "missing" ? "bad" : "off";
    const text = p.installed === "installed" ? (p.restart_required ? "installed — gateway still has a cached miss" : "installed") : p.installed === "missing" ? `missing: ${(p.missing_components || []).join(", ") || "harness"}` : "unknown";
    return el("span", {}, glyph(kind, COLORS[kind]), text, p.install_command && p.installed === "missing" ? el("div", {}, code(p.install_command)) : null);
  }

  function harness(row) {
    const c = row.components || {};
    const parts = [];
    if (row.id === "" || row.id === "kas") {
      parts.push(el("div", {}, glyph(c.launcher_on_host_path ? "ok" : "off", c.launcher_on_host_path ? COLORS.ok : COLORS.off), c.launcher_on_host_path ? code(short(c.launcher_on_host_path, home())) : `${c.launcher} not on the search path`));
      return el("div", {}, ...parts);
    }
    if (c.launcher_on_host_path) parts.push(el("div", {}, glyph("ok", COLORS.ok), "host search: ", code(short(c.launcher_on_host_path, home()))));
    else parts.push(el("div", {}, glyph(c.adapter || (c.launchers || []).length ? "warn" : "bad", c.adapter || (c.launchers || []).length ? COLORS.warn : COLORS.bad), `${c.launcher}: not on the host's search path`));
    if (c.adapter) parts.push(el("div", { style: { color: muted } }, "found by the doctor: ", code(short(c.adapter.entry, home())), ` (${c.adapter.rung}; node ${short(c.adapter.node, home())})`));
    else if ((c.launchers || []).length) parts.push(el("div", { style: { color: muted } }, "launcher under an npm prefix: ", code(short(c.launchers[0], home()))));
    return el("div", {}, ...parts);
  }

  function cli(row) {
    const c = (row.components || {}).cli;
    if (!c) return el("span", { style: { color: muted } }, "–");
    const kind = c.path ? "ok" : row.id === "codex" ? "off" : "bad";
    const text = c.path ? code(short(c.path, home())) : row.id === "codex" ? "adapter's own Codex" : `${c.name} not found`;
    return el("div", {}, glyph(kind, COLORS[kind]), text, el("div", { style: { color: muted, fontSize: "0.75rem" } }, `${c.env}${c.forwarded ? " (set by the host for the child)" : " (read by the adapter from the gateway's environment)"}`));
  }

  function cache(row) {
    const s = (row.cache || {}).state || "none";
    const kind = s === "ok" ? "ok" : s === "negative" ? "bad" : "off";
    const label = s === "ok" ? "resolved" : s === "negative" ? "cached: not found" : s === "unresolved" ? "not resolved yet" : s === "none" ? "no cache (resolves per spawn)" : s;
    const detail = Object.values((row.cache || {}).caches || {}).find((c) => c.state === "ok");
    return el("div", {}, glyph(kind, COLORS[kind]), label, detail && detail.value ? el("div", { style: { color: muted, fontSize: "0.75rem" } }, code(Array.isArray(detail.value) ? detail.value.map((v) => short(v, home())).join(" ") : short(detail.value, home()))) : null);
  }

  function models(row) {
    const ids = row.advertised || [];
    if (!ids.length) return el("span", { style: { color: muted } }, row.namespace === "acp" ? "kiro-cli's list" : "none advertised yet");
    const d = el("details", {}, el("summary", { style: { cursor: "pointer" } }, `${ids.length} advertised`), el("div", { style: { ...mono, whiteSpace: "pre-wrap", maxWidth: "18rem" } }, ids.join("\n")));
    return d;
  }

  function render() {
    if (!status) return;
    const hooks = status.hooks || {};
    header.replaceChildren(
      el("div", {}, `KiroCrew ${status.host_version || "?"} · discovery hooks: node adapters ${hooks.node_adapters || "?"}, plain binaries ${hooks.plain_binaries || "?"}`),
      el("div", {}, `node: ${status.node ? short(status.node, home()) : "not found"} · CODEX_PATH: ${status.codex_path ? short(status.codex_path, home()) : "unset"}`),
      el("div", {}, `npm prefixes checked (${(status.prefixes || []).length}): ${(status.prefixes || []).map((p) => `${short(p.prefix, home())}${p.node_modules.length ? "" : " (no node_modules)"}`).join(", ") || "none found"}`),
      status.host_rows_error ? el("div", { style: { color: COLORS.warn } }, `host /api/acp-backends snapshot unavailable: ${status.host_rows_error.error}`) : null);
    searchPath.replaceChildren(el("summary", { style: { cursor: "pointer" } }, `the host's adapter search path (${status.search_path_count} dirs)`), el("div", { style: { whiteSpace: "pre-wrap" } }, (status.search_path || []).join("\n")));

    table.replaceChildren(el("thead", {}, el("tr", {}, head("Backend"), head("Host verdict"), head("Adapter / binary"), head("CLI"), head("Gateway cache"), head("Models"), head(""))));
    const body = el("tbody");
    for (const row of status.backends || []) {
      const name = el("div", {}, el("strong", {}, row.name), el("div", { style: { color: muted, fontSize: "0.75rem" } }, `${row.namespace}${row.selectable ? "" : " · not selectable by policy"}`));
      const action = row.id === "" || row.id === "kas" ? el("span", { style: { color: muted } }, "–") : btn("Re-resolve", () => reresolve(row.id), { "data-testid": `abd-reresolve-${row.name}` });
      body.append(el("tr", { "data-backend": row.name }, cell(name), cell(verdict(row)), cell(harness(row)), cell(cli(row)), cell(cache(row)), cell(models(row)), cell(action)));
    }
    table.append(body);

    // codex
    const cfg = status.config || {};
    const toggle = el("input", { type: "checkbox", "data-testid": "abd-codex-toggle" });
    toggle.checked = Boolean(status.codex_path);
    const pathInput = el("input", { type: "text", "data-testid": "abd-codex-path", value: cfg.codex_path || status.codex_path || status.codex_path_default || "", style: { ...mono, width: "22rem", maxWidth: "100%" } });
    toggle.addEventListener("change", () => setCodexPath(toggle.checked, pathInput.value.trim()));
    pathInput.addEventListener("change", () => { if (toggle.checked) setCodexPath(true, pathInput.value.trim()); });
    const codexRow = (status.backends || []).find((r) => r.id === "codex");
    const installedCodex = codexRow && codexRow.components && codexRow.components.cli ? codexRow.components.cli.installed_codex : null;
    codexBox.replaceChildren(
      el("p", { style: { color: muted, margin: "0 0 0.4rem" } }, "codex-acp ships its own Codex binary and runs a different one only when CODEX_PATH names it; the host leaves that variable alone. Switching this on exports it in the gateway process so every new Codex session uses the binary below (for example the one your own CLI tooling installed)."),
      el("label", { style: { display: "flex", gap: "0.5rem", alignItems: "center", flexWrap: "wrap" } }, toggle, "Use this Codex for codex-acp sessions:", pathInput),
      el("div", { style: { color: muted, fontSize: "0.78rem", marginTop: "0.3rem" } }, installedCodex ? `codex on the host's search path: ${short(installedCodex, home())}` : "no codex on the host's search path; type a path"));

    // repairs
    const plan = (status.repairs || []).filter((e) => !e.exists);
    const existing = (status.repairs || []).filter((e) => e.exists);
    repairs.replaceChildren(
      el("p", { style: { color: muted, margin: "0 0 0.4rem" } }, `The discovery above lives only while this mod is active. Linking the found launchers into ${short(status.local_bin, home())} (which the host does search) makes the host find them on its own, mod or no mod. Your own directory; nothing in the KiroCrew install changes.`),
      plan.length
        ? el("div", {}, el("ul", { style: { margin: "0 0 0.4rem", paddingLeft: "1.2rem", ...mono } }, ...plan.map((e) => el("li", {}, `${short(e.link, home())} → ${short(e.target, home())}`))), btn(`Create ${plan.length} symlink${plan.length === 1 ? "" : "s"}…`, () => applyRepairs(plan), { "data-testid": "abd-symlink" }))
        : el("div", { style: { color: muted } }, existing.length ? "every found launcher is already linked" : "nothing to link: nothing was found outside the host's search path"));

    // findings
    const rows = (status.backends || []).map((r) => r.finding ? { name: r.components.launcher, f: r.finding } : null).filter(Boolean);
    findings.replaceChildren(rows.length
      ? el("ul", { style: { margin: 0, paddingLeft: "1.2rem" } }, ...rows.map(({ name, f }) => el("li", {}, `${name}: host ${f.host}${f.doctor ? `, doctor answered ${short(f.doctor.entry, home())} (${f.doctor.rung})` : f.host === "missing" ? ", nothing found either" : ""}`)))
      : el("span", {}, "no session has asked for an adapter since the gateway started"));
  }

  await refresh();
  return () => { container.replaceChildren(); };
}
