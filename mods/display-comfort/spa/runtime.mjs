// display-comfort — the runtime part: keep the stored settings applied to the dashboard.
//
// Three levers, all fail-open and all undone on disable:
//
//   * chat text scale — one custom property on <html> (`--floofy-dc-chat-scale`);
//     the stylesheet (baked at boot, and held here as `<style id="floofy-display-comfort">`
//     so the mod works before the next patch apply too) turns it into `zoom` on every
//     `[data-testid="message-bubble"]`, which scales text, code blocks and spacing
//     proportionally — explicit rem/px utility classes would ignore an inherited font-size;
//   * composer opt-in — `data-floofy-dc-composer="on"` on <html> extends the same scale
//     to `[data-testid="chat-footer"]`;
//   * page zoom — `zoom` on the document element, any percentage, not just the
//     browser's fixed stops.
//
// Settings come from localStorage first (instant, what the boot hook used), then the
// mod's config through `floofy.mod(id).config` (authoritative, survives a cleared
// cache and follows this login across browsers). The settings page saves through the
// same config route and broadcasts `floofy-display-comfort-changed`, which this part
// answers by re-applying. Same-origin only; nothing here reaches another origin.
"use strict";

const STYLE_ID = "floofy-display-comfort";
const CACHE_KEY = "floofy-display-comfort";
const CHANGED_EVENT = "floofy-display-comfort-changed";

export const DEFAULTS = Object.freeze({ chatScale: 100, composerToo: false, pageZoom: 100 });
const LIMITS = Object.freeze({ chatScale: [50, 250], pageZoom: [25, 300] });

const RULES = [
  '[data-testid="message-bubble"] { zoom: var(--floofy-dc-chat-scale, 1); }',
  'html[data-floofy-dc-composer="on"] [data-testid="chat-footer"] { zoom: var(--floofy-dc-chat-scale, 1); }',
].join("\n");

function clamp(value, [low, high], fallback) {
  const n = Math.round(Number(value));
  return Number.isFinite(n) ? Math.min(high, Math.max(low, n)) : fallback;
}

/** Any stored/received shape → a complete, in-range settings object. */
export function normalize(raw) {
  const source = raw && typeof raw === "object" ? raw : {};
  return {
    chatScale: clamp(source.chatScale, LIMITS.chatScale, DEFAULTS.chatScale),
    composerToo: source.composerToo === true,
    pageZoom: clamp(source.pageZoom, LIMITS.pageZoom, DEFAULTS.pageZoom),
  };
}

/** Write the three levers onto <html>; a default value clears its lever entirely. */
export function applySettings(settings, doc = document) {
  const el = doc.documentElement;
  if (settings.chatScale !== DEFAULTS.chatScale) el.style.setProperty("--floofy-dc-chat-scale", String(settings.chatScale / 100));
  else el.style.removeProperty("--floofy-dc-chat-scale");
  if (settings.composerToo) el.setAttribute("data-floofy-dc-composer", "on");
  else el.removeAttribute("data-floofy-dc-composer");
  el.style.zoom = settings.pageZoom !== DEFAULTS.pageZoom ? String(settings.pageZoom / 100) : "";
}

function readCache() {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    return raw ? normalize(JSON.parse(raw)) : null;
  } catch {
    return null;
  }
}

function writeCache(settings) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(settings));
  } catch {}
}

export default function activate(ctx) {
  const doc = document;
  let disposed = false;

  let style = doc.getElementById(STYLE_ID);
  if (!style) {
    style = doc.createElement("style");
    style.id = STYLE_ID;
    style.setAttribute("data-floofy-mod", ctx.modId);
    style.textContent = RULES;
    doc.head.appendChild(style);
  }

  const cached = readCache();
  if (cached) applySettings(cached, doc);

  // the authoritative copy: the mod's own config through the Loader (may lag the
  // cache by one save at most, and wins — a cleared cache or another client catches up)
  const modApi = typeof window !== "undefined" && window.floofy ? window.floofy.mod(ctx.modId) : null;
  if (modApi) {
    modApi.config
      .get()
      .then((stored) => {
        if (disposed || !stored || Object.keys(stored).length === 0) return;
        const settings = normalize(stored);
        applySettings(settings, doc);
        writeCache(settings);
        ctx.log.info("applied stored settings", settings);
      })
      .catch((error) => ctx.log.warn("could not read the mod config; the cached settings stay", error));
  }

  const onChanged = (event) => {
    const settings = normalize(event && event.detail);
    applySettings(settings, doc);
    writeCache(settings);
  };
  window.addEventListener(CHANGED_EVENT, onChanged);

  return () => {
    disposed = true;
    window.removeEventListener(CHANGED_EVENT, onChanged);
    applySettings(DEFAULTS, doc); // clears the property, the attribute and the zoom
    const node = doc.getElementById(STYLE_ID);
    if (node && node.parentNode) node.parentNode.removeChild(node);
  };
}

export function deactivate() {}
