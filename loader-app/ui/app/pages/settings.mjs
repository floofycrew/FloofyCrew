// FloofyCrew manager App — the Settings page (Requirement 16.1, 16.2, 11.1, 11.8; task 11.2).
//
// The consent record and the way to re-acknowledge the warning (the same
// modal, `POST /consent {agree: true, reaccept: true}`), a one-time vanilla
// boot, and — never hidden — the capabilities the App does not have because
// it runs inside the gateway it manages, each with the terminal command that
// does it. About: versions and the FloofyCrew mark as the terminal draws it.
import React from "react";

import { RestartButton } from "../actions.mjs";
import { apiFetch } from "../api.mjs";
import { PALETTE, UNOFFICIAL, styles } from "../palette.mjs";

/** What the App cannot do from inside the gateway, with the command that can (Requirement 16.2). */
export const TERMINAL_ONLY = Object.freeze([
  ["Install or repair the Loader app, the re-apply trigger and the early shim", "floofy init [--trigger user-timer|path-wrapper] [--early]"],
  ["Re-acknowledge the warning without the App", "floofy init --reaccept"],
  ["Remove FloofyCrew's triggers, shim and Loader app; restore every payload", "floofy deinit [--purge]"],
  ["Revert-then-patch every payload now (the Loader does it at every start)", "floofy apply"],
  ["Return every payload to vanilla", "floofy restore --all"],
  ["Verify served bytes against the deployment manifests; run the SPA reporter", "floofy verify [--spa]"],
  ["Restart the gateway from a shell (the red button does the same from here, through the host's own command)", "kirocrew restart"],
  ["Pause the host's own auto-update (explicit, logged)", "floofy hold [--duration 7d] [--release]"],
  ["Link a mod checkout for development and stream its log", "floofy dev <path> [--follow]"],
  ["Scaffold a new mod", "floofy new <kind> --id <id>"],
  ["Validate a mod directory or archive before installing it", "floofy validate <path>"],
  ["Resolve a file's sha256 to a mod", "floofy which <sha256>"],
]);

function Banner() {
  const [art, setArt] = React.useState(null);
  React.useEffect(() => {
    apiFetch("/banner").then((reply) => setArt(reply.ok ? reply.body : null)).catch(() => setArt(null));
  }, []);
  if (!art || !Array.isArray(art.rows)) return null;
  return React.createElement(
    "pre",
    { style: { ...styles.mono, lineHeight: 1.05, maxHeight: "none", overflow: "visible" }, "aria-label": "FloofyCrew banner", "data-testid": "floofycrew-banner-art" },
    art.rows.map((row, r) => React.createElement("div", { key: r }, row.map((cell, c) => React.createElement("span", { key: c, style: { color: `rgb(${cell[1][0]},${cell[1][1]},${cell[1][2]})` } }, cell[0])))),
  );
}

export function SettingsPage({ manager, openConsent, banner }) {
  const { state, busy, act } = manager;
  const consent = (state && state.consent) || {};
  const host = (state && state.host) || {};
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      "div",
      { style: styles.card, "data-testid": "floofycrew-settings-consent" },
      React.createElement("h2", { style: styles.cardTitle }, "Consent"),
      React.createElement("div", null, consent.required ? React.createElement("span", { style: styles.danger }, `required — ${consent.message || "the one-time warning has not been acknowledged"}`) : `acknowledged by ${consent.by || "?"} on ${consent.acknowledgedAt || "?"} through ${consent.how || "?"} (warning v${consent.warningVersion ?? "?"}, current v${consent.currentWarningVersion ?? "?"})`),
      React.createElement("div", { style: { marginTop: "0.4rem" } }, openConsent ? React.createElement("button", { style: { ...styles.button, ...styles.primary }, disabled: busy, "data-testid": "floofycrew-consent-open", onClick: () => openConsent({ reaccept: !consent.required }) }, consent.required ? "Read the warning and agree" : "Read the warning again and re-acknowledge") : null, React.createElement("span", { style: styles.muted }, " The acknowledgement is recorded in consent.json with how: app; at a terminal: ", React.createElement("code", { style: styles.command }, "floofy init [--reaccept]"))),
    ),
    React.createElement(
      "div",
      { style: styles.card, "data-testid": "floofycrew-settings-vanilla" },
      React.createElement("h2", { style: styles.cardTitle }, "Vanilla boot"),
      React.createElement("div", { style: styles.cardIntro }, "Ask for exactly one gateway start with every mod disabled; the marker is consumed by the next start — request it, then restart."),
      React.createElement("div", { style: { display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.3rem 0.5rem" } }, React.createElement("button", { style: { ...styles.button, marginBottom: 0 }, disabled: busy, "data-testid": "floofycrew-vanilla", onClick: () => act("/vanilla", {}) }, "Request a vanilla boot"), React.createElement("button", { style: { ...styles.button, marginBottom: 0 }, disabled: busy, onClick: () => act("/vanilla", { cancel: true }) }, "Cancel the request"), React.createElement(RestartButton, { manager, testid: "floofycrew-restart-settings" }), React.createElement("span", { style: styles.muted }, " Terminal: ", React.createElement("code", { style: styles.command }, "floofy vanilla [--cancel]"), " then ", React.createElement("code", { style: styles.command }, "kirocrew restart"))),
    ),
    React.createElement(
      "div",
      { style: styles.card, "data-testid": "floofycrew-settings-restart" },
      React.createElement("h2", { style: styles.cardTitle }, "Restart KiroCrew"),
      React.createElement("div", { style: styles.cardIntro }, "Staged installs and removals, a Loader app update and a vanilla request all take effect at the next gateway start. The button asks first, then runs the host's own `kirocrew restart` (service-aware); the dashboard goes away for a few seconds and this page reloads when the new gateway answers."),
      React.createElement(RestartButton, { manager, testid: "floofycrew-restart-card" }),
    ),
    React.createElement(
      "div",
      { style: styles.card, "data-testid": "floofycrew-settings-terminal" },
      React.createElement("h2", { style: styles.cardTitle }, "Only at a terminal"),
      React.createElement("div", { style: styles.muted }, "The App runs inside the gateway it manages, so these stay with the floofy CLI — nothing is hidden, the command is:"),
      React.createElement("div", { style: { ...styles.kv, marginTop: "0.4rem" } }, TERMINAL_ONLY.flatMap(([what, command]) => [React.createElement("span", { key: `${command}-w` }, what), React.createElement("code", { key: `${command}-c`, style: styles.command }, command)])),
    ),
    React.createElement(
      "div",
      { style: styles.card, "data-testid": "floofycrew-settings-about" },
      React.createElement("h2", { style: styles.cardTitle }, "About"),
      React.createElement("div", { style: styles.kv }, React.createElement("span", { style: styles.muted }, "FloofyCrew"), React.createElement("span", null, state ? `${state.loaderVersion || "?"} (API ${state.api_version || "?"})` : "…"), React.createElement("span", { style: styles.muted }, "host"), React.createElement("span", null, `${host.edition || "?"} ${host.version || "?"} ${host.channel || ""}`), React.createElement("span", { style: styles.muted }, "data home"), React.createElement("span", null, (state && state.dataHome) || "…")),
      React.createElement("div", { style: { ...styles.muted, marginTop: "0.4rem", color: PALETTE.accent } }, UNOFFICIAL),
      banner
        ? React.createElement(
            "div",
            { style: { marginTop: "0.5rem", display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" }, "data-testid": "floofycrew-banner-preference" },
            React.createElement("span", { style: styles.muted }, banner.hidden ? "The unofficial banner at the top is hidden on this browser (the footer keeps the statement)." : "The unofficial banner shows at the top of every page on this browser."),
            React.createElement("button", { type: "button", style: { ...styles.button, marginBottom: 0 }, "data-testid": "floofycrew-banner-toggle", onClick: banner.hidden ? banner.show : banner.hide }, banner.hidden ? "Show the banner again" : "Hide the banner"),
          )
        : null,
      React.createElement(Banner, null),
    ),
  );
}
