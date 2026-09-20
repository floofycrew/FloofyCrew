// FloofyCrew manager App — the Mods page, the landing page (Requirement 16.1, 2.7, 7.3, 8.11, 16.5; task 6.7 → 11.2).
//
// Every installed mod with its state and typed reason, the seam of every part
// and whether it modifies payload files, the host-compat and source-tier badges,
// warnings and governance warnings (never gates), update availability; the
// actions Enable/Disable, Update, Yeet, Uninstall through the typed routes; the
// staged set with "Apply now" (POST /reload, Requirement 7.6); the quarantine
// with per-version restore; and the install form — a registry id, a path, an
// archive URL or a git reference, staged by default, "apply now" on request.
// The disclosure and the confirmations the CLI would ask arrive as 409
// questions the App's modals present (task 11.3).
import React from "react";

import { commandNote, styles } from "../palette.mjs";

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

function stateBadge(row) {
  if (row.active) return React.createElement("span", { style: { ...styles.badge, ...styles.ok } }, "active");
  const reason = row.reason || (row.enabled === false ? "UserDisabled" : "inactive");
  return React.createElement("span", { style: { ...styles.badge, ...(reason === "Error" || reason === "Quarantined" ? styles.danger : styles.warn) }, title: row.detail || "" }, reason);
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
  if (!items.length) return React.createElement("span", { style: styles.muted }, "—");
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
  const [enable, setEnable] = React.useState(false);
  const submit = async (event) => {
    event.preventDefault();
    const text = source.trim();
    if (!text) return;
    const result = await manager.act("/mods/install", { source: text, now, enable });
    if (result && result.ok) setSource("");
  };
  return React.createElement(
    "form",
    { onSubmit: submit, style: { display: "flex", gap: "0.6rem", flexWrap: "wrap", alignItems: "center" }, "data-testid": "floofycrew-install-form" },
    React.createElement("input", { style: { ...styles.input, minWidth: "26rem" }, value: source, onChange: (e) => setSource(e.target.value), placeholder: "registry id[@version], ssh://…@tag, https://…@tag, https://…/mod.zip or a local path", "data-testid": "floofycrew-install-source", "aria-label": "mod source" }),
    React.createElement("label", { style: styles.muted }, React.createElement("input", { type: "checkbox", checked: now, onChange: (e) => setNow(e.target.checked), "data-testid": "floofycrew-install-now" }), " apply now (default: staged for the next gateway start)"),
    React.createElement("label", { style: styles.muted }, React.createElement("input", { type: "checkbox", checked: enable, onChange: (e) => setEnable(e.target.checked) }), " enable code parts right away"),
    React.createElement("button", { type: "submit", style: { ...styles.button, ...styles.primary }, disabled: manager.busy || !source.trim(), "data-testid": "floofycrew-install-submit" }, "Install"),
  );
}

export function ModsPage({ manager, navigate, openConsent }) {
  const { state, rows, statusMeta, registry, busy, act, applyNow } = manager;
  const host = (state && state.host) || {};
  const consent = (state && state.consent) || {};
  const pending = (statusMeta && statusMeta.pending) || [];
  const quarantine = (statusMeta && statusMeta.quarantine) || {};
  const updates = (registry && registry.updates) || {};
  const mod = (id) => `/mods/${encodeURIComponent(id)}`;

  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      "div",
      { style: styles.card },
      React.createElement("div", null, React.createElement("strong", null, "Loader: "), state ? `${state.loader} (FloofyCrew ${state.loaderVersion || "?"}, API ${state.api_version || "?"}, booted ${state.bootedAt || "?"})` : "loading…", state && state.vanilla ? " — vanilla boot (every mod disabled once)" : ""),
      React.createElement("div", null, React.createElement("strong", null, "Host: "), `${host.edition || "?"} ${host.version || "?"} (${host.channel || "channel unknown"})`),
      React.createElement(
        "div",
        { style: consent.required ? styles.danger : undefined, "data-testid": "floofycrew-consent" },
        React.createElement("strong", null, "Consent: "),
        consent.required ? `required — ${consent.message || "the one-time warning has not been acknowledged"}; the first action asks for it ` : `acknowledged by ${consent.by || "?"} on ${consent.acknowledgedAt || "?"} (${consent.how || "?"})`,
        consent.required && openConsent ? React.createElement("button", { style: { ...styles.button, ...styles.primary }, disabled: busy, "data-testid": "floofycrew-consent-open", onClick: () => openConsent({ reaccept: false }) }, "Read the warning and agree") : null,
      ),
      pending.length ? React.createElement("div", { style: styles.warn, "data-testid": "floofycrew-pending" }, `Staged (restart to apply): ${pending.join(", ")} `, React.createElement("button", { style: styles.button, onClick: applyNow, disabled: busy, "data-testid": "floofycrew-apply-now" }, "Apply now")) : null,
      state && state.governance && state.governance.length
        ? React.createElement(
            "ul",
            { style: { ...styles.warn, ...styles.list, marginTop: "0.4rem" } },
            state.governance.map((warning, index) => React.createElement("li", { key: index }, `governance ${warning.code}: ${warning.message}`)),
          )
        : null,
    ),
    React.createElement("div", { style: styles.card, "data-testid": "floofycrew-install-card" }, React.createElement("h2", { style: styles.cardTitle }, "Install a mod"), React.createElement(InstallForm, { manager }), commandNote(React, "The same at a terminal:", "floofy install <id | path | archive | https URL | git reference> [--now]")),
    React.createElement(
      "div",
      { style: styles.card },
      React.createElement("div", { style: { ...styles.row, ...styles.head } }, React.createElement("span", null, "Mod"), React.createElement("span", null, "State"), React.createElement("span", null, "Host compat · source tier"), React.createElement("span", null, "Parts (seam)"), React.createElement("span", null, "Actions")),
      rows.length === 0 ? React.createElement("div", { style: { ...styles.muted, padding: "0.6rem 0" } }, "no mods installed — use the form above, or the Registry page") : null,
      rows.map((row) => {
        const update = updates[row.id];
        return React.createElement(
          "div",
          { key: row.id, style: styles.row, "data-testid": `floofycrew-mod-row-${row.id}` },
          React.createElement(
            "div",
            null,
            React.createElement("div", null, React.createElement("strong", null, row.name || row.id), " ", React.createElement("code", null, `${row.id} ${row.version}`)),
            React.createElement("div", { style: styles.muted, "data-testid": `floofycrew-enabled-${row.id}` }, row.enabled ? "enabled" : "disabled", row.link ? " · dev link" : ""),
            update && update.update ? React.createElement("div", { style: styles.ok, "data-testid": `floofycrew-update-${row.id}` }, `update available: ${update.candidate}${update.verdict ? ` (${update.verdict} here)` : ""}`) : null,
          ),
          React.createElement("div", null, stateBadge(row), row.detail ? React.createElement("div", { style: styles.muted, title: row.detail }, row.detail.length > 90 ? `${row.detail.slice(0, 90)}…` : row.detail) : null),
          React.createElement("div", null, compatBadge(row), tierBadge(row), React.createElement("div", null, React.createElement(Warnings, { row }))),
          React.createElement(Parts, { parts: row.parts }),
          React.createElement(
            "div",
            null,
            React.createElement("button", { style: styles.button, disabled: busy, "data-testid": `floofycrew-toggle-${row.id}`, onClick: () => act(`${mod(row.id)}/${row.enabled ? "disable" : "enable"}`) }, row.enabled ? "Disable" : "Enable"),
            update && update.update ? React.createElement("button", { style: styles.button, disabled: busy, "data-testid": `floofycrew-update-button-${row.id}`, onClick: () => act(`${mod(row.id)}/update`, { now: true }) }, `Update to ${update.candidate}`) : null,
            React.createElement("button", { style: styles.button, disabled: busy, onClick: () => (window.confirm(`Park ${row.id} in the quarantine for host ${host.version}?`) ? act(`${mod(row.id)}/yeet`, { reason: "manual yeet (app)" }) : null) }, "Yeet"),
            React.createElement("button", { style: { ...styles.button, ...styles.danger }, disabled: busy, "data-testid": `floofycrew-uninstall-${row.id}`, onClick: () => act(`${mod(row.id)}/uninstall`, { now: true }) }, "Uninstall"),
            navigate ? React.createElement("button", { style: styles.button, disabled: busy, onClick: () => navigate("registry", row.id) }, "Registry entry") : null,
          ),
        );
      }),
    ),
    Object.keys(quarantine).filter((key) => key !== "requests").length
      ? React.createElement(
          "div",
          { style: styles.card, "data-testid": "floofycrew-quarantine" },
          React.createElement("strong", null, "Quarantine (yeeted per host version)"),
          Object.entries(quarantine)
            .filter(([key]) => key !== "requests")
            .map(([version, mods]) => React.createElement("div", { key: version }, React.createElement("code", null, version), `: ${(mods || []).join(", ") || "-"} `, React.createElement("button", { style: styles.button, disabled: busy, onClick: () => act("/restore", { version }) }, "Restore set"))),
        )
      : null,
  );
}
