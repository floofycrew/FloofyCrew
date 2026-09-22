// os-notify-bridge — the settings page the FloofyCrew App mounts under Mods.
//
// Everything the bridge needs a human for lives here, because the Notification
// permission can only be requested from a user gesture:
//   * the permission surface — status plus an "Enable OS notifications" button
//     that actually AWAITS Notification.requestPermission() (the host's own
//     opportunistic prompt fires it un-awaited at delivery time, which is one
//     of the gaps this mod exists to close);
//   * the master switch, the background-only policy and one switch per kind,
//     persisted through floofy.mod(id).config so every browser shares them;
//   * the "Agent turn complete" switch, which surfaces the host's own hidden
//     `mc-notify-chat-complete` flag (per browser, like the permission);
//   * a test banner button.
//
// Changes are pushed to the live bridge over window.floofy.events, so nothing
// needs a reload. No browser pop-up dialogs — the desktop shell has none.
"use strict";

const SETTINGS_EVENT = "mod.os-notify-bridge.settings";
const HOST_TURN_FLAG = "mc-notify-chat-complete";

const DEFAULT_SETTINGS = Object.freeze({
  enabled: true,
  onlyBackground: true,
  kinds: Object.freeze({
    approval: true,
    cron: true,
    hook: true,
    heartbeat: false,
    agent: true,
    taskrunner: true,
    skills: true,
    safety_override: true,
    resources: true,
    turn: true,
  }),
  otherKinds: true,
});

const KIND_ROWS = Object.freeze([
  ["approval", "Tool approval required", "an agent is blocked on your decision"],
  ["turn", "Agent turn complete", "the host's own per-session banner; background only, per browser"],
  ["cron", "Cron jobs", "failures and reports from scheduled jobs"],
  ["hook", "Hooks", "hook runs that push a note"],
  ["agent", "Agent notes", "notes agents push to the notification center"],
  ["taskrunner", "Task runner", "task lifecycle notes"],
  ["skills", "Skill reviews", "a staged skill candidate waits for approval"],
  ["safety_override", "Auto-approve lifecycle", "e.g. an override expired mid-run"],
  ["resources", "Host resource pressure", "disk/memory thresholds crossed"],
  ["heartbeat", "Heartbeat", "periodic heartbeat notes (noisy; off by default)"],
]);

function mergeSettings(stored) {
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

function permissionState() {
  if (typeof Notification === "undefined") return "unsupported";
  return Notification.permission; // granted | denied | default
}

export default async function mount(container, api) {
  const tokens = api.theme.tokens();
  const accent = tokens["--accent"] || "#ff6b8b";
  const muted = tokens["--text-muted"] || "inherit";
  let settings = mergeSettings(await api.config.get());

  async function save(patch) {
    settings = mergeSettings(await api.config.set(patch));
    syncHostTurnFlag();
    try {
      window.floofy.events.emit(SETTINGS_EVENT, { settings });
    } catch {
      /* the bridge re-reads the store on its next activation anyway */
    }
    api.log.info("settings saved");
  }

  function syncHostTurnFlag() {
    try {
      localStorage.setItem(HOST_TURN_FLAG, settings.enabled && settings.kinds.turn ? "1" : "0");
    } catch {
      /* per-browser flag; storage may be unavailable */
    }
  }

  // ── permission surface ──
  const permText = el("span", { "data-testid": "onb-permission-state" }, "");
  const permHint = el("div", { style: { color: muted, fontSize: "0.85rem", margin: "0.2rem 0 0" } }, "");
  const permButton = el(
    "button",
    {
      type: "button",
      "data-testid": "onb-grant",
      style: { borderColor: accent, color: accent, padding: "0.3rem 0.7rem", borderRadius: "6px", background: "transparent", cursor: "pointer" },
      onclick: async () => {
        if (typeof Notification === "undefined") return;
        try {
          await Notification.requestPermission();
        } catch {
          /* some shells reject instead of resolving to "denied" */
        }
        renderPermission();
      },
    },
    "Enable OS notifications",
  );

  function renderPermission() {
    const state = permissionState();
    permText.textContent = `OS notification permission: ${state}`;
    permButton.style.display = state === "default" ? "" : "none";
    permHint.textContent =
      state === "granted"
        ? "Granted for this browser/app. The permission is per browser — repeat this on each machine you watch the dashboard from."
        : state === "denied"
          ? "Denied at the browser/shell level. Reset it in the browser's site settings (or the desktop shell's notification settings), then come back."
          : state === "unsupported"
            ? "This environment has no Notification API; the bridge stays inert here."
            : "Nothing reaches the OS until you grant the permission from this button.";
  }
  renderPermission();

  const testButton = el(
    "button",
    {
      type: "button",
      "data-testid": "onb-test",
      style: { borderColor: accent, color: accent, padding: "0.3rem 0.7rem", borderRadius: "6px", background: "transparent", cursor: "pointer" },
      onclick: () => {
        if (permissionState() !== "granted") {
          renderPermission();
          return;
        }
        try {
          new Notification("os-notify-bridge", { body: "Test banner — the bridge can reach your OS.", tag: "floofy-onb:test" });
        } catch {
          /* never take the page down over a test */
        }
      },
    },
    "Send a test banner",
  );

  // ── switches ──
  function toggle(labelText, checked, testid, onchange, hint) {
    const box = el("input", { type: "checkbox", "data-testid": testid });
    box.checked = checked;
    box.addEventListener("change", () => onchange(box.checked));
    const label = el("label", { style: { display: "flex", alignItems: "baseline", gap: "0.5rem" } }, box, el("span", {}, labelText, hint ? el("span", { style: { color: muted, fontSize: "0.85rem" } }, ` — ${hint}`) : ""));
    return label;
  }

  const kindList = el("div", { style: { display: "flex", flexDirection: "column", gap: "0.35rem", marginLeft: "1.2rem" } });
  for (const [kind, label, hint] of KIND_ROWS) {
    kindList.append(toggle(label, settings.kinds[kind] !== false, `onb-kind-${kind}`, (on) => save({ kinds: { ...settings.kinds, [kind]: on } }), hint));
  }
  kindList.append(toggle("Everything else", settings.otherKinds, "onb-kind-other", (on) => save({ otherKinds: on }), "app channels and kinds this version does not know"));

  const page = el(
    "div",
    { "data-testid": "onb-page", style: { display: "flex", flexDirection: "column", gap: "0.9rem", maxWidth: "44rem" } },
    el(
      "p",
      { style: { margin: 0 } },
      el("strong", { style: { color: accent } }, "OS notification bridge"),
      " — mirrors the dashboard's notification feed to native OS banners. ",
      el("span", { style: { color: muted } }, "Unofficial; nothing here is part of KiroCrew."),
    ),
    el("div", {}, permText, el("span", {}, " "), permButton, permHint),
    toggle("Mirror notifications to the OS", settings.enabled, "onb-enabled", (on) => save({ enabled: on })),
    toggle("Only when the dashboard is in the background", settings.onlyBackground, "onb-background", (on) => save({ onlyBackground: on }), "hidden tab or unfocused window; keeps the in-app toast the only surface while you are looking"),
    el("div", {}, el("div", { style: { marginBottom: "0.35rem" } }, "Banner these kinds:"), kindList),
    el("div", {}, testButton),
    el(
      "p",
      { style: { color: muted, margin: 0, fontSize: "0.8rem" } },
      "The bridge listens to the host's own notification fan-out, so channels muted in KiroCrew's notification settings never reach the OS. The host also ships two narrow native paths of its own (approval while hidden, unread-count growth); the bridge reuses their banner tags so the OS collapses duplicates where the platform allows.",
    ),
  );

  container.append(page);
  syncHostTurnFlag();
  return () => {
    page.remove();
  };
}
