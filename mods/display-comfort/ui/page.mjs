// display-comfort — the settings page inside the FloofyCrew App.
//
// Two sliders and a toggle, persisted through `api.config.set` (the mod's
// .floofy/config.json) and applied live as you drag:
//
//   * chat text scale (50–250 %) — every message bubble, and with the toggle the
//     composer too, scaled via one custom property on <html> that the mod's
//     stylesheet turns into `zoom` on the bubbles;
//   * page zoom (25–300 %, 1 % steps) — `zoom` on the document element, any value,
//     not just the browser's fixed stops.
//
// The page applies each candidate itself (so the preview works even right after
// enabling, before the runtime part has loaded) and broadcasts
// `floofy-display-comfort-changed` so the runtime part stays the long-term owner
// and caches the value for the first-frame boot hook. No framework, no browser
// pop-ups, no network beyond the Loader's same-origin routes.
"use strict";

const CACHE_KEY = "floofy-display-comfort";
const CHANGED_EVENT = "floofy-display-comfort-changed";
const DEFAULTS = Object.freeze({ chatScale: 100, composerToo: false, pageZoom: 100 });
const LIMITS = Object.freeze({ chatScale: [50, 250], pageZoom: [25, 300] });

function clamp(value, [low, high], fallback) {
  const n = Math.round(Number(value));
  return Number.isFinite(n) ? Math.min(high, Math.max(low, n)) : fallback;
}

function normalize(raw) {
  const source = raw && typeof raw === "object" ? raw : {};
  return {
    chatScale: clamp(source.chatScale, LIMITS.chatScale, DEFAULTS.chatScale),
    composerToo: source.composerToo === true,
    pageZoom: clamp(source.pageZoom, LIMITS.pageZoom, DEFAULTS.pageZoom),
  };
}

function applySettings(settings) {
  const el = document.documentElement;
  if (settings.chatScale !== DEFAULTS.chatScale) el.style.setProperty("--floofy-dc-chat-scale", String(settings.chatScale / 100));
  else el.style.removeProperty("--floofy-dc-chat-scale");
  if (settings.composerToo) el.setAttribute("data-floofy-dc-composer", "on");
  else el.removeAttribute("data-floofy-dc-composer");
  el.style.zoom = settings.pageZoom !== DEFAULTS.pageZoom ? String(settings.pageZoom / 100) : "";
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(settings));
  } catch {}
  window.dispatchEvent(new CustomEvent(CHANGED_EVENT, { detail: settings }));
}

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
  let settings = normalize(await api.config.get());

  const status = el("span", { "data-testid": "display-comfort-status", style: { color: muted } }, "loaded");
  let saveTimer = null;

  function preview(patch) {
    settings = normalize({ ...settings, ...patch });
    applySettings(settings);
    render();
  }

  function save(patch) {
    preview(patch);
    status.textContent = "saving…";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(async () => {
      try {
        await api.config.set({ ...settings });
        status.textContent = `saved: chat ${settings.chatScale} %, zoom ${settings.pageZoom} %`;
        api.log.info("settings saved", settings);
      } catch (error) {
        status.textContent = "saving failed; the preview still applies until reload";
        api.log.warn("config.set failed", error);
      }
    }, 250);
  }

  function slider(testid, [low, high], step, get, set) {
    const range = el("input", { type: "range", min: low, max: high, step, "data-testid": testid, style: { flex: "1", minWidth: "12rem", accentColor: accent } });
    const number = el("input", { type: "number", min: low, max: high, step: 1, "data-testid": `${testid}-value`, style: { width: "5rem" } });
    range.addEventListener("input", () => set(Number(range.value), false));
    range.addEventListener("change", () => set(Number(range.value), true));
    number.addEventListener("change", () => set(Number(number.value), true));
    return { range, number, sync: () => { range.value = String(get()); number.value = String(get()); } };
  }

  const chat = slider("display-comfort-chat-scale", LIMITS.chatScale, 5, () => settings.chatScale, (value, commit) => (commit ? save : preview)({ chatScale: value }));
  const zoom = slider("display-comfort-page-zoom", LIMITS.pageZoom, 1, () => settings.pageZoom, (value, commit) => (commit ? save : preview)({ pageZoom: value }));
  const composer = el("input", { type: "checkbox", "data-testid": "display-comfort-composer" });
  composer.addEventListener("change", () => save({ composerToo: composer.checked }));

  function render() {
    chat.sync();
    zoom.sync();
    composer.checked = settings.composerToo;
  }
  render();

  const row = (label, control, valueBox, suffix) => el("label", { style: { display: "flex", alignItems: "center", gap: "0.6rem" } }, el("span", { style: { minWidth: "9rem" } }, label), control, valueBox, el("span", { style: { color: muted } }, suffix));

  const page = el(
    "div",
    { "data-testid": "display-comfort-page", style: { display: "flex", flexDirection: "column", gap: "0.9rem", maxWidth: "44rem" } },
    el("p", { style: { margin: 0 } }, el("strong", { style: { color: accent } }, "Display comfort"), " — chat text size and page zoom, persistent and applied before the first paint. ", el("span", { style: { color: muted } }, "Unofficial; nothing here is part of KiroCrew.")),
    row("Chat text scale", chat.range, chat.number, "% of the default (message bubbles)"),
    el("label", { style: { display: "flex", alignItems: "center", gap: "0.5rem", marginLeft: "9.6rem" } }, composer, "scale the composer too"),
    row("Page zoom", zoom.range, zoom.number, "% — any value, not just the browser's stops"),
    el(
      "div",
      { style: { display: "flex", alignItems: "center", gap: "0.8rem" } },
      el(
        "button",
        {
          type: "button",
          "data-testid": "display-comfort-reset",
          style: { borderColor: accent, color: accent, padding: "0.3rem 0.7rem", borderRadius: "6px", background: "transparent", cursor: "pointer" },
          onclick: () => save({ ...DEFAULTS }),
        },
        "Reset to 100 %",
      ),
      status,
    ),
    el("p", { style: { color: muted, margin: 0, fontSize: "0.8rem" } }, "Chat scaling uses zoom on each message bubble (text, code and spacing scale together); the values live in this mod's config and in a localStorage cache the first-frame hook reads, so a reload never flashes the default size."),
  );
  container.append(page);
  return () => {
    clearTimeout(saveTimer);
    page.remove();
    // the levers stay as saved — the runtime part owns them while the mod is enabled
  };
}
