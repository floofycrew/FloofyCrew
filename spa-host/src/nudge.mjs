// FloofyCrew SPA host — the update nudge: a dashboard-wide reminder that a FloofyCrew release or mod
// updates are waiting (Requirement 7.7 carried out of the App page).
//
// The App page shows the release notice and the per-mod update buttons, but a user who never opens
// that page never sees them. This module renders, once per dashboard document, the same kind of
// reminder the host shows for its own updates: a centred card over a dimmed page, a header with an
// icon and a dismiss cross, the body naming the version(s), "Remind me tomorrow" and a primary
// action in the footer, a "Skip these versions" link, Escape and a click outside the card standing
// for "remind me tomorrow". The structure and the utility classes are the host's own update
// modal's (its `UpdateFoundModal`: `fixed inset-0 z-[65] bg-bg/80 …`, `bg-card border border-border
// rounded-xl shadow-xl w-[460px] …`, `bg-accent text-accent-fg` for the primary button), so the card
// takes the host's theme tokens and sits where its reminders sit; the few inline styles are a
// fallback that keeps it a centred card should a class ever go missing.
//
// What it does NOT do: it never installs anything. The primary action opens the FloofyCrew App
// (`/apps/floofycrew`), whose buttons run the update behind the host's confirmation protocol — the
// nudge only tells. The decision record lives in the browser's localStorage per dashboard origin
// (`floofycrew.update-nudge`), like the unofficial-banner preference: `{"<item key>": {snoozed_until,
// skipped}}` with the host's rules — an item is due when it has no record, or its record is neither
// skipped nor snoozed into the future; "Remind me tomorrow" snoozes every shown item for 24 h, "Skip"
// marks them skipped for good (a newer version is a new key, so it shows again).
//
// Framework-free: plain DOM, same-origin fetches only (Requirement 4.6). Everything is injectable so
// the node tests drive it with the fake document of the dialog tests.
"use strict";

/** The Loader's routes (the same prefix `host.mjs` uses; not imported from there, which would make the two modules a cycle). */
const API_BASE = "/api/apps/floofycrew";

export const NUDGE_STORAGE_KEY = "floofycrew.update-nudge";
export const SNOOZE_SECONDS = 86400;
export const APP_PATH = "/apps/floofycrew";
/** The host's update modal is on screen first when both are due; the nudge waits a little and never stacks on another modal. */
export const SHOW_DELAY_MS = 2500;
export const BUSY_RETRY_MS = 20000;
export const BUSY_RETRIES = 6;

const DOWNLOAD_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 15V3"/><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/></svg>';
const CLOSE_ICON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';

// --- what is due ----------------------------------------------------------------------------------------------------

/** The items a Loader state and a `/registry` document announce: the FloofyCrew release, then one per mod update. */
export function updateItems(state, registry) {
  const items = [];
  const selfUpdate = state?.selfUpdate;
  if (selfUpdate && selfUpdate.available && selfUpdate.version) {
    items.push({ kind: "floofycrew", key: `floofycrew@${selfUpdate.version}`, version: String(selfUpdate.version), running: state.loaderVersion ?? selfUpdate.running ?? null, notes: selfUpdate.notes ?? null });
  }
  const updates = registry?.updates && typeof registry.updates === "object" ? registry.updates : {};
  for (const [modId, row] of Object.entries(updates).sort(([a], [b]) => a.localeCompare(b))) {
    if (!row || row.update !== true || !row.candidate) continue;
    items.push({ kind: "mod", key: `mod:${modId}@${row.candidate}`, modId, version: String(row.candidate), installed: row.installed ?? null, verdict: row.verdict ?? null, notes: row.changelog ?? row.repo ?? null, name: state?.mods?.[modId]?.name ?? modId });
  }
  return items;
}

/** The host's rule: due when no record, or the record is neither skipped nor snoozed into the future. */
export function isDue(record, nowSeconds) {
  if (!record || typeof record !== "object") return true;
  if (record.skipped === true) return false;
  return Number(record.snoozed_until ?? 0) <= nowSeconds;
}

export function readRecords(storage) {
  try {
    const raw = storage?.getItem?.(NUDGE_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

/** Write the records, keeping only the keys of items still announced (an installed update frees its row). */
export function writeRecords(storage, records, keepKeys) {
  const kept = {};
  for (const key of keepKeys) if (records[key]) kept[key] = records[key];
  try {
    storage?.setItem?.(NUDGE_STORAGE_KEY, JSON.stringify(kept));
  } catch {
    /* storage unavailable: the nudge simply shows again next time */
  }
  return kept;
}

export function dueItems(items, records, nowSeconds) {
  return items.filter((item) => isDue(records[item.key], nowSeconds));
}

// --- rendering -------------------------------------------------------------------------------------------------------

function el(doc, tag, { className, style, text, html, attrs } = {}) {
  const node = doc.createElement(tag);
  if (className) node.setAttribute("class", className);
  if (style) Object.assign(node.style, style);
  if (text != null) node.textContent = String(text);
  if (html != null) node.innerHTML = html;
  for (const [key, value] of Object.entries(attrs ?? {})) {
    if (value === null || value === undefined || value === false) continue;
    node.setAttribute(key, value === true ? "" : String(value));
  }
  return node;
}

function headline(items) {
  const release = items.find((i) => i.kind === "floofycrew");
  const mods = items.filter((i) => i.kind === "mod");
  if (release && mods.length) return "FloofyCrew and mod updates available";
  if (release) return "FloofyCrew update available";
  return mods.length === 1 ? "Mod update available" : "Mod updates available";
}

/**
 * Render the card for `items` into `doc.body`. `actions`: `snooze()`, `skip()`, `open()`. Returns the overlay node.
 * Markup and classes mirror the host's own update modal so the card inherits its theme tokens.
 */
export function renderNudge(doc, items, actions) {
  const release = items.find((i) => i.kind === "floofycrew");
  const mods = items.filter((i) => i.kind === "mod");
  const overlay = el(doc, "div", {
    className: "fixed inset-0 z-[65] bg-bg/80 backdrop-blur-xs flex items-center justify-center animate-rise p-safe",
    style: { position: "fixed", inset: "0", zIndex: "65", display: "flex", alignItems: "center", justifyContent: "center" },
    attrs: { role: "presentation", "data-testid": "floofycrew-update-nudge" },
  });
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) actions.snooze();
  });
  const card = el(doc, "div", {
    className: "bg-card border border-border rounded-xl shadow-xl w-[460px] max-w-[90vw] flex flex-col overflow-hidden outline-hidden",
    style: { background: "var(--bg-card, var(--bg, #1a1a1f))", color: "var(--text, #eee)", border: "1px solid var(--border, rgba(127,127,127,.35))", borderRadius: "0.75rem", width: "460px", maxWidth: "90vw", display: "flex", flexDirection: "column", overflow: "hidden" },
    attrs: { role: "dialog", "aria-modal": "true", "aria-label": headline(items), tabindex: "-1", "data-testid": "floofycrew-update-nudge-card" },
  });
  card.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") event.stopPropagation?.();
  });

  const header = el(doc, "div", { className: "flex items-center justify-between px-4 py-2.5 border-b border-border bg-bg-elevated", style: { display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0.625rem 1rem" } });
  const title = el(doc, "div", { className: "flex items-center gap-2", style: { display: "flex", alignItems: "center", gap: "0.5rem" } });
  title.append(el(doc, "span", { className: "lucide-inline text-accent", html: DOWNLOAD_ICON, attrs: { "aria-hidden": "true" } }));
  title.append(el(doc, "span", { className: "text-sm font-semibold text-text", text: headline(items), attrs: { "data-testid": "floofycrew-update-nudge-title" } }));
  const dismiss = el(doc, "button", { className: "text-muted hover:text-text cursor-pointer bg-transparent border-none disabled:opacity-50", html: CLOSE_ICON, attrs: { type: "button", "aria-label": "Dismiss", title: "Remind me tomorrow", "data-testid": "floofycrew-update-nudge-dismiss" } });
  dismiss.addEventListener("click", () => actions.snooze());
  header.append(title, dismiss);

  const body = el(doc, "div", { className: "px-4 py-3 text-sm text-text", style: { padding: "0.75rem 1rem" } });
  if (release) {
    const line = el(doc, "p", { attrs: { "data-testid": "floofycrew-update-nudge-release" } });
    line.append(doc.createTextNode("FloofyCrew "));
    line.append(el(doc, "span", { className: "font-semibold", text: release.version }));
    line.append(doc.createTextNode(` is available for this host${release.running ? ` (you run ${release.running})` : ""}.`));
    if (release.notes) {
      line.append(doc.createTextNode(" "));
      line.append(el(doc, "a", { className: "underline text-muted hover:text-text", text: "Release notes", attrs: { href: release.notes, target: "_blank", rel: "noopener noreferrer" } }));
    }
    body.append(line);
  }
  if (mods.length) {
    const intro = el(doc, "p", { className: release ? "mt-2" : "", text: mods.length === 1 ? "One installed mod has a newer version:" : `${mods.length} installed mods have newer versions:`, attrs: { "data-testid": "floofycrew-update-nudge-mods-intro" } });
    body.append(intro);
    const box = el(doc, "div", { className: "mt-2 p-3 bg-bg rounded-lg border border-border max-h-56 overflow-y-auto text-[13px] text-muted", style: { marginTop: "0.5rem", padding: "0.75rem", maxHeight: "14rem", overflowY: "auto" }, attrs: { "data-testid": "floofycrew-update-nudge-mods" } });
    for (const mod of mods) {
      const row = el(doc, "div", { className: "flex items-center justify-between gap-2", style: { display: "flex", justifyContent: "space-between", gap: "0.5rem" }, attrs: { "data-testid": `floofycrew-update-nudge-mod-${mod.modId}` } });
      const name = el(doc, "span");
      name.append(el(doc, "span", { className: "font-semibold text-text", text: mod.name }));
      name.append(doc.createTextNode(` ${mod.installed ?? "?"} → ${mod.version}${mod.verdict ? ` (${mod.verdict})` : ""}`));
      row.append(name);
      if (mod.notes) row.append(el(doc, "a", { className: "underline shrink-0", text: "notes", attrs: { href: mod.notes, target: "_blank", rel: "noopener noreferrer" } }));
      box.append(row);
    }
    body.append(box);
  }
  const links = el(doc, "p", { className: "mt-2 text-[12px] flex items-center gap-3", style: { marginTop: "0.5rem", fontSize: "12px", display: "flex", alignItems: "center", gap: "0.75rem" } });
  const anytime = el(doc, "button", { className: "underline text-muted hover:text-text cursor-pointer bg-transparent border-none p-0 text-[12px] text-left", text: "You can update anytime from the FloofyCrew app", attrs: { type: "button", "data-testid": "floofycrew-update-nudge-anytime" } });
  anytime.addEventListener("click", () => {
    actions.snooze();
    actions.open();
  });
  const skip = el(doc, "button", { className: "underline text-muted hover:text-text bg-transparent border-none p-0 text-[12px] cursor-pointer shrink-0", text: items.length === 1 ? "Skip this version" : "Skip these versions", attrs: { type: "button", "data-testid": "floofycrew-update-nudge-skip" } });
  skip.addEventListener("click", () => actions.skip());
  links.append(anytime, skip);
  body.append(links);

  const footer = el(doc, "div", { className: "flex flex-wrap items-center justify-end gap-2 px-4 py-2.5 border-t border-border bg-bg-elevated", style: { display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "flex-end", gap: "0.5rem", padding: "0.625rem 1rem" } });
  const later = el(doc, "button", { className: "px-3 py-1.5 text-sm rounded-md border border-border text-text hover:border-border-strong bg-transparent cursor-pointer disabled:opacity-50", text: "Remind me tomorrow", attrs: { type: "button", "data-testid": "floofycrew-update-nudge-later" } });
  later.addEventListener("click", () => actions.snooze());
  const open = el(doc, "button", { className: "px-3 py-1.5 text-sm rounded-md bg-accent text-accent-fg hover:opacity-90 cursor-pointer disabled:opacity-50", text: release ? `Update to ${release.version} in FloofyCrew` : "Open FloofyCrew", attrs: { type: "button", "data-testid": "floofycrew-update-nudge-open" } });
  open.addEventListener("click", () => actions.open());
  footer.append(later, open);

  card.append(header, body, footer);
  overlay.append(card);
  doc.body.append(overlay);
  card.focus?.();
  return overlay;
}

// --- the controller ---------------------------------------------------------------------------------------------------

function onAppPage(win) {
  const path = win?.location?.pathname ?? "";
  return path === APP_PATH || path.startsWith(`${APP_PATH}/`);
}

/** Another modal is up (the host's own update reminder, a confirmation): do not stack on it. */
function anotherModalOpen(doc) {
  try {
    const dialogs = doc.querySelectorAll?.('[role="dialog"][aria-modal="true"]') ?? [];
    return dialogs.length > 0;
  } catch {
    return false;
  }
}

/**
 * Build the nudge controller. `env`: `document`, `window`, `fetch` (same-origin), `state()` (the latest Loader
 * state), `storage` (localStorage), `now()` (seconds), `bus` (the host's; `route.changed` re-arms the nudge when
 * the user leaves the App page), `setTimeout`, `log`, `delayMs`.
 */
export function createUpdateNudge(env = {}) {
  const win = env.window ?? (typeof window !== "undefined" ? window : null);
  const doc = env.document ?? (typeof document !== "undefined" ? document : null);
  const log = env.console ?? console;
  const fetchImpl = env.fetch ?? ((...args) => win.fetch(...args));
  const storage = env.storage ?? (() => {
    try {
      return win?.localStorage ?? null;
    } catch {
      return null;
    }
  })();
  const now = env.now ?? (() => Date.now() / 1000);
  const setTimer = env.setTimeout ?? ((fn, ms) => setTimeout(fn, ms));
  const delayMs = env.delayMs ?? SHOW_DELAY_MS;
  const navigate = env.navigate ?? ((path) => {
    try {
      win.history.pushState({}, "", path);
      win.dispatchEvent(new win.PopStateEvent("popstate"));
    } catch {
      win.location.assign(path);
    }
  });

  let overlay = null;
  let shownKeys = [];
  let allKeys = [];
  let busyRetries = 0;
  let armed = false;

  async function registryDocument() {
    try {
      const response = await fetchImpl(`${API_BASE}/registry`, { credentials: "same-origin", headers: { Accept: "application/json" } });
      if (!response.ok) return null;
      return await response.json();
    } catch (error) {
      log.debug?.("[floofy] update nudge: /registry unavailable", error);
      return null;
    }
  }

  function close() {
    overlay?.remove?.();
    overlay = null;
    doc?.removeEventListener?.("keydown", onKey);
  }

  function record(fields) {
    const records = readRecords(storage);
    for (const key of shownKeys) records[key] = { ...fields };
    writeRecords(storage, records, allKeys);
  }

  const actions = {
    snooze() {
      record({ snoozed_until: Math.floor(now()) + SNOOZE_SECONDS, skipped: false });
      close();
    },
    skip() {
      record({ snoozed_until: 0, skipped: true });
      close();
    },
    open() {
      close();
      navigate(`${APP_PATH}#/mods`);
    },
  };

  function onKey(event) {
    if (event.key === "Escape" && overlay) actions.snooze();
  }

  /** Decide and, when something is due, show. Returns the items shown (empty when nothing was). */
  async function show() {
    if (!doc || overlay) return [];
    if (onAppPage(win)) return [];  // the App page carries the notice and the buttons itself
    const state = (await env.state?.()) ?? null;
    if (!state) return [];
    const registry = await registryDocument();
    const items = updateItems(state, registry);
    allKeys = items.map((i) => i.key);
    const records = readRecords(storage);
    const due = dueItems(items, records, now());
    if (!due.length) {
      writeRecords(storage, records, allKeys);
      return [];
    }
    if (anotherModalOpen(doc)) {
      if (busyRetries < BUSY_RETRIES) {
        busyRetries += 1;
        setTimer(() => void show(), BUSY_RETRY_MS);
      }
      return [];
    }
    shownKeys = due.map((i) => i.key);
    overlay = renderNudge(doc, due, actions);
    doc.addEventListener?.("keydown", onKey);
    return due;
  }

  /** Arm once per document: show after the delay; if the user is on the App page, show when they leave it. */
  function start() {
    if (armed) return;
    armed = true;
    let fired = false;
    const fire = () => {
      if (fired) return;
      fired = true;
      void show().catch((error) => log.warn("[floofy] update nudge failed", error));
    };
    setTimer(() => {
      if (onAppPage(win)) {
        env.bus?.on?.("route.changed", () => {
          if (!onAppPage(win)) fire();
        }, "floofy-host");
        return;
      }
      fire();
    }, delayMs);
  }

  return Object.freeze({ start, show, close, actions, isOpen: () => overlay !== null });
}
