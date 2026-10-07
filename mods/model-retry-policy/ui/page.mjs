// Model Retry Policy — the mod's page inside the FloofyCrew App.
//
// Imported same-origin by the App and mounted with `mount(container, api)`, where
// `api = floofy.mod("model-retry-policy")` (docs/spa-api.md). The page edits the mod's
// config (api.config.set → .floofy/config.json, which the gateway hook re-reads on every
// retry decision) and polls the hook's `status` route for the live picture: what is in
// force, the retry / swap / exhaustion counters and the recent events. The "what will
// happen" timeline is computed by the hook's `preview` route from the config being
// edited, so the user sees the attempt sequence before saving. No framework, no
// third-party origin, no browser pop-ups (api.dialog is the App's own overlay). Icons
// are inline SVG drawn here.
"use strict";

const MECHANISMS = Object.freeze({
  host: { label: "KiroCrew default", short: "3 retries, then agent.fallback_model", help: "KiroCrew's own behaviour: three same-model retries on a 2 s doubling curve, then the agent.fallback_model chain (two attempts per candidate), then the error. An error that lands after the turn already produced output (text or a tool call) is recovered once, then shown. Nothing is overridden." },
  count: { label: "Retry N times", short: "a retry count of your choosing", help: "Retry the same model N times (on the host's doubling curve unless you pick another below), then hand over to KiroCrew's own agent.fallback_model chain, then the error. A turn that already produced output is recovered up to N times too." },
  infinite_fixed: { label: "Retry forever, fixed interval", short: "every N seconds until it works", help: "Never give up on the selected model: retry every N seconds until the turn succeeds or you press Stop — whether the error lands before any output or in the middle of a long tool-using turn." },
  infinite_exponential: { label: "Retry forever, backoff", short: "exponential backoff up to a cap", help: "Never give up on the selected model: back off exponentially between retries (base × factor^n, capped) until the turn succeeds or you press Stop — whether the error lands before any output or in the middle of a long tool-using turn." },
  fallback: { label: "Fallback profile", short: "switch models after N retries", help: "Retry the primary model N times, then move to the next model in the profile for its own number of attempts, and so on. When the chain is exhausted either show the error or keep retrying the last model forever. KiroCrew cannot switch models in the middle of a turn, so an error that lands after output was produced is recovered on the current model, as many times as the profile allows in total (forever with \"keep retrying\")." },
});

const SVG_NS = "http://www.w3.org/2000/svg";

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

function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) if (value !== null && value !== undefined) node.setAttribute(key, String(value));
  for (const child of children) if (child !== null && child !== undefined) node.append(child);
  return node;
}

// --- mechanism icons (24×24, stroke = currentColor) -------------------------------------------------------
function mechanismIcon(name) {
  const root = svg("svg", { viewBox: "0 0 24 24", width: 40, height: 40, fill: "none", stroke: "currentColor", "stroke-width": 1.7, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
  switch (name) {
    case "host": // a shield with a tick: leave it alone
      root.append(svg("path", { d: "M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6z" }), svg("path", { d: "M9 12l2 2 4-4" }));
      break;
    case "count": // a loop with three ticks: retry N
      root.append(svg("path", { d: "M17.5 6.5A7 7 0 1 0 19 12" }), svg("path", { d: "M17 3v4h-4" }), svg("path", { d: "M8 12h.01M12 12h.01M16 12h.01", "stroke-width": 2.6 }));
      break;
    case "infinite_fixed": // infinity over an even tick row
      root.append(svg("path", { d: "M8 12c-2 0-3.5-1.3-3.5-3s1.5-3 3.5-3c2.7 0 5.3 6 8 6 2 0 3.5-1.3 3.5-3s-1.5-3-3.5-3c-2.7 0-5.3 6-8 6z", transform: "translate(0,-1)" }), svg("path", { d: "M4 19h16" }), svg("path", { d: "M6 17v2M10 17v2M14 17v2M18 17v2" }));
      break;
    case "infinite_exponential": // infinity over a widening tick row
      root.append(svg("path", { d: "M8 12c-2 0-3.5-1.3-3.5-3s1.5-3 3.5-3c2.7 0 5.3 6 8 6 2 0 3.5-1.3 3.5-3s-1.5-3-3.5-3c-2.7 0-5.3 6-8 6z", transform: "translate(0,-1)" }), svg("path", { d: "M4 19h16" }), svg("path", { d: "M5 17v2M7 17v2M10.5 17v2M15.5 17v2" }));
      break;
    default: // fallback: a node hands over to the next, then the next
      root.append(svg("circle", { cx: 5, cy: 12, r: 2.4, fill: "currentColor" }), svg("path", { d: "M7.6 12h3.2M9.6 10.2l1.8 1.8-1.8 1.8" }), svg("circle", { cx: 13.5, cy: 12, r: 2.4 }), svg("path", { d: "M16 12h2.6M17.4 10.2l1.8 1.8-1.8 1.8", "stroke-dasharray": "1.6 1.6" }), svg("circle", { cx: 21, cy: 12, r: 1.4, "stroke-dasharray": "1.4 1.4" }));
  }
  return root;
}

const ago = (ts) => {
  if (!ts) return "–";
  const d = Date.now() / 1000 - ts;
  return d < 60 ? `${Math.round(d)}s ago` : d < 3600 ? `${Math.round(d / 60)}m ago` : `${Math.round(d / 3600)}h ago`;
};
const fmtS = (s) => (s === null || s === undefined ? "–" : s >= 3600 ? `${(s / 3600).toFixed(1)} h` : s >= 60 ? `${(s / 60).toFixed(1)} min` : `${Number(s).toFixed(s < 10 ? 1 : 0)} s`);
const isPlainObject = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const deepMerge = (base, patch) => {
  const out = isPlainObject(base) ? { ...base } : {};
  for (const [k, v] of Object.entries(patch || {})) out[k] = isPlainObject(v) ? deepMerge(out[k], v) : v;
  return out;
};
function stripNulls(value) {
  if (Array.isArray(value)) return value.filter((v) => v !== null).map(stripNulls);
  if (isPlainObject(value)) { const out = {}; for (const [k, v] of Object.entries(value)) if (v !== null) out[k] = stripNulls(v); return out; }
  return value;
}

// The document config.set receives for *patch*: config.set merges TOP-LEVEL keys only
// (null deletes one), while the policy's keys are nested objects. So each object-valued
// key is deep-merged against the current document and sent whole, with nulls inside it
// removed (a null under `fallback.profiles` deletes that profile). A scalar key — the
// `enabled` flag — is sent as it is: spreading a boolean through the merge would turn
// it into `{}`, which the hook reads as "off" (that was the bug every click hit).
export function mergePatch(current, patch) {
  const full = {};
  for (const [key, value] of Object.entries(patch || {})) {
    full[key] = value === null ? null : isPlainObject(value) ? stripNulls(deepMerge((current || {})[key], value)) : value;
  }
  return full;
}

export default async function mount(container, api) {
  const tokens = api.theme.tokens();
  const accent = tokens["--accent"] || "#ff6b8b";
  const muted = tokens["--text-muted"] || "inherit";
  const border = tokens["--border"] || "rgba(127,127,127,0.35)";
  const mono = { fontFamily: "var(--mono, monospace)", fontSize: "0.8rem" };
  const btn = (label, onclick, extra = {}) =>
    el("button", { type: "button", onclick, style: { borderColor: accent, color: accent, padding: "0.25rem 0.6rem", borderRadius: "4px", background: "transparent", cursor: "pointer", ...extra } }, label);
  const small = (text) => el("span", { style: { color: muted, fontSize: "0.78rem" } }, text);

  let status = null;
  let config = await api.config.get();
  let models = []; // ids the dashboard's model picker knows
  const cfg = () => (status && status.config) || config;

  const note = el("span", { "data-testid": "mrp-note", style: { color: muted, marginLeft: "0.6rem" } }, "");
  async function save(patch) {
    note.textContent = "saving…";
    const current = await api.config.get();
    config = await api.config.set(mergePatch(current, patch));
    try { await api.routes.fetch("apply", { method: "POST" }); } catch (error) { api.log.warn("apply failed", error); }
    note.textContent = `saved ${new Date().toLocaleTimeString()}`;
    await refresh();
  }

  async function loadModels() {
    try {
      const response = await fetch("/api/models", { credentials: "same-origin" });
      if (!response.ok) return;
      const data = await response.json();
      const list = Array.isArray(data) ? data : (data && data.models) || [];
      models = list.map((m) => (typeof m === "string" ? m : m.model_id || m.model_name || m.id || "")).filter(Boolean);
    } catch (error) {
      api.log.warn("model list unavailable", error);
    }
  }

  // --- mechanism cards ---------------------------------------------------------------------------------
  const group = el("div", { role: "radiogroup", "aria-label": "retry mechanism", "data-testid": "mrp-mechanism", style: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(13rem, 1fr))", gap: "0.5rem" } });
  const enabled = el("input", { type: "checkbox", "data-testid": "mrp-enabled" });
  enabled.addEventListener("change", () => save({ enabled: enabled.checked }));
  const help = el("p", { "data-testid": "mrp-help", style: { color: muted, margin: "0.2rem 0 0", fontSize: "0.85rem" } }, "");

  async function pick(name) {
    const c = cfg();
    if (name === "fallback") {
      const profiles = (c.fallback && c.fallback.profiles) || {};
      let id = c.fallback && c.fallback.profile;
      if (!id || !profiles[id]) id = Object.keys(profiles)[0];
      if (!id) { id = await newProfile(); if (!id) return; }
      return save({ fallback: { enabled: true, profile: id } });
    }
    const patch = { fallback: { enabled: false } };
    if (name === "host") Object.assign(patch, { retries: { mode: "host" }, delay: { mode: "host" } });
    if (name === "count") patch.retries = { mode: "count" };
    if (name === "infinite_fixed") Object.assign(patch, { retries: { mode: "infinite" }, delay: { mode: "fixed" } });
    if (name === "infinite_exponential") Object.assign(patch, { retries: { mode: "infinite" }, delay: { mode: "exponential" } });
    return save(patch);
  }

  function renderMechanisms(current) {
    group.replaceChildren();
    for (const [name, meta] of Object.entries(MECHANISMS)) {
      const selected = name === current;
      group.append(el("button", {
        type: "button", role: "radio", "aria-checked": selected, "data-mechanism": name, title: meta.help,
        onclick: () => { if (name !== current) pick(name); },
        style: {
          display: "flex", alignItems: "center", gap: "0.6rem", textAlign: "left", cursor: "pointer", padding: "0.5rem 0.6rem", borderRadius: "4px",
          border: `1px solid ${selected ? accent : border}`, background: selected ? `color-mix(in srgb, ${accent} 12%, transparent)` : "transparent", color: selected ? accent : "inherit",
        },
      }, mechanismIcon(name), el("div", {}, el("div", { style: { fontWeight: 600 } }, meta.label), el("div", { style: { color: muted, fontSize: "0.72rem", lineHeight: 1.25 } }, meta.short))));
    }
  }

  // --- parameters ------------------------------------------------------------------------------------------
  const numField = (path, label, { step = 1, min = 0, width = "6rem", suffix = "" } = {}) => {
    const input = el("input", { type: "number", step, min, "data-testid": `mrp-${path.join("-")}`, style: { width } });
    input.addEventListener("change", () => {
      const v = Number(input.value);
      if (!Number.isFinite(v)) return;
      const patch = {}; let cursor = patch;
      path.slice(0, -1).forEach((k) => { cursor[k] = {}; cursor = cursor[k]; });
      cursor[path[path.length - 1]] = v;
      save(patch);
    });
    const row = el("label", { style: { display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" } }, label, input, suffix ? small(suffix) : null);
    return { row, input, path };
  };
  const readPath = (obj, path) => path.reduce((o, k) => (o && o[k] !== undefined ? o[k] : undefined), obj);

  const countField = numField(["retries", "count"], "retries on the same model", { min: 0 });
  const intervalField = numField(["delay", "interval_s"], "interval", { step: 1, suffix: "s" });
  const baseField = numField(["delay", "base_s"], "base", { step: 0.5, suffix: "s" });
  const factorField = numField(["delay", "factor"], "× factor", { step: 0.5, min: 1, width: "4.5rem" });
  const maxField = numField(["delay", "max_s"], "cap", { step: 10, suffix: "s" });
  const jitterField = numField(["delay", "jitter"], "jitter", { step: 0.05, width: "4.5rem", suffix: "× delay, added at random" });
  const allFields = [countField, intervalField, baseField, factorField, maxField, jitterField];

  const delayMode = el("select", { "data-testid": "mrp-delay-mode" },
    el("option", { value: "host" }, "KiroCrew's curve (2 s doubling)"),
    el("option", { value: "fixed" }, "fixed interval"),
    el("option", { value: "exponential" }, "exponential backoff"));
  delayMode.addEventListener("change", () => save({ delay: { mode: delayMode.value } }));

  const paramsBox = el("div", { "data-testid": "mrp-params", style: { display: "flex", flexWrap: "wrap", gap: "0.6rem 1.2rem", alignItems: "center" } });
  const paramsHint = el("div", { style: { color: muted, fontSize: "0.78rem" } }, "");

  function renderParams() {
    const c = cfg();
    const preset = (status && status.effective && status.effective.preset) || "host";
    for (const f of allFields) { const v = readPath(c, f.path); if (v !== undefined) f.input.value = v; }
    delayMode.value = c.delay.mode;
    paramsBox.replaceChildren();
    if (preset === "host") { paramsHint.textContent = "Nothing to set — every retry decision is KiroCrew's own."; return; }
    if (preset === "count") paramsBox.append(countField.row);
    const showDelay = preset !== "host";
    if (showDelay) paramsBox.append(el("label", { style: { display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" } }, "between retries:", delayMode));
    const mode = c.delay.mode;
    if (mode === "fixed") paramsBox.append(intervalField.row);
    if (mode === "exponential") paramsBox.append(baseField.row, factorField.row, maxField.row);
    if (mode === "host" && preset !== "host") paramsBox.append(maxField.row);
    if (mode !== "host" || preset !== "host") paramsBox.append(jitterField.row);
    paramsHint.textContent = preset.startsWith("infinite") ? "Forever means until the turn succeeds, you press Stop, the session is reset or the gateway restarts."
      : preset === "count" ? "After these retries, KiroCrew's own agent.fallback_model chain still runs (if configured), then the error is shown."
      : preset === "fallback" ? "The profile below decides how many retries each model gets; the curve here is the wait between them."
      : "";
  }

  // --- fallback profiles ------------------------------------------------------------------------------------
  const profilesBox = el("div", { "data-testid": "mrp-profiles", style: { display: "flex", flexDirection: "column", gap: "0.6rem" } });

  async function newProfile() {
    const answer = await api.dialog.form({
      title: "New fallback profile",
      text: "Give the profile a name. You add the models and their retry counts next.",
      fields: [
        { name: "name", label: "name", placeholder: "e.g. Night shift", validate: (v) => (v.trim() ? "" : "required") },
        { name: "primary_retries", label: "retries on the primary model before the first switch", value: "3", validate: (v) => (/^\d+$/.test(v.trim()) ? "" : "a whole number") },
      ],
      okLabel: "Create",
    });
    if (!answer) return null;
    const id = `p-${Date.now().toString(36)}`;
    const profiles = { ...((cfg().fallback && cfg().fallback.profiles) || {}) };
    profiles[id] = { name: answer.name.trim(), primary_retries: Number(answer.primary_retries), steps: [], exhausted: "stop" };
    await save({ fallback: { profiles, profile: id, enabled: true } });
    return id;
  }

  async function addStep(id) {
    const hint = models.length ? `Known ids: ${models.slice(0, 12).join(", ")}${models.length > 12 ? ", …" : ""}. "auto" lets the backend pick.` : 'Type a model id exactly as the model picker shows it; "auto" lets the backend pick.';
    const answer = await api.dialog.form({
      title: "Add a fallback step",
      text: hint,
      fields: [
        { name: "model", label: "model id", value: models[0] || "auto", validate: (v) => (v.trim() && !/\s/.test(v.trim()) ? "" : "a model id without spaces") },
        { name: "retries", label: "attempts on this model before moving on", value: "2", validate: (v) => (/^[1-9]\d*$/.test(v.trim()) ? "" : "1 or more") },
      ],
      okLabel: "Add",
    });
    if (!answer) return;
    const profiles = structuredClone(cfg().fallback.profiles || {});
    profiles[id].steps = [...(profiles[id].steps || []), { model: answer.model.trim(), retries: Number(answer.retries) }];
    await save({ fallback: { profiles } });
  }

  function renderProfiles() {
    const c = cfg();
    const fb = c.fallback || {};
    const profiles = fb.profiles || {};
    const activeId = fb.enabled ? fb.profile : "";
    profilesBox.replaceChildren();
    const ids = Object.keys(profiles);
    if (!ids.length) profilesBox.append(small("No profiles yet."));
    for (const id of ids) {
      const p = profiles[id];
      const isActive = id === activeId;
      const stepsBox = el("ol", { style: { margin: "0.3rem 0 0 1.2rem", padding: 0, display: "flex", flexDirection: "column", gap: "0.2rem" } });
      (p.steps || []).forEach((s, i) => {
        stepsBox.append(el("li", { style: { display: "flex", alignItems: "center", gap: "0.5rem", ...mono } },
          el("code", {}, s.model), small(`× ${s.retries}`),
          btn("↑", () => { if (i === 0) return; const pr = structuredClone(profiles); const st = pr[id].steps; [st[i - 1], st[i]] = [st[i], st[i - 1]]; save({ fallback: { profiles: pr } }); }, { padding: "0 0.35rem", fontSize: "0.7rem", opacity: i === 0 ? 0.35 : 1 }),
          btn("↓", () => { if (i >= p.steps.length - 1) return; const pr = structuredClone(profiles); const st = pr[id].steps; [st[i + 1], st[i]] = [st[i], st[i + 1]]; save({ fallback: { profiles: pr } }); }, { padding: "0 0.35rem", fontSize: "0.7rem", opacity: i >= p.steps.length - 1 ? 0.35 : 1 }),
          btn("remove", () => { const pr = structuredClone(profiles); pr[id].steps.splice(i, 1); save({ fallback: { profiles: pr } }); }, { padding: "0 0.4rem", fontSize: "0.7rem" })));
      });
      const primary = el("input", { type: "number", min: 0, step: 1, value: p.primary_retries, style: { width: "4.5rem" }, "data-testid": `mrp-primary-${id}` });
      primary.addEventListener("change", () => { const pr = structuredClone(profiles); pr[id].primary_retries = Number(primary.value); save({ fallback: { profiles: pr } }); });
      const exhausted = el("select", { "data-testid": `mrp-exhausted-${id}` }, el("option", { value: "stop" }, "show the error"), el("option", { value: "retry-last" }, "keep retrying the last model forever"));
      exhausted.value = p.exhausted || "stop";
      exhausted.addEventListener("change", () => { const pr = structuredClone(profiles); pr[id].exhausted = exhausted.value; save({ fallback: { profiles: pr } }); });
      profilesBox.append(el("div", { "data-profile": id, style: { border: `1px solid ${isActive ? accent : border}`, borderRadius: "4px", padding: "0.5rem 0.7rem", background: isActive ? `color-mix(in srgb, ${accent} 8%, transparent)` : "transparent" } },
        el("div", { style: { display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" } },
          el("strong", { style: { color: isActive ? accent : "inherit" } }, p.name || id),
          isActive ? small("active") : btn("use this profile", () => save({ fallback: { enabled: true, profile: id } }), { padding: "0.1rem 0.4rem", fontSize: "0.75rem" }),
          btn("rename", async () => { const name = await api.dialog.prompt("Profile name", { value: p.name || "", validate: (v) => (v.trim() ? "" : "required") }); if (name === null) return; const pr = structuredClone(profiles); pr[id].name = name.trim(); save({ fallback: { profiles: pr } }); }, { padding: "0.1rem 0.4rem", fontSize: "0.75rem" }),
          btn("delete", async () => {
            if (!(await api.dialog.confirm(`Delete the profile "${p.name || id}"?`, { title: "Delete profile", danger: true, okLabel: "Delete" }))) return;
            const patch = { fallback: { profiles: { [id]: null } } };
            if (isActive) Object.assign(patch.fallback, { enabled: false, profile: "" });
            save(patch);
          }, { padding: "0.1rem 0.4rem", fontSize: "0.75rem" })),
        el("div", { style: { display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.4rem", fontSize: "0.85rem", flexWrap: "wrap" } }, "primary model: retry", primary, "times, then"),
        p.steps && p.steps.length ? stepsBox : el("div", { style: { marginLeft: "1.2rem", marginTop: "0.3rem" } }, small("no fallback models yet — with none, this profile is just a retry count")),
        el("div", { style: { display: "flex", alignItems: "center", gap: "0.6rem", marginTop: "0.4rem", fontSize: "0.85rem", flexWrap: "wrap" } },
          btn("add a model…", () => addStep(id), { padding: "0.1rem 0.5rem", fontSize: "0.78rem" }), "when the chain is exhausted:", exhausted)));
    }
    profilesBox.append(el("div", {}, btn("New profile…", newProfile)));
  }

  // --- timeline (what will happen) -----------------------------------------------------------------------------------
  const timelineBox = el("div", { "data-testid": "mrp-timeline", style: { ...mono } });
  async function renderTimeline() {
    try {
      const response = await api.routes.fetch("preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ config: cfg(), limit: 14 }) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const { timeline } = await response.json();
      const rows = timeline.map((r) => el("div", { style: { display: "grid", gridTemplateColumns: "2.2rem 1fr 6rem 1fr", gap: "0.6rem", padding: "0.1rem 0" } },
        el("span", { style: { color: muted } }, `#${r.attempt}`), el("code", {}, r.model), el("span", { style: { color: muted, textAlign: "right" } }, r.delay === null || r.delay === undefined ? "" : (r.delay === 0 ? "now" : `wait ${fmtS(r.delay)}`)), el("span", {}, r.note)));
      let total = 0; for (const r of timeline) total += r.delay || 0;
      timelineBox.replaceChildren(...rows, el("div", { style: { color: muted, marginTop: "0.3rem" } }, timeline.length >= 14 ? `… first 14 attempts shown; about ${fmtS(total)} of waiting so far (jitter not included)` : `about ${fmtS(total)} of waiting in total (jitter not included)`));
    } catch (error) {
      timelineBox.textContent = "preview unavailable";
      api.log.warn("preview failed", error);
    }
  }

  // --- live picture -----------------------------------------------------------------------------------------------
  const effectiveLine = el("div", { "data-testid": "mrp-effective", style: { display: "flex", gap: "1rem", flexWrap: "wrap", fontSize: "0.85rem" } });
  const counters = el("div", { "data-testid": "mrp-counters", style: { display: "flex", gap: "1.2rem", flexWrap: "wrap", ...mono } });
  const eventsPre = el("pre", { "data-testid": "mrp-events", style: { whiteSpace: "pre-wrap", margin: 0, ...mono } }, "");
  const hooksLine = el("div", { style: { color: muted, fontSize: "0.75rem" } }, "");

  const chip = (label, value) => el("span", {}, small(label + " "), el("code", {}, value));

  function renderLive() {
    if (!status) return;
    const e = status.effective || {};
    effectiveLine.replaceChildren(chip("in force:", MECHANISMS[e.preset] ? MECHANISMS[e.preset].label : e.preset), chip("same-model retries", e.retries), chip("between retries", e.delay),
      e.chain ? chip("then", e.chain.length ? e.chain.join(" → ") + (e.exhausted === "retry-last" ? " → stay" : " → error") : "error") : chip("then", "agent.fallback_model chain"),
      chip("mid-turn recoveries", e.midturn === undefined ? "host (1)" : e.midturn));
    const c = status.counters || {};
    counters.replaceChildren(chip("retries", c.retries || 0), chip("mid-turn recoveries", c.resumes || 0), chip("model swaps", c.swaps || 0), chip("stayed on a candidate", c.stays || 0), chip("chains exhausted", c.exhausted || 0), chip("last", ago(c.last_ts)),
      ...Object.entries(c.by_surface || {}).map(([k, v]) => chip(k, v)));
    eventsPre.textContent = (status.recent || []).slice(-40).reverse().map((ev) => {
      const when = new Date(ev.ts * 1000).toLocaleTimeString();
      if (ev.kind === "retry") return `${when}  ${ev.surface.padEnd(10)} retry #${ev.attempt}  wait ${fmtS(ev.delay)}`;
      if (ev.kind === "resume") return `${when}  ${ev.surface.padEnd(10)} mid-turn recovery #${ev.attempt}  (${ev.detail})`;
      if (ev.kind === "swap") return `${when}  ${ev.surface.padEnd(10)} switch ${ev.previous || "?"} → ${ev.model}`;
      if (ev.kind === "stay") return `${when}  ${ev.surface.padEnd(10)} stay on ${ev.model}  (${ev.detail})`;
      return `${when}  ${ev.surface.padEnd(10)} exhausted on ${ev.model}  ${ev.detail || ""}`;
    }).join("\n") || "no retries recorded yet — they appear here when a model or API error is retried";
    hooksLine.textContent = `${status.hooks} hook(s) registered · retry budget overridden on ${(status.budget_overrides || []).length} binding(s)${status.host && status.host.version ? ` · KiroCrew ${status.host.version}` : ""}`;
  }

  function renderConfig() {
    const c = cfg();
    const preset = (status && status.effective && status.effective.preset) || "host";
    renderMechanisms(preset);
    help.textContent = (MECHANISMS[preset] || {}).help || (preset === "custom" ? "A custom combination — the parameters below are what is in force." : "");
    enabled.checked = c.enabled !== false;
    renderParams();
    renderProfiles();
  }

  async function refresh() {
    try {
      const response = await api.routes.fetch("status");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      status = await response.json();
    } catch (error) {
      api.log.warn("status unavailable", error);
    }
    renderConfig();
    renderLive();
    await renderTimeline();
  }

  const page = el("div", { "data-testid": "mrp-page", style: { display: "flex", flexDirection: "column", gap: "0.9rem" } },
    el("p", { style: { margin: 0 } }, el("strong", { style: { color: accent } }, "Model Retry Policy"),
      " — decides what KiroCrew does when the model or its API fails with a transient error (throttling, overload, 5xx, a dropped stream): how often to retry, how long to wait, and which model to move to. ",
      el("span", { style: { color: muted } }, "Applies to dashboard chats, cron and Slack turns and sub-agents. Works alongside Agent Backend Router. Unofficial; nothing here is part of KiroCrew.")),
    el("div", { style: { display: "flex", alignItems: "center", gap: "0.8rem", flexWrap: "wrap" } },
      el("label", { style: { display: "flex", alignItems: "center", gap: "0.4rem" } }, enabled, "policy on"), small("(off = KiroCrew's own behaviour, nothing overridden)"), note),
    el("div", {}, el("div", { style: { marginBottom: "0.3rem" } }, "Mechanism"), group, help),
    el("div", {}, el("div", { style: { marginBottom: "0.3rem" } }, "Parameters"), paramsBox, paramsHint),
    el("div", {}, el("div", { style: { marginBottom: "0.3rem" } }, "Fallback profiles ", small("(a profile is the ordered list of models to move to, and how many attempts each gets)")), profilesBox),
    el("div", {}, el("div", { style: { marginBottom: "0.3rem" } }, "What will happen ", small("(the attempt sequence for a turn whose model keeps failing)")), timelineBox),
    el("div", {}, el("div", { style: { marginBottom: "0.3rem", display: "flex", gap: "0.6rem", alignItems: "center", flexWrap: "wrap" } }, "Live",
      btn("refresh", refresh),
      btn("clear counters", async () => { if (await api.dialog.confirm("Clear the recorded retry events and counters?", { title: "Clear counters" })) { await api.routes.fetch("reset", { method: "POST" }); await refresh(); } })),
      effectiveLine, el("div", { style: { marginTop: "0.4rem" } }, counters), hooksLine),
    el("details", { open: "" }, el("summary", { style: { cursor: "pointer" } }, "Recent retry events"), eventsPre),
  );
  container.append(page);
  await loadModels();
  await refresh();
  const timer = setInterval(async () => {
    try {
      const response = await api.routes.fetch("status");
      if (response.ok) { status = await response.json(); renderLive(); }
    } catch (error) { api.log.warn("status poll failed", error); }
  }, 5000);
  return () => {
    clearInterval(timer);
    page.remove();
  };
}
