// FloofyCrew manager App — the Mods page, the landing page (Requirement 16.1, 2.7, 7.3, 8.11, 16.5; task 6.7 → 11.2).
//
// The installed mods as a table — an on/off switch (the enable/disable routes;
// an enabled-but-inactive mod shows its typed reason beside the name), name,
// id · version, the host-compat and source-tier badges with warnings and
// governance warnings (never gates), the actions Update / Registry entry /
// Uninstall through the typed routes, and the parts (seam) collapsed to a count
// with the "modifies payload files" warning kept on the summary line; the
// staged set with "Apply now" (POST /reload, Requirement 7.6); the quarantine
// with per-version restore; and the install form — a registry id, a path, an
// archive URL or a git reference, staged by default, "apply now" on request.
// The disclosure and the confirmations the CLI would ask arrive as 409
// questions the App's modals present (task 11.3).
import React from "react";

import { RestartButton, UpdateAndRestartButton } from "../actions.mjs";
import { BusyButton, Spinner } from "../busy.mjs";
import { Switch } from "../modicon.mjs";
import { commandNote, styles } from "../palette.mjs";
import { ModPage, ModPagesList } from "./modpage.mjs";

export function compatBadge(row) {
  const text = row.hostCompat || "unknown";
  const style = text.startsWith("in-range") ? styles.ok : text.includes("strict") ? styles.danger : styles.warn;
  return React.createElement("span", { style: { ...styles.badge, ...style }, "data-testid": `floofycrew-compat-${row.id}` }, text);
}

/** The source tier (Requirement 8.11): unlisted (a git reference, a path or an archive), listed (a registry record), tested (listed + a `tested` matrix cell for this host). */
export function tierBadge(row, { testid = "floofycrew-tier" } = {}) {
  const tier = row.tier || "unlisted";
  const style = tier === "tested" ? styles.ok : tier === "listed" ? {} : styles.warn;
  const title = tier === "tested" ? "listed in a registry and graded tested on this host version by the Forge" : tier === "listed" ? "installed from a registry record (curated; index verified or allowed unsigned)" : "unlisted source: no curator review, no compatibility data — installed from a git reference, a path or an archive on your consent";
  return React.createElement("span", { style: { ...styles.badge, ...style }, title, "data-testid": `${testid}-${row.id}` }, `tier: ${tier}`);
}

function Parts({ parts }) {
  return React.createElement(
    "ul",
    { style: styles.list },
    (parts || []).map((part) =>
      React.createElement(
        "li",
        { key: part.index },
        React.createElement("code", null, `${part.kind}/${part.side}`),
        " ",
        React.createElement("span", { style: styles.muted }, `→ ${part.seam || "?"}`),
        part.modifiesPayload ? React.createElement("span", { style: { ...styles.badge, ...styles.warn, marginLeft: "0.3rem" } }, "modifies payload files") : null,
        part.seamStatus ? React.createElement("span", { style: { ...styles.badge, marginLeft: "0.3rem" } }, `on disk: ${part.seamStatus}`) : null,
        part.status && part.status !== "unknown" ? React.createElement("span", { style: { ...styles.badge, marginLeft: "0.3rem" } }, part.status) : null,
      ),
    ),
  );
}

function Warnings({ row }) {
  const items = [...(row.warnings || []).map((w) => ({ kind: "warning", ...w })), ...(row.governance || []).map((w) => ({ kind: "governance", ...w }))];
  if (!items.length) return null;
  return React.createElement(
    "ul",
    { style: styles.list },
    items.map((item, index) => React.createElement("li", { key: index, style: item.kind === "governance" ? styles.warn : undefined, title: item.message }, `${item.kind === "governance" ? "governance " : ""}${item.code}`)),
  );
}

/** The install form: a registry id[@version], a path, an https archive URL or a git reference; staged unless "apply now". */
export function InstallForm({ manager }) {
  const [source, setSource] = React.useState("");
  const [now, setNow] = React.useState(false);
  const [disabled, setDisabled] = React.useState(false);
  const submit = async (event) => {
    event.preventDefault();
    const text = source.trim();
    if (!text) return;
    const result = await manager.act("/mods/install", { source: text, now, disabled });
    if (result && result.ok) setSource("");
  };
  return React.createElement(
    "form",
    { onSubmit: submit, style: { display: "flex", gap: "0.6rem", flexWrap: "wrap", alignItems: "center" }, "data-testid": "floofycrew-install-form" },
    React.createElement("input", { style: { ...styles.input, minWidth: "26rem" }, value: source, onChange: (e) => setSource(e.target.value), placeholder: "registry id[@version], a git repository URL (ssh://… or https://…, optional @tag), https://…/mod.zip or a local path", "data-testid": "floofycrew-install-source", "aria-label": "mod source" }),
    React.createElement("label", { style: styles.muted }, React.createElement("input", { type: "checkbox", checked: now, onChange: (e) => setNow(e.target.checked), "data-testid": "floofycrew-install-now" }), " apply now (default: staged for the next gateway start)"),
    React.createElement("label", { style: styles.muted }, React.createElement("input", { type: "checkbox", checked: disabled, onChange: (e) => setDisabled(e.target.checked), "data-testid": "floofycrew-install-disabled" }), " install switched off (enable it later)"),
    React.createElement(BusyButton, { type: "submit", style: { ...styles.button, ...styles.primary }, disabled: manager.busy || !source.trim(), running: manager.running("/mods/install"), label: "Install", busyLabel: "Installing…", testid: "floofycrew-install-submit" }),
  );
}

/** The "Parts (seam)" cell: collapsed by default to a count and the kinds; the "modifies payload files" warning stays on the summary line, never hidden. */
function PartsCell({ row }) {
  const parts = row.parts || [];
  return React.createElement(
    "details",
    { "data-testid": `floofycrew-parts-${row.id}`, style: { fontSize: "0.85rem", minWidth: 0 } },
    React.createElement(
      "summary",
      { style: { cursor: "pointer" } },
      `${parts.length} part(s)`,
      parts.length ? React.createElement("span", { style: { ...styles.muted, marginLeft: "0.3rem" } }, [...new Set(parts.map((p) => p.kind))].join(", ")) : null,
      parts.some((p) => p.modifiesPayload) ? React.createElement("span", { style: { ...styles.badge, ...styles.warn, marginLeft: "0.3rem" } }, "modifies payload files") : null,
    ),
    React.createElement(Parts, { parts }),
  );
}

/** One row of the Installed mods table: switch | name | id | compat · tier | actions | parts. */
function ModRow({ row, update, busy, act, navigate, mod, running }) {
  const inactiveReason = row.enabled && !row.active ? row.reason || "inactive" : null; // enabled but not running: the switch alone would hide it
  const switching = running(`${mod(row.id)}/enable`) || running(`${mod(row.id)}/disable`);
  return React.createElement(
    "div",
    { style: styles.modRow, "data-testid": `floofycrew-mod-row-${row.id}` },
    React.createElement(
      "div",
      { style: { display: "flex", alignItems: "center", gap: "0.35rem" } },
      React.createElement(Switch, { checked: Boolean(row.enabled), disabled: busy, label: `${row.enabled ? "Disable" : "Enable"} ${row.name || row.id}`, testid: `floofycrew-toggle-${row.id}`, onChange: (on) => act(`${mod(row.id)}/${on ? "enable" : "disable"}`) }),
      switching ? React.createElement(Spinner, { size: 14, label: row.enabled ? "disabling" : "enabling" }) : null,
      // the state in words for screen readers and tests; the switch's position says the same thing visually
      React.createElement("span", { style: styles.srOnly, "data-testid": `floofycrew-enabled-${row.id}` }, row.enabled ? "enabled" : "disabled", row.link ? " · dev link" : ""),
    ),
    React.createElement(
      "div",
      { style: { minWidth: 0 }, title: row.description || row.name || row.id },
      React.createElement(
        "div",
        { style: { overflowWrap: "anywhere" } },
        React.createElement("strong", null, row.name || row.id),
        update && update.update ? React.createElement("span", { style: { ...styles.badge, ...styles.ok, marginLeft: "0.4rem" }, "data-testid": `floofycrew-update-${row.id}`, title: `update available: ${update.candidate}${update.verdict ? ` (${update.verdict} here)` : ""}` }, `↑ ${update.candidate}`) : null,
        row.link ? React.createElement("span", { style: { ...styles.badge, marginLeft: "0.4rem" }, title: "a development link: the mod directory is a symlink to your checkout" }, "dev link") : null,
      ),
      inactiveReason ? React.createElement("div", { style: { marginTop: "0.15rem" } }, React.createElement("span", { style: { ...styles.badge, ...(inactiveReason === "Error" || inactiveReason === "Quarantined" ? styles.danger : styles.warn) }, title: row.detail || "" }, inactiveReason)) : null,
    ),
    React.createElement("div", { style: { minWidth: 0, overflowWrap: "anywhere" } }, React.createElement("code", { style: styles.modId }, row.id), React.createElement("span", { style: { ...styles.muted, fontSize: "0.78rem", marginLeft: "0.4rem", whiteSpace: "nowrap" } }, row.version)),
    React.createElement("div", { style: { minWidth: 0, display: "flex", flexWrap: "wrap", gap: "0.2rem 0" } }, compatBadge(row), tierBadge(row), (row.warnings || []).length || (row.governance || []).length ? React.createElement("div", { style: { flexBasis: "100%", fontSize: "0.8rem" } }, React.createElement(Warnings, { row })) : null),
    React.createElement(
      "div",
      { style: { display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0 0.1rem" } },
      update && update.update ? React.createElement(BusyButton, { style: { ...styles.button, ...styles.primary }, disabled: busy, running: running(`${mod(row.id)}/update`, { now: undefined }), title: `${update.candidate} available${update.verdict ? ` (${update.verdict} here)` : ""}`, testid: `floofycrew-update-button-${row.id}`, label: `Update to ${update.candidate}`, busyLabel: "Updating…", onClick: () => act(`${mod(row.id)}/update`, {}) }) : null,
      update && update.update ? React.createElement(BusyButton, { style: styles.button, disabled: busy, running: running(`${mod(row.id)}/update`, { now: true }), testid: `floofycrew-update-now-${row.id}`, label: "Update now", busyLabel: "Updating…", onClick: () => act(`${mod(row.id)}/update`, { now: true }) }) : null,
      update && update.update && update.changelog ? React.createElement("a", { href: update.changelog, target: "_blank", rel: "noopener noreferrer", style: { ...styles.accent, fontSize: "0.8rem", marginRight: "0.35rem" }, "data-testid": `floofycrew-changelog-${row.id}` }, "release notes") : null,
      navigate ? React.createElement("button", { style: styles.button, disabled: busy, onClick: () => navigate("registry", row.id) }, "Registry entry") : null,
      React.createElement(BusyButton, { style: { ...styles.button, ...styles.danger }, disabled: busy, running: running(`${mod(row.id)}/uninstall`), testid: `floofycrew-uninstall-${row.id}`, label: "Uninstall", busyLabel: "Uninstalling…", onClick: () => act(`${mod(row.id)}/uninstall`, { now: true }) }),
    ),
    React.createElement(PartsCell, { row }),
  );
}

export function ModsPage({ manager, navigate, openConsent, rest }) {
  const { state, rows, statusMeta, registry, busy, act, applyNow, running } = manager;
  if (rest) return React.createElement(ModPage, { manager, modId: rest, navigate });
  const host = (state && state.host) || {};
  const selfUpdate = (state && state.selfUpdate) || null; // Requirement 7.7: the CLI's cached daily check, re-graded by the Loader
  const consent = (state && state.consent) || {};
  const pending = (statusMeta && statusMeta.pending) || [];
  const quarantine = (statusMeta && statusMeta.quarantine) || {};
  const updates = (registry && registry.updates) || {};
  const modUpdates = Object.entries(updates).filter(([, u]) => u && u.update);
  const mod = (id) => `/mods/${encodeURIComponent(id)}`;

  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      "div",
      { style: styles.card },
      React.createElement("div", null, React.createElement("strong", null, "Loader: "), state ? `${state.loader} (FloofyCrew ${state.loaderVersion || "?"}, API ${state.api_version || "?"}, booted ${state.bootedAt || "?"})` : "loading…", state && state.vanilla ? " — vanilla boot (every mod disabled once)" : ""),
      React.createElement("div", null, React.createElement("strong", null, "Host: "), `${host.edition || "?"} ${host.version || "?"} (${host.channel || "channel unknown"})`),
      selfUpdate && selfUpdate.available
        ? React.createElement(
            "div",
            { style: { ...styles.warn, display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.4rem 0.6rem", marginTop: "0.4rem" }, "data-testid": "floofycrew-self-update-notice" },
            React.createElement("span", null, React.createElement("strong", null, "FloofyCrew: "), `${selfUpdate.version} is available and supports this host (you run ${state.loaderVersion || "?"})`, selfUpdate.notes ? React.createElement(React.Fragment, null, " · ", React.createElement("a", { href: selfUpdate.notes, target: "_blank", rel: "noopener noreferrer", style: styles.accent }, "release notes")) : null),
            React.createElement(UpdateAndRestartButton, { manager, version: selfUpdate.version }),
            React.createElement("span", { style: styles.muted }, "or at a terminal: ", React.createElement("code", { style: styles.command }, "floofy self-update")),
          )
        : null,
      selfUpdate && selfUpdate.staged && !selfUpdate.staged.applied && selfUpdate.staged.present
        ? React.createElement("div", { style: { ...styles.warn, display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.4rem 0.6rem", marginTop: "0.4rem" }, "data-testid": "floofycrew-self-update-staged" }, React.createElement("span", null, `Loader app update to FloofyCrew ${selfUpdate.staged.version} staged — installed by floofy apply the next time no gateway runs, or now with `, React.createElement("code", { style: styles.command }, "floofy self-update --now")), React.createElement("button", { style: { ...styles.button, ...styles.primary, marginBottom: 0 }, disabled: busy || manager.restarting, "data-testid": "floofycrew-self-update-install-staged", onClick: () => manager.selfUpdateAndRestart() }, "Install the staged update & restart"))
        : null,
      React.createElement(
        "div",
        { style: consent.required ? styles.danger : undefined, "data-testid": "floofycrew-consent" },
        React.createElement("strong", null, "Consent: "),
        consent.required ? `required — ${consent.message || "the one-time warning has not been acknowledged"}; the first action asks for it ` : `acknowledged by ${consent.by || "?"} on ${consent.acknowledgedAt || "?"} (${consent.how || "?"})`,
        consent.required && openConsent ? React.createElement("button", { style: { ...styles.button, ...styles.primary }, disabled: busy, "data-testid": "floofycrew-consent-open", onClick: () => openConsent({ reaccept: false }) }, "Read the warning and agree") : null,
      ),
      pending.length
        ? React.createElement(
            "div",
            { style: { ...styles.warn, display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.4rem 0.6rem", marginTop: "0.4rem" }, "data-testid": "floofycrew-pending" },
            React.createElement("span", null, `Staged (applied at the next gateway start): ${pending.join(", ")}`),
            React.createElement(BusyButton, { style: { ...styles.button, marginBottom: 0 }, onClick: applyNow, disabled: busy, running: running("/reload"), label: "Apply now (reload the Loader)", busyLabel: "Reloading the Loader…", testid: "floofycrew-apply-now" }),
            React.createElement(RestartButton, { manager, testid: "floofycrew-restart-pending" }),
          )
        : null,
      state && state.governance && state.governance.length
        ? React.createElement(
            "ul",
            { style: { ...styles.warn, ...styles.list, marginTop: "0.4rem" } },
            state.governance.map((warning, index) => React.createElement("li", { key: index }, `governance ${warning.code}: ${warning.message}`)),
          )
        : null,
      // Requirement 7.7: the landing page reminds about MOD updates too (the data is kept fresh unattended
      // by the re-apply trigger's daily check + registry refresh; the Registry page carries the actions)
      modUpdates.length
        ? React.createElement(
            "div",
            { style: { ...styles.ok, display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.4rem 0.6rem", marginTop: "0.4rem" }, "data-testid": "floofycrew-mod-updates-notice" },
            React.createElement("span", null, `${modUpdates.length} mod update(s) available: ${modUpdates.map(([id, u]) => `${id} ${u.installed} → ${u.candidate}`).join(", ")}`),
            navigate ? React.createElement("button", { style: { ...styles.button, ...styles.primary, marginBottom: 0 }, disabled: busy, "data-testid": "floofycrew-mod-updates-open", onClick: () => navigate("registry") }, "Review on the Registry page") : null,
          )
        : null,
    ),
    React.createElement(ModPagesList, { manager, navigate }),
    React.createElement("div", { style: styles.card, "data-testid": "floofycrew-install-card" }, React.createElement("h2", { style: styles.cardTitle }, "Install a mod"), React.createElement(InstallForm, { manager }), commandNote(React, "The same at a terminal:", "floofy install <id | path | archive | https URL | git reference> [--now]")),
    React.createElement(
      "div",
      { style: styles.card },
      React.createElement("h2", { style: styles.cardTitle }, `Installed mods (${rows.length})`),
      rows.length === 0 ? React.createElement("div", { style: { ...styles.muted, padding: "0.6rem 0" } }, "no mods installed — use the form above, or the Registry page") : null,
      rows.length
        ? React.createElement(
            "div",
            { style: { ...styles.modRow, ...styles.head, paddingBottom: "0.25rem" }, "data-testid": "floofycrew-mod-rows-head" },
            React.createElement("span", { title: "on: the mod is enabled and loads at the next boot; off: it stays installed but inactive" }, "On"),
            React.createElement("span", null, "Mod"),
            React.createElement("span", null, "Id · version"),
            React.createElement("span", null, "Host compat · source tier"),
            React.createElement("span", null, "Actions"),
            React.createElement("span", null, "Parts (seam)"),
          )
        : null,
      rows.map((row) => React.createElement(ModRow, { key: row.id, row, update: updates[row.id], busy, act, navigate, mod, running })),
    ),
    Object.keys(quarantine).filter((key) => key !== "requests").length
      ? React.createElement(
          "div",
          { style: styles.card, "data-testid": "floofycrew-quarantine" },
          React.createElement("strong", null, "Quarantine (parked automatically when a host update broke compatibility)"),
          Object.entries(quarantine)
            .filter(([key]) => key !== "requests")
            .map(([version, mods]) => React.createElement("div", { key: version }, React.createElement("code", null, version), `: ${(mods || []).join(", ") || "-"} `, React.createElement(BusyButton, { style: styles.button, disabled: busy, running: running("/restore", { version }), label: "Restore set", busyLabel: "Restoring…", onClick: () => act("/restore", { version }) }))),
          commandNote(React, "Parking a mod by hand is a terminal capability:", "floofy yeet <id>"),
        )
      : null,
  );
}
