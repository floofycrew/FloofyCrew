// os-notify-bridge — mirror KiroCrew's in-app notifications to native OS banners.
//
// Source of truth: the host's own `mc-notification` fan-out event — a window
// CustomEvent with detail `{kind}` the dashboard dispatches for every
// non-silenced, non-passive notification-bus note and for the synthesized
// `approval` kind. Because the bridge sits BEHIND that event, the host's own
// channel mute/priority settings keep their meaning: a muted channel never
// reaches the OS through this mod.
//
// The event carries only the kind, so the bridge enriches the banner from
// GET /api/notifications (same-origin, behind the dashboard auth): newest
// fresh note of that kind supplies the title, body and deep-link URL.
//
// The host ships two narrow native paths of its own; the bridge cooperates
// instead of duplicating them:
//   * approval banners while the tab is hidden (tag `kirocrew-approval`) —
//     the bridge reuses the host's tags (`approval_id`/`job_id`/`task_id`,
//     `kirocrew-approval`) so the OS collapses both into one banner;
//   * per-session "turn done" banners behind the host's own
//     `mc-notify-chat-complete` flag — the settings page surfaces that flag
//     as the "Agent turn complete" switch and the bridge never fires `turn`
//     itself, so the host's richer per-session banner is the only one.
//
// No framework, no third-party origin, no credentials. Unofficial; not part
// of KiroCrew.
"use strict";

const EVENT_NAME = "mc-notification";
const HOST_TURN_FLAG = "mc-notify-chat-complete";
const SETTINGS_EVENT = "mod.os-notify-bridge.settings";
const FEED_URL = "/api/notifications";
const FRESH_WINDOW_S = 90; // a note older than this is not "the one that just fired"
const PER_TAG_MIN_MS = 3000;
const GLOBAL_MAX_PER_MIN = 10;

// Known system kinds (the last segment of the host's system.* channels).
// `subagent` is passive by default and never reaches mc-notification, so it
// has no switch; an escalated one falls under `otherKinds` like app notes.
export const KNOWN_KINDS = Object.freeze([
  "approval",
  "cron",
  "hook",
  "heartbeat",
  "agent",
  "taskrunner",
  "skills",
  "safety_override",
  "resources",
]);

export const DEFAULT_SETTINGS = Object.freeze({
  enabled: true,
  onlyBackground: true,
  kinds: Object.freeze({
    approval: true,
    cron: true,
    hook: true,
    heartbeat: false, // noisy by nature; opt in
    agent: true,
    taskrunner: true,
    skills: true,
    safety_override: true,
    resources: true,
    turn: true, // delegated to the host's own per-session banner (see below)
  }),
  otherKinds: true, // app channels and kinds this version does not know
});

const KIND_TITLES = Object.freeze({
  approval: "Approval required",
  cron: "Cron job",
  hook: "Hook",
  heartbeat: "Heartbeat",
  agent: "Agent",
  taskrunner: "Task runner",
  skills: "Skill review",
  safety_override: "Auto-approve",
  resources: "Resource pressure",
});

const KIND_BODIES = Object.freeze({
  approval: "A tool call is waiting for your decision.",
  cron: "A scheduled job reported.",
  skills: "A staged skill is waiting for review.",
});

/** Deep-merge stored settings over the defaults (kinds key by key). */
export function mergeSettings(stored) {
  const source = stored && typeof stored === "object" ? stored : {};
  const kinds = { ...DEFAULT_SETTINGS.kinds };
  if (source.kinds && typeof source.kinds === "object") {
    for (const [key, value] of Object.entries(source.kinds)) kinds[key] = Boolean(value);
  }
  return {
    enabled: source.enabled === undefined ? DEFAULT_SETTINGS.enabled : Boolean(source.enabled),
    onlyBackground: source.onlyBackground === undefined ? DEFAULT_SETTINGS.onlyBackground : Boolean(source.onlyBackground),
    kinds,
    otherKinds: source.otherKinds === undefined ? DEFAULT_SETTINGS.otherKinds : Boolean(source.otherKinds),
  };
}

/** Strip markdown-ish noise for an OS banner and cap the length. */
export function cleanText(text, cap = 220) {
  if (typeof text !== "string") return "";
  const flat = text
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1") // [label](url) -> label
    .replace(/[*_`#>]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return flat.length > cap ? `${flat.slice(0, cap - 1)}…` : flat;
}

/** Pick the newest fresh note of `kind` from the feed document. */
export function pickNote(feed, kind, nowS = Date.now() / 1000) {
  const notes = feed && Array.isArray(feed.notifications) ? feed.notifications : [];
  for (let i = notes.length - 1; i >= 0; i -= 1) {
    const note = notes[i];
    if (!note || note.kind !== kind) continue;
    const ts = Number.parseFloat(note.ts);
    if (Number.isFinite(ts) && nowS - ts > FRESH_WINDOW_S) return null; // feed is append-ordered; older than fresh = give up
    return note;
  }
  return null;
}

/** The Notification tag: reuse the host's own tags so banners collapse. */
export function tagFor(kind, note) {
  const hostTag = note && (note.approval_id || note.job_id || note.task_id);
  if (hostTag) return String(hostTag);
  if (kind === "approval") return "kirocrew-approval";
  return `floofy-onb:${kind}`;
}

export function activate(ctx) {
  const win = typeof window === "undefined" ? null : window;
  if (!win) return () => {};
  const log = ctx.log || console;

  let settings = mergeSettings(null);
  const perTag = new Map(); // tag -> last shown ms
  const recent = []; // shown timestamps ms, for the global cap

  function syncHostTurnFlag() {
    // The "turn" switch IS the host's own hidden flag: when on, the host
    // renders its richer per-session banner and the bridge stays out of it.
    try {
      win.localStorage.setItem(HOST_TURN_FLAG, settings.enabled && settings.kinds.turn ? "1" : "0");
    } catch {
      /* storage may be unavailable; the bridge still works without the flag */
    }
  }

  function applySettings(stored) {
    settings = mergeSettings(stored);
    syncHostTurnFlag();
  }

  async function loadSettings() {
    try {
      const api = win.floofy && typeof win.floofy.mod === "function" ? win.floofy.mod(ctx.modId) : null;
      if (api) applySettings(await api.config.get());
    } catch (error) {
      log.warn && log.warn("settings load failed; using defaults", error);
      applySettings(null);
    }
  }

  function permitted() {
    return typeof win.Notification !== "undefined" && win.Notification.permission === "granted";
  }

  function inBackground() {
    return win.document.hidden || !win.document.hasFocus();
  }

  function rateLimited(tag, now) {
    while (recent.length && now - recent[0] > 60000) recent.shift();
    if (recent.length >= GLOBAL_MAX_PER_MIN) return true;
    const last = perTag.get(tag);
    return last !== undefined && now - last < PER_TAG_MIN_MS;
  }

  function show(kind, note) {
    const now = Date.now();
    const tag = tagFor(kind, note);
    if (rateLimited(tag, now)) return;
    const title = cleanText(note && note.title, 80) || KIND_TITLES[kind] || `KiroCrew: ${kind}`;
    const body = cleanText(note && note.body) || KIND_BODIES[kind] || "Open the dashboard for details.";
    let banner;
    try {
      banner = new win.Notification(title, { body, tag });
    } catch {
      return; // some shells construct-throw; never fault the mod over a banner
    }
    perTag.set(tag, now);
    recent.push(now);
    const url = note && typeof note.url === "string" && note.url.startsWith("/") ? note.url : null;
    banner.onclick = () => {
      try {
        win.focus();
        if (url) win.location.assign(url);
        banner.close();
      } catch {
        /* focus/navigation is best-effort */
      }
    };
  }

  async function enrich(kind) {
    try {
      const response = await win.fetch(FEED_URL, { headers: { Accept: "application/json" } });
      if (!response.ok) return null;
      return pickNote(await response.json(), kind);
    } catch {
      return null;
    }
  }

  async function onNotification(event) {
    const kind = event && event.detail ? event.detail.kind : null;
    if (typeof kind !== "string" || !kind) return;
    if (kind === "turn") return; // delegated to the host's own per-session banner
    if (!settings.enabled || !permitted()) return;
    const known = kind === "approval" || KNOWN_KINDS.includes(kind);
    if (known ? settings.kinds[kind] === false : !settings.otherKinds) return;
    if (settings.onlyBackground && !inBackground()) return;
    show(kind, await enrich(kind));
  }

  const listener = (event) => {
    onNotification(event).catch((error) => log.warn && log.warn("banner failed", error));
  };
  win.addEventListener(EVENT_NAME, listener);
  const offSettings = ctx.events && typeof ctx.events.on === "function"
    ? ctx.events.on(SETTINGS_EVENT, (_name, payload) => applySettings(payload && payload.settings))
    : null;
  loadSettings();

  return () => {
    win.removeEventListener(EVENT_NAME, listener);
    if (offSettings) offSettings();
  };
}

export function deactivate() {
  /* activate's cleanup handles everything */
}
