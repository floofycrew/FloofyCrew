// custom-themes — the theme editor inside the FloofyCrew App (a `ui` part).
//
// Mounted by the App with `mount(container, api)` where `api = floofy.mod("custom-themes")`.
// Everything goes through the mod's own backend routes (api.routes.fetch): the library
// (your themes), the presets (the running host's built-in themes, harvested from its own
// stylesheet), the compiler (preview / install) and the host's theme directory. The three
// host calls a theme switch needs — `localStorage['mc-color-theme']`, the `mc-theme-sync`
// event and `PUT /api/config/theme` — are the same ones the host's Settings page makes.
//
// No framework, no build step, no third-party origin. Styles live in one <style> scoped
// under `.fct` and use the host's own tokens, so the editor follows whatever theme is active.
"use strict";

const VAR_GROUPS = [
  ["Surfaces", ["--bg", "--bg-accent", "--bg-elevated", "--bg-hover", "--card", "--card-fg", "--card-hl", "--panel", "--panel-strong", "--chrome"]],
  ["Text", ["--text", "--text-strong", "--muted", "--muted-strong", "--muted-fg"]],
  ["Borders", ["--border", "--border-strong", "--border-hover", "--ring"]],
  ["Accent", ["--accent", "--accent-fg", "--accent-hover", "--accent-subtle", "--accent-glow"]],
  ["Status", ["--ok", "--ok-fg", "--ok-subtle", "--warn", "--warn-fg", "--warn-subtle", "--danger", "--danger-fg", "--danger-subtle", "--info", "--info-fg", "--aim", "--aim-fg", "--aim-subtle", "--clarify", "--clarify-subtle"]],
  ["Code and diffs", ["--json-key", "--json-str", "--json-num", "--json-bool", "--diff-add", "--diff-add-text", "--diff-del", "--diff-del-text", "--diff-hunk", "--diff-hunk-text", "--diff-meta-text"]],
  ["Shadows and terminal", ["--shadow-sm", "--shadow-md", "--shadow-lg", "--term-magenta", "--term-cyan"]],
];
export const ALL_VARS = VAR_GROUPS.flatMap(([, names]) => names);
const REQUIRED = new Set(["--bg", "--text", "--accent"]);
const PREVIEW_STYLE_ID = "floofy-custom-themes-preview";
const CHANGED_EVENT = "floofy-custom-themes-changed";
const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,39}$/;

const CSS = `
.fct{display:flex;flex-direction:column;gap:1rem;color:var(--text,inherit);font-size:.9rem}
.fct *{box-sizing:border-box}
.fct h2{margin:0;font-size:1.05rem}
.fct h3{margin:0 0 .4rem;font-size:.82rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted,inherit)}
.fct .muted{color:var(--muted,inherit)}
.fct .row{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center}
.fct .cols{display:grid;grid-template-columns:minmax(260px,320px) minmax(0,1fr);gap:1rem;align-items:start}
@media (max-width:900px){.fct .cols{grid-template-columns:1fr}}
.fct .card{background:var(--card,transparent);border:1px solid var(--border,rgba(127,127,127,.35));border-radius:6px;padding:.75rem}
.fct button{font:inherit;color:var(--text,inherit);background:var(--bg-elevated,transparent);border:1px solid var(--border-strong,rgba(127,127,127,.5));border-radius:4px;padding:.3rem .65rem;cursor:pointer}
.fct button:hover{border-color:var(--border-hover,var(--accent))}
.fct button.primary{background:var(--accent);color:var(--accent-fg,#000);border-color:var(--accent)}
.fct button.danger{color:var(--danger);border-color:var(--danger)}
.fct button:disabled{opacity:.5;cursor:default}
.fct input[type=text],.fct input[type=search],.fct textarea,.fct select{font:inherit;color:var(--text,inherit);background:var(--bg,transparent);border:1px solid var(--border,rgba(127,127,127,.35));border-radius:4px;padding:.3rem .45rem;min-width:0}
.fct textarea{font-family:var(--mono,ui-monospace,monospace);font-size:.8rem;line-height:1.4;width:100%;min-height:14rem;resize:vertical;white-space:pre;tab-size:2}
.fct .list{display:flex;flex-direction:column;gap:.4rem}
.fct .item{display:flex;align-items:center;gap:.55rem;padding:.45rem .55rem;border:1px solid var(--border,rgba(127,127,127,.35));border-radius:4px;background:var(--bg-elevated,transparent);cursor:pointer;text-align:left;width:100%}
.fct .item.selected{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.fct .item .name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fct .swatch{display:inline-flex;border-radius:3px;overflow:hidden;border:1px solid var(--border,rgba(127,127,127,.35));height:18px;flex:none}
.fct .swatch i{display:block;width:9px;height:18px}
.fct .badge{font-size:.68rem;text-transform:uppercase;letter-spacing:.05em;padding:.1rem .35rem;border-radius:3px;background:var(--accent-subtle,rgba(127,127,127,.2));color:var(--text,inherit);flex:none}
.fct .badge.on{background:var(--ok-subtle,rgba(80,200,120,.2));color:var(--ok,inherit)}
.fct .presets{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:.4rem}
.fct .preset{display:flex;flex-direction:column;gap:.3rem;padding:.45rem;border:1px solid var(--border,rgba(127,127,127,.35));border-radius:4px;background:var(--bg-elevated,transparent);cursor:pointer;text-align:left}
.fct .preset:hover{border-color:var(--accent)}
.fct .preset .bar{height:22px;border-radius:3px;display:flex;overflow:hidden}
.fct .preset .bar i{flex:1}
.fct .preset .label{font-size:.78rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fct .tabs{display:flex;gap:.25rem;border-bottom:1px solid var(--border,rgba(127,127,127,.35));margin-bottom:.75rem}
.fct .tabs button{border:0;border-bottom:2px solid transparent;border-radius:0;background:transparent;padding:.4rem .7rem}
.fct .tabs button.on{border-bottom-color:var(--accent);color:var(--accent)}
.fct .vars{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:.35rem .9rem}
.fct .var{display:grid;grid-template-columns:150px 30px 1fr 22px;gap:.35rem;align-items:center}
.fct .var code{font-size:.75rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fct .var input[type=color]{width:30px;height:26px;padding:0;border:1px solid var(--border,rgba(127,127,127,.35));border-radius:4px;background:transparent}
.fct .var input[type=text]{font-family:var(--mono,ui-monospace,monospace);font-size:.78rem}
.fct .var .clear{border:0;background:transparent;padding:0;color:var(--muted,inherit);font-size:.9rem;line-height:1}
.fct .var.required code{color:var(--accent)}
.fct .grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.6rem}
.fct label.f{display:flex;flex-direction:column;gap:.2rem;font-size:.8rem;color:var(--muted,inherit)}
.fct label.f input,.fct label.f select{color:var(--text,inherit)}
.fct .status{min-height:1.3em;font-size:.8rem}
.fct .status.err{color:var(--danger)}
.fct .status.ok{color:var(--ok)}
.fct .asset{display:flex;align-items:center;gap:.5rem;padding:.35rem 0;border-bottom:1px solid var(--border,rgba(127,127,127,.2))}
.fct .asset img{height:28px;width:28px;object-fit:contain;background:var(--bg,transparent);border-radius:3px}
.fct .hint{font-size:.78rem;color:var(--muted,inherit);margin:.2rem 0 0}
.fct kbd{font-family:var(--mono,ui-monospace,monospace);font-size:.75rem;padding:0 .25rem;border:1px solid var(--border,rgba(127,127,127,.35));border-radius:3px}
`;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (key === "value") node.value = value;
    else if (key === "checked") node.checked = Boolean(value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function swatch(block) {
  return el("span", { class: "swatch", title: "bg · card · text · accent" }, ...["--bg", "--card", "--text", "--accent"].map((k) => el("i", { style: { background: (block && block[k]) || "transparent" } })));
}

function toHex6(value) {
  const v = String(value || "").trim();
  let m = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(v);
  if (m) return `#${m[1]}`;
  m = /^#([0-9a-f]{3})([0-9a-f])?$/i.exec(v);
  if (m) return `#${m[1].split("").map((c) => c + c).join("")}`;
  m = /^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i.exec(v);
  if (m) return `#${[m[1], m[2], m[3]].map((n) => Math.max(0, Math.min(255, Number(n))).toString(16).padStart(2, "0")).join("")}`;
  return null;
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function download(name, text, type = "application/json") {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = el("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.readAsDataURL(file);
  });
}

function pickFile(accept) {
  return new Promise((resolve) => {
    const input = el("input", { type: "file", accept, style: { display: "none" } });
    input.addEventListener("change", () => resolve(input.files && input.files[0] ? input.files[0] : null));
    document.body.append(input);
    input.click();
    setTimeout(() => input.remove(), 60000);
  });
}

function slugify(text) {
  return String(text || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40);
}

export default async function mount(container, api) {
  const root = el("div", { class: "fct", "data-testid": "custom-themes-page" });
  const style = el("style", {}, CSS);
  root.append(style);
  container.append(root);

  const state = { list: null, presets: null, selected: null, doc: null, dirty: false, tab: "palette", mode: "dark", preview: false, activeTheme: null, hostVersion: "" };
  const status = el("div", { class: "status", "data-testid": "custom-themes-status" });

  // Every question this page asks goes through api.dialog (floofy.api_version >= 1.2.0): the dashboard also runs in
  // the KiroCrew desktop shell, which has no window.prompt/confirm/alert. On an older FloofyCrew the page says so
  // instead of silently doing nothing.
  if (!api.dialog || typeof api.dialog.form !== "function") {
    root.append(el("p", { class: "status err", "data-testid": "custom-themes-needs-dialog" }, "This page needs a newer FloofyCrew Loader (floofy.api_version 1.2.0 or later, for its in-app dialogs). Update FloofyCrew with `floofy self-update`, then reload."));
    return () => root.remove();
  }

  async function call(path, init = {}) {
    // routes.url() encodes each path segment; a query string is appended afterwards
    const [rel, query] = String(path).split("?", 2);
    const url = api.routes.url(rel) + (query ? `?${query}` : "");
    const response = await fetch(url, { credentials: "same-origin", ...init, headers: { ...(init.body && typeof init.body === "string" ? { "Content-Type": "application/json" } : {}), ...(init.headers || {}) } });
    const type = response.headers.get("Content-Type") || "";
    const body = type.includes("json") ? await response.json() : await response.text();
    if (!response.ok) throw new Error((body && body.error) || `HTTP ${response.status}`);
    return body;
  }
  function say(text, kind = "") {
    status.textContent = text;
    status.className = `status ${kind}`.trim();
  }

  // -- preview -------------------------------------------------------------------------

  function previewNode() {
    let node = document.getElementById(PREVIEW_STYLE_ID);
    if (!node) {
      node = el("style", { id: PREVIEW_STYLE_ID, "data-floofy-mod": api.id });
      document.head.append(node);
    }
    return node;
  }
  function stopPreview() {
    const node = document.getElementById(PREVIEW_STYLE_ID);
    if (node) node.remove();
  }
  const refreshPreview = debounce(async () => {
    if (!state.preview || !state.doc) return;
    try {
      const mode = document.documentElement.getAttribute("data-mode") || state.mode;
      const css = await call("preview", { method: "POST", body: JSON.stringify({ slug: state.doc.slug, variables: state.doc.variables, extraCss: state.doc.extraCss, mode }) });
      if (state.preview) previewNode().textContent = css;
    } catch (error) {
      say(`preview: ${error.message}`, "err");
    }
  }, 220);
  function touched() {
    state.dirty = true;
    saveButton.disabled = false;
    refreshPreview();
  }

  // -- host theme switch ---------------------------------------------------------------

  async function activateTheme(slug) {
    const color = `custom-${slug}`;
    try {
      localStorage.setItem("mc-color-theme", color);
    } catch {}
    const modePref = (() => {
      try {
        return localStorage.getItem("mc-theme") || "system";
      } catch {
        return "system";
      }
    })();
    window.dispatchEvent(new Event("mc-custom-themes-changed"));
    window.dispatchEvent(new CustomEvent("mc-theme-sync", { detail: { mode: modePref, colorTheme: color } }));
    try {
      const response = await fetch("/api/config/theme", { method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ color }) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
    } catch (error) {
      say(`the theme is on for this client; the host did not record it as the default (${error.message})`, "err");
      return;
    }
    window.dispatchEvent(new Event(CHANGED_EVENT));
    state.activeTheme = color;
    say(`${slug} is now the active theme (this client and the host default)`, "ok");
    await loadList();
  }

  // -- data ------------------------------------------------------------------------------

  async function loadList() {
    const result = await call("themes");
    state.list = result;
    state.activeTheme = result.activeTheme;
    renderSidebar();
  }
  async function loadPresets() {
    try {
      state.presets = (await call("presets")).presets;
    } catch (error) {
      state.presets = [];
      say(`presets: ${error.message}`, "err");
    }
    renderSidebar();
  }
  async function open(slug) {
    if (state.dirty && !(await api.dialog.confirm("Discard the unsaved changes to the current theme?", { title: "Unsaved changes", okLabel: "Discard", cancelLabel: "Keep editing", danger: true }))) return;
    const result = await call(`themes/${slug}`);
    state.selected = slug;
    state.doc = result.theme;
    state.dirty = false;
    stopPreview();
    state.preview = false;
    renderSidebar();
    renderEditor();
  }
  async function create(from, defaults = {}) {
    // One in-document dialog for both fields (the desktop shell has no window.prompt): the slug follows the
    // name until the user edits the slug themselves.
    let slugTouched = Boolean(defaults.slug);
    const answer = await api.dialog.form({
      title: from === "blank" ? "New blank theme" : `New theme from ${from.replace(/^(preset|installed):/, "")}`,
      text: "The slug is the theme's id on the host (lower-case letters, digits and dashes); it names the pack under the host's themes directory.",
      okLabel: "Create",
      fields: [
        { name: "name", label: "Name", value: defaults.name || "", placeholder: "My theme", validate: (v) => (v.trim() ? null : "a name is required"), transform: (v) => (slugTouched ? null : { slug: slugify(v) }) },
        { name: "slug", label: "Slug", value: slugify(defaults.slug || defaults.name || ""), placeholder: "my-theme", validate: (v) => (SLUG_RE.test(v) ? null : "lower-case letters, digits and dashes, starting with a letter or digit, at most 40 characters"), transform: () => { slugTouched = true; return null; } },
      ],
    });
    if (answer === null) return;
    const name = answer.name.trim();
    const slug = answer.slug;
    try {
      const result = await call("themes", { method: "POST", body: JSON.stringify({ slug, name, from, emoji: defaults.emoji }) });
      say(`created ${slug}`, "ok");
      await loadList();
      await open(result.theme.slug);
    } catch (error) {
      say(error.message, "err");
    }
  }

  // -- sidebar -----------------------------------------------------------------------------

  const sidebar = el("div", { class: "list" });
  function renderSidebar() {
    sidebar.replaceChildren();
    const list = state.list || { library: [], installed: [] };
    const active = state.activeTheme;
    sidebar.append(
      el("div", { class: "card" }, el("h3", {}, "Your themes"), el("div", { class: "list", "data-testid": "custom-themes-library" }, list.library.length ? list.library.map((t) => el("button", { type: "button", class: `item${state.selected === t.slug ? " selected" : ""}`, "data-slug": t.slug, onclick: () => open(t.slug) }, el("span", {}, t.emoji), el("span", { class: "name" }, t.name, " ", el("span", { class: "muted" }, t.slug)), t.hasExtraCss ? el("span", { class: "badge", title: "has an extra CSS layer applied by FloofyCrew" }, "look") : null, t.installed ? el("span", { class: `badge${active === `custom-${t.slug}` ? " on" : ""}` }, active === `custom-${t.slug}` ? "active" : "installed") : null, swatch(t.swatch && t.swatch.dark))) : el("p", { class: "hint" }, "Nothing yet. Start from a built-in theme below, adopt an installed one, or import a file.")), el("div", { class: "row", style: { marginTop: ".5rem" } }, el("button", { type: "button", "data-testid": "custom-themes-new-blank", onclick: () => create("blank") }, "New blank theme"), el("button", { type: "button", "data-testid": "custom-themes-import", onclick: importFile }, "Import…"))),
    );
    const foreign = list.installed.filter((t) => !t.inLibrary);
    if (foreign.length) {
      sidebar.append(el("div", { class: "card" }, el("h3", {}, "Installed on the host, not in the library"), el("div", { class: "list" }, foreign.map((t) => el("button", { type: "button", class: "item", onclick: () => create(`installed:${t.slug}`, { name: t.name, slug: t.slug, emoji: t.emoji }) }, el("span", {}, t.emoji), el("span", { class: "name" }, t.name, " ", el("span", { class: "muted" }, t.slug)), el("span", { class: "badge" }, t.kind === "record" ? "editor record" : `level ${t.level}`), el("span", { class: "muted", style: { fontSize: ".75rem" } }, "adopt →")))), el("p", { class: "hint" }, "Adopting copies the installed pack into the library so you can edit it here (the pack a theme mod installed, a GitHub install, a record the host's own editor made).")));
    }
    const presets = state.presets;
    sidebar.append(
      el(
        "div",
        { class: "card" },
        el("h3", {}, "Start from a built-in theme"),
        presets === null ? el("p", { class: "hint" }, "Reading the host's stylesheet…") : presets.length === 0 ? el("p", { class: "hint" }, "No built-in theme found in the host's stylesheet.") : el("div", { class: "presets", "data-testid": "custom-themes-presets" }, presets.map((p) => el("button", { type: "button", class: "preset", "data-preset": p.slug, title: `${p.label}: ${p.variables} variables${p.decorated ? ", with its decorations as an extra CSS layer" : ""}`, onclick: () => create(`preset:${p.slug}`, { name: `My ${p.label}`, slug: `my-${p.slug}`, emoji: p.emoji }) }, el("span", { class: "bar" }, ...["--bg", "--card", "--accent", "--text"].map((k) => el("i", { style: { background: (p.swatch && p.swatch.dark && p.swatch.dark[k]) || "transparent" } }))), el("span", { class: "label" }, p.emoji ? `${p.emoji} ` : "", p.label), p.decorated ? el("span", { class: "badge" }, "decorated") : null))),
        el("p", { class: "hint" }, "Presets are read from the running host's own stylesheet, so this list is exactly what this KiroCrew ships. A decorated preset carries the theme's extra rules (backgrounds, glows, scanlines) as a FloofyCrew-applied layer the host's own installer would drop; effects driven by the host's scripts do not carry over."),
      ),
    );
  }

  async function importFile() {
    const file = await pickFile(".json,application/json");
    if (!file) return;
    try {
      const text = await file.text();
      const document_ = JSON.parse(text);
      const proposed = document_.slug || slugify(file.name.replace(/\.floofy-theme\.json$|\.json$/i, ""));
      const slug = await api.dialog.prompt("Import the theme under this slug (its id on the host):", { title: `Import ${file.name}`, label: "Slug", value: proposed, okLabel: "Import", validate: (v) => (SLUG_RE.test(v) ? null : "lower-case letters, digits and dashes, starting with a letter or digit, at most 40 characters") });
      if (slug === null) return;
      let result;
      try {
        result = await call(`import?slug=${slug}`, { method: "POST", body: JSON.stringify(document_) });
      } catch (error) {
        if (!/already exists/.test(error.message) || !(await api.dialog.confirm(`${error.message}\n\nReplace it?`, { title: "Theme already exists", okLabel: "Replace", cancelLabel: "Cancel", danger: true }))) throw error;
        result = await call(`import?slug=${slug}&overwrite=1`, { method: "POST", body: JSON.stringify(document_) });
      }
      say(`imported ${result.theme.slug}`, "ok");
      await loadList();
      await open(result.theme.slug);
    } catch (error) {
      say(`import: ${error.message}`, "err");
    }
  }

  // -- editor ------------------------------------------------------------------------------

  const editor = el("div", { class: "card", "data-testid": "custom-themes-editor" });
  const saveButton = el("button", { type: "button", class: "primary", "data-testid": "custom-themes-save", disabled: true, onclick: save }, "Save");

  async function save() {
    if (!state.doc) return;
    try {
      const result = await call(`themes/${state.doc.slug}`, { method: "PUT", body: JSON.stringify(state.doc) });
      state.doc = result.theme;
      state.dirty = false;
      saveButton.disabled = true;
      say(`saved ${state.doc.slug} (level ${state.doc.manifest.level})${state.doc.installed ? " — installed copy is older; Install again to update the host" : ""}`, "ok");
      await loadList();
      renderEditor();
    } catch (error) {
      say(error.message, "err");
    }
  }
  async function install() {
    if (!state.doc) return;
    if (state.dirty) await save();
    if (state.dirty) return;
    try {
      const result = await call(`themes/${state.doc.slug}/install`, { method: "POST" });
      window.dispatchEvent(new Event("mc-custom-themes-changed"));
      window.dispatchEvent(new Event(CHANGED_EVENT));
      say(`installed ${result.installed.slug} into the host (level ${result.installed.level}, ${result.installed.files.length} files) — it is now listed in Settings → Display`, "ok");
      await loadList();
      state.doc.installed = true;
      renderEditor();
    } catch (error) {
      say(error.message, "err");
    }
  }
  async function uninstall() {
    if (!state.doc || !(await api.dialog.confirm(`Remove ${state.doc.slug} from the host's themes? The library copy stays.`, { title: "Uninstall from the host", okLabel: "Uninstall", cancelLabel: "Cancel", danger: true }))) return;
    try {
      await call(`themes/${state.doc.slug}/uninstall`, { method: "POST" });
      window.dispatchEvent(new Event("mc-custom-themes-changed"));
      say(`uninstalled ${state.doc.slug} from the host`, "ok");
      state.doc.installed = false;
      await loadList();
      renderEditor();
    } catch (error) {
      say(error.message, "err");
    }
  }
  async function remove() {
    if (!state.doc) return;
    const slug = state.doc.slug;
    const also = state.doc.installed ? await api.dialog.confirm(`Also remove ${slug} from the host's themes? ("Keep" leaves the installed copy in place.)`, { title: "Delete the theme", okLabel: "Remove from the host too", cancelLabel: "Keep the installed copy", danger: true }) : false;
    if (!(await api.dialog.confirm(`Delete ${slug} from the library?`, { title: "Delete the theme", okLabel: "Delete", cancelLabel: "Cancel", danger: true }))) return;
    if (!state.doc || state.doc.slug !== slug) return;
    try {
      await call(`themes/${state.doc.slug}${also ? "?uninstall=1" : ""}`, { method: "DELETE" });
      if (also) window.dispatchEvent(new Event("mc-custom-themes-changed"));
      say(`deleted ${state.doc.slug}`, "ok");
      state.selected = null;
      state.doc = null;
      state.dirty = false;
      stopPreview();
      await loadList();
      renderEditor();
    } catch (error) {
      say(error.message, "err");
    }
  }
  async function duplicate() {
    if (!state.doc) return;
    await create(`library:${state.doc.slug}`, { name: `${state.doc.manifest.name} copy`, slug: `${state.doc.slug}-copy` });
  }
  async function exportTheme() {
    if (!state.doc) return;
    try {
      const text = await call(`themes/${state.doc.slug}/export`);
      download(`${state.doc.slug}.floofy-theme.json`, typeof text === "string" ? text : JSON.stringify(text, null, 2));
    } catch (error) {
      say(error.message, "err");
    }
  }

  function renderEditor() {
    editor.replaceChildren();
    const doc = state.doc;
    if (!doc) {
      editor.append(el("h2", {}, "Custom themes"), el("p", { class: "muted", style: { margin: ".4rem 0 0" } }, "Pick a theme on the left to edit it, or start a new one. A theme is a KiroCrew theme pack (palette, branding, fonts) plus a FloofyCrew layer of extra CSS that can restyle anything — the part the host's own installer would drop."), el("p", { class: "hint" }, `Host ${state.hostVersion} · active theme ${state.activeTheme || "default"} · ${api.id} ${api.unofficial ? "(unofficial)" : ""}`));
      return;
    }
    const m = doc.manifest;
    const isActive = state.activeTheme === `custom-${doc.slug}`;
    const head = el(
      "div",
      { class: "row", style: { justifyContent: "space-between" } },
      el("h2", {}, `${m.emoji || "🎨"} ${m.name}`, " ", el("span", { class: "muted", style: { fontSize: ".8rem", fontWeight: 400 } }, `custom-${doc.slug} · level ${m.level ?? 0}`), doc.installed ? el("span", { class: `badge${isActive ? " on" : ""}`, style: { marginLeft: ".5rem" } }, isActive ? "active" : "installed") : null),
      el(
        "div",
        { class: "row" },
        el("label", { class: "row", style: { fontSize: ".8rem", gap: ".3rem" } }, el("input", { type: "checkbox", "data-testid": "custom-themes-preview", checked: state.preview, onchange: (e) => { state.preview = e.target.checked; if (state.preview) refreshPreview(); else stopPreview(); } }), "Live preview"),
        saveButton,
        el("button", { type: "button", "data-testid": "custom-themes-install", onclick: install }, doc.installed ? "Install again" : "Install to host"),
        doc.installed && !isActive ? el("button", { type: "button", "data-testid": "custom-themes-activate", onclick: () => activateTheme(doc.slug) }, "Use this theme") : null,
        el("button", { type: "button", onclick: exportTheme }, "Export"),
        el("button", { type: "button", onclick: duplicate }, "Duplicate"),
        doc.installed ? el("button", { type: "button", onclick: uninstall }, "Uninstall") : null,
        el("button", { type: "button", class: "danger", "data-testid": "custom-themes-delete", onclick: remove }, "Delete"),
      ),
    );
    const tabs = el("div", { class: "tabs" }, ...[["palette", "Palette"], ["look", "Look (extra CSS)"], ["pack", "Pack"], ["branding", "Branding"]].map(([id, label]) => el("button", { type: "button", class: id === state.tab ? "on" : "", "data-tab": id, onclick: () => { state.tab = id; renderEditor(); } }, label)));
    const body = el("div", {});
    if (state.tab === "palette") body.append(paletteTab(doc));
    else if (state.tab === "look") body.append(lookTab(doc));
    else if (state.tab === "pack") body.append(packTab(doc));
    else body.append(brandingTab(doc));
    editor.append(head, tabs, body);
  }

  function paletteTab(doc) {
    const wrap = el("div", {});
    const modes = el("div", { class: "row", style: { marginBottom: ".6rem" } }, ...["dark", "light"].map((mode) => el("button", { type: "button", class: state.mode === mode ? "primary" : "", "data-mode": mode, onclick: () => { state.mode = mode; renderEditor(); } }, mode === "dark" ? "Dark" : "Light")), el("span", { class: "muted", style: { fontSize: ".8rem" } }, `${Object.keys(doc.variables[state.mode] || {}).length} of ${ALL_VARS.length} set · unset tokens are derived from --bg / --text / --accent by the host`), el("button", { type: "button", style: { marginLeft: "auto" }, onclick: async () => { const from = state.mode; const other = from === "dark" ? "light" : "dark"; if (await api.dialog.confirm(`Copy every ${from} value over the ${other} palette?`, { title: `Copy ${from} → ${other}`, okLabel: "Copy" })) { doc.variables[other] = { ...doc.variables[from] }; touched(); renderEditor(); } } }, `Copy ${state.mode} → ${state.mode === "dark" ? "light" : "dark"}`));
    wrap.append(modes);
    const block = (doc.variables[state.mode] = doc.variables[state.mode] || {});
    for (const [group, names] of VAR_GROUPS) {
      const grid = el("div", { class: "vars" });
      for (const name of names) {
        const text = el("input", { type: "text", value: block[name] || "", placeholder: REQUIRED.has(name) ? "required" : "derived", "data-var": name, spellcheck: "false" });
        const hex = toHex6(block[name]);
        const color = el("input", { type: "color", value: hex || "#000000", style: { visibility: hex || !block[name] ? "visible" : "hidden" } });
        color.addEventListener("input", () => { text.value = color.value; block[name] = color.value; touched(); });
        text.addEventListener("input", () => { const v = text.value.trim(); if (v) block[name] = v; else delete block[name]; const h = toHex6(v); if (h) color.value = h; color.style.visibility = h || !v ? "visible" : "hidden"; touched(); });
        const clear = el("button", { type: "button", class: "clear", title: "unset (derive)", onclick: () => { delete block[name]; text.value = ""; color.style.visibility = "visible"; touched(); } }, "×");
        grid.append(el("div", { class: `var${REQUIRED.has(name) ? " required" : ""}` }, el("code", { title: name }, name), color, text, clear));
      }
      wrap.append(el("h3", { style: { marginTop: ".6rem" } }, group), grid);
    }
    return wrap;
  }

  function lookTab(doc) {
    const area = el("textarea", { "data-testid": "custom-themes-extra", spellcheck: "false", placeholder: "/* any selector, any rule — scoped to this theme for you */\n.topbar-glass { backdrop-filter: blur(10px); }\nbody::before { content: ''; position: fixed; inset: 0; pointer-events: none; background: repeating-linear-gradient(#fff1 0 1px, transparent 1px 3px); }\n\n@floofy-mode dark {\n  body { background: radial-gradient(circle at top, #223, #000) fixed; }\n}" }, doc.extraCss || "");
    area.addEventListener("input", () => { doc.extraCss = area.value; touched(); });
    return el("div", {}, el("p", { class: "hint", style: { marginBottom: ".5rem" } }, "The FloofyCrew layer. Write rules unscoped; the mod prefixes every selector with ", el("kbd", {}, `[data-theme="custom-${doc.slug}-dark|light"]`), " and applies the sheet itself (first frame included), so it never leaks onto another theme and is not subject to the host's runtime allowlist. ", el("kbd", {}, "@floofy-mode dark { … }"), " limits rules to one mode; ", el("kbd", {}, "url(pack:branding/logo.png)"), " points at a file the pack ships. Forbidden: @import, other origins, </style>."), area);
  }

  function packTab(doc) {
    const m = doc.manifest;
    const field = (label, attrs, oninput) => { const input = el("input", { type: "text", ...attrs }); input.addEventListener("input", () => oninput(input.value)); return el("label", { class: "f" }, label, input); };
    const branding = m.branding || {};
    const fontsArea = el("textarea", { style: { minHeight: "7rem" }, spellcheck: "false", placeholder: '[{"family": "My Sans", "file": "my-sans-400.woff2", "weight": 400, "role": "sans"}]' }, m.fonts && m.fonts.length ? JSON.stringify(m.fonts, null, 2) : "");
    const fontsStatus = el("span", { class: "hint" });
    fontsArea.addEventListener("input", () => {
      const text = fontsArea.value.trim();
      if (!text) { delete m.fonts; fontsStatus.textContent = ""; touched(); return; }
      try { const parsed = JSON.parse(text); if (!Array.isArray(parsed)) throw new Error("must be a JSON array"); m.fonts = parsed; fontsStatus.textContent = `${parsed.length} face(s)`; touched(); } catch (error) { fontsStatus.textContent = `not saved: ${error.message}`; }
    });
    const overrides = el("textarea", { style: { minHeight: "9rem" }, spellcheck: "false", placeholder: "/* the host's own overrides.css: installs into the pack; only body, .topbar, .sidebar, .chat-container, .message-bubble, .input-area, .code-block and button.primary render at runtime */" }, doc.overridesCss || "");
    overrides.addEventListener("input", () => { doc.overridesCss = overrides.value; touched(); });
    return el(
      "div",
      {},
      el("div", { class: "grid2" }, field("Name", { value: m.name || "", maxlength: 60, "data-field": "name" }, (v) => { m.name = v; touched(); }), field("Emoji", { value: m.emoji || "", maxlength: 4, "data-field": "emoji" }, (v) => { m.emoji = v; touched(); }), field("Bot name (branding)", { value: branding.botName || "", maxlength: 48, placeholder: "unchanged", "data-field": "botName" }, (v) => { if (v.trim()) m.branding = { ...(m.branding || {}), botName: v }; else delete m.branding; touched(); }), field("Loader icons (4–8, comma-separated)", { value: (m.loaderIcons || []).join(", "), placeholder: "cloud, flower, heart, moon, sparkles, star, sun, zap" }, (v) => { const names = v.split(",").map((s) => s.trim()).filter(Boolean); if (names.length) m.loaderIcons = names; else delete m.loaderIcons; touched(); })),
      el("h3", { style: { marginTop: ".8rem" } }, "Fonts (theme.json fonts[])"),
      fontsArea,
      fontsStatus,
      el("p", { class: "hint" }, "Upload the .woff2 / .ttf files under Branding; each entry here names one of them with a role (sans or mono) and a weight. The host routes the faces through its Font Family preference; never set fonts in CSS."),
      el("h3", { style: { marginTop: ".8rem" } }, "styles/overrides.css (the host's own layer)"),
      overrides,
      el("p", { class: "hint" }, "Ships inside the pack and renders even without FloofyCrew, but the host keeps only rules on a handful of surfaces. Anything else belongs in Look."),
      el("h3", { style: { marginTop: ".8rem" } }, "Notes"),
      (() => { const notes = el("textarea", { style: { minHeight: "4rem", fontFamily: "inherit", whiteSpace: "pre-wrap" } }, (doc.meta && doc.meta.notes) || ""); notes.addEventListener("input", () => { doc.meta = { ...(doc.meta || {}), notes: notes.value }; touched(); }); return notes; })(),
      el("p", { class: "hint" }, `Based on ${(doc.meta && doc.meta.basedOn) || "—"} · created ${(doc.meta && doc.meta.createdAt) || "—"} · updated ${(doc.meta && doc.meta.updatedAt) || "—"}`),
    );
  }

  function brandingTab(doc) {
    const wrap = el("div", {});
    const slots = [
      ["branding/logo.png", "Logo (png or svg, ≤100 KB) — the sidebar mark", "image/png,image/svg+xml"],
      ["branding/favicon.png", "Favicon (png, ico or svg, ≤50 KB)", "image/png,image/x-icon,image/vnd.microsoft.icon,image/svg+xml"],
      ["branding/wordmark.svg", "Wordmark (svg or png, ≤100 KB)", "image/svg+xml,image/png"],
      ["branding/preview.png", "Preview (png or webp, ≤512 KB) — shown in the theme picker", "image/png,image/webp"],
    ];
    const have = new Map((doc.assets || []).map((a) => [a.path, a]));
    const assetUrl = (rel) => (doc.installed ? `/api/theme/${encodeURIComponent(doc.slug)}/assets/${rel}` : null);
    async function upload(rel, accept) {
      const file = await pickFile(accept);
      if (!file) return;
      const ext = (file.name.split(".").pop() || "").toLowerCase();
      const base = rel.split("/")[1].split(".")[0];
      const target = `${base}.${ext === "jpeg" ? "jpg" : ext}`;
      try {
        const data = await readFileAsBase64(file);
        const result = await call(`assets/${doc.slug}/branding/${target}`, { method: "POST", body: JSON.stringify({ data }) });
        state.doc = result.theme;
        say(`uploaded branding/${target} (${file.size} bytes)`, "ok");
        renderEditor();
      } catch (error) {
        say(error.message, "err");
      }
    }
    async function removeAsset(path) {
      const [kind, name] = path.startsWith("styles/fonts/") ? ["fonts", path.slice("styles/fonts/".length)] : ["branding", path.slice("branding/".length)];
      try {
        const result = await call(`assets/${doc.slug}/${kind}/${name}`, { method: "POST", body: JSON.stringify({ remove: true }) });
        state.doc = result.theme;
        renderEditor();
      } catch (error) {
        say(error.message, "err");
      }
    }
    for (const [rel, label, accept] of slots) {
      const base = rel.split("/")[1].split(".")[0];
      const present = [...have.values()].find((a) => a.path.startsWith(`branding/${base}.`));
      const url = present && assetUrl(present.path);
      wrap.append(el("div", { class: "asset" }, url ? el("img", { src: url, alt: "" }) : el("span", { class: "swatch", style: { width: "28px", background: "var(--bg-hover, transparent)" } }), el("span", { style: { flex: 1 } }, label, present ? el("span", { class: "muted" }, ` · ${present.path} (${present.bytes} bytes)`) : ""), el("button", { type: "button", onclick: () => upload(rel, accept) }, present ? "Replace" : "Upload"), present ? el("button", { type: "button", class: "danger", onclick: () => removeAsset(present.path) }, "Remove") : null));
    }
    const fonts = [...have.values()].filter((a) => a.path.startsWith("styles/fonts/"));
    wrap.append(el("h3", { style: { marginTop: ".8rem" } }, "Font files (styles/fonts/, .woff2 or .ttf, ≤512 KB each, 6 at most)"));
    for (const font of fonts) wrap.append(el("div", { class: "asset" }, el("span", { style: { flex: 1 } }, font.path, el("span", { class: "muted" }, ` (${font.bytes} bytes)`)), el("button", { type: "button", class: "danger", onclick: () => removeAsset(font.path) }, "Remove")));
    wrap.append(el("div", { class: "row", style: { marginTop: ".4rem" } }, el("button", { type: "button", onclick: async () => { const file = await pickFile(".woff2,.ttf,font/woff2,font/ttf"); if (!file) return; const name = file.name.toLowerCase().replace(/[^a-z0-9_.-]/g, "-"); try { const data = await readFileAsBase64(file); const result = await call(`assets/${doc.slug}/fonts/${name}`, { method: "POST", body: JSON.stringify({ data }) }); state.doc = result.theme; say(`uploaded styles/fonts/${name}`, "ok"); renderEditor(); } catch (error) { say(error.message, "err"); } } }, "Upload a font file")));
    wrap.append(el("p", { class: "hint" }, doc.installed ? "Previews come from the installed copy; after replacing a file, Install again to see it." : "Previews appear once the theme is installed on the host."));
    return wrap;
  }

  // -- layout and first load ---------------------------------------------------------------

  root.append(el("div", { class: "row", style: { justifyContent: "space-between" } }, el("div", {}, el("strong", {}, "Custom themes"), " ", el("span", { class: "muted" }, "— a theme editor for KiroCrew, as a FloofyCrew mod. Unofficial.")), status), el("div", { class: "cols" }, sidebar, editor));
  try {
    const info = await call("status");
    state.hostVersion = info.host.version;
    state.activeTheme = info.activeTheme;
  } catch (error) {
    say(error.message, "err");
  }
  renderEditor();
  await loadList();
  void loadPresets();

  return () => {
    stopPreview();
    root.remove();
  };
}
