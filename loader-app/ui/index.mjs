// FloofyCrew Loader — the manager page (`ui.entry`; Requirement 2.7, 7.3, 11.8, 16.2).
//
// The host's AppHost renders the default export of this module as a React
// component when the user opens /apps/floofycrew (design "Spike outcomes" 1.3:
// ui.entry loads only on page open). `react` resolves through the host's inline
// import map (index.html: react -> /vendor/react.mjs); no bundler, no npm.
//
// Everything the page does goes through the Loader's own routes, same-origin,
// behind the dashboard auth: GET /state (the Loader's verdicts), GET /registry
// (update availability), POST /reload ("apply now"), POST /cli {argv} for the
// READ-ONLY commands (status, …) and one typed route per action —
// POST /mods/<id>/enable|disable|uninstall|update|yeet, /restore, … — each of
// which runs the same `floofy` handler as the CLI (actor: app) and writes the
// same audit row. A confirmation the CLI would ask comes back as a 409 question
// (`confirmation.kind`: yes-no, consent, governance-target, unlisted-source,
// unsigned-index) and is answered by re-posting with `confirmations`; nothing
// happens until then. The page never contacts a remote origin.
//
// Importing `./host.mjs` here makes the SPA host available on the manager page
// even when no `index.html` loader tag is patched in (Requirement 4.2).
import React from "react";

import { ensureStarted } from "./host.mjs";

export const API = "/api/apps/floofycrew";
export const UNOFFICIAL = "FloofyCrew is unofficial and not affiliated with Kiro or KiroCrew.";

const styles = {
  page: { padding: "1.25rem", display: "flex", flexDirection: "column", gap: "1rem", fontSize: "0.9rem", color: "var(--text, inherit)" },
  banner: { border: "1px solid var(--warn, #d97706)", background: "var(--warn-subtle, rgba(217,119,6,.12))", padding: "0.75rem 1rem", borderRadius: "8px", fontWeight: 600 },
  card: { border: "1px solid var(--border, rgba(127,127,127,.35))", borderRadius: "8px", padding: "0.75rem 1rem", background: "var(--card, transparent)" },
  row: { borderTop: "1px solid var(--border, rgba(127,127,127,.25))", padding: "0.6rem 0", display: "grid", gridTemplateColumns: "minmax(12rem, 1.4fr) minmax(8rem, 1fr) minmax(8rem, 1fr) minmax(14rem, 2fr) minmax(12rem, 1.2fr)", gap: "0.75rem", alignItems: "start" },
  head: { fontWeight: 600, opacity: 0.8, borderTop: "none" },
  badge: { display: "inline-block", padding: "0.1rem 0.45rem", borderRadius: "999px", border: "1px solid var(--border-strong, rgba(127,127,127,.5))", fontSize: "0.75rem", marginRight: "0.3rem", whiteSpace: "nowrap" },
  button: { padding: "0.25rem 0.6rem", borderRadius: "6px", border: "1px solid var(--border-strong, rgba(127,127,127,.6))", background: "var(--bg-elevated, transparent)", color: "inherit", cursor: "pointer", marginRight: "0.35rem", marginBottom: "0.3rem", fontSize: "0.8rem" },
  muted: { opacity: 0.75 },
  mono: { fontFamily: "var(--mono, ui-monospace, monospace)", fontSize: "0.78rem", whiteSpace: "pre-wrap", maxHeight: "14rem", overflow: "auto", margin: 0 },
  warn: { color: "var(--warn, #d97706)" },
  danger: { color: "var(--danger, #dc2626)" },
  ok: { color: "var(--ok, #16a34a)" },
};

export async function apiFetch(path, options = {}) {
  const response = await fetch(`${API}${path}`, { credentials: "same-origin", ...options });
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  return { status: response.status, ok: response.ok, body };
}

/** Run one READ-ONLY floofy command in-process (POST /cli). */
export async function runCli(argv) {
  const reply = await apiFetch("/cli", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ argv }) });
  const body = reply.body || {};
  return { ...body, httpStatus: reply.status, ok: reply.ok && body.ok !== false };
}

/**
 * Call one typed action route (POST unless `method` says otherwise) with the 409 confirmation protocol:
 * an ordinary yes/no question is put to the user and re-posted with the exact prompt in `confirmations.yes`;
 * every other question kind (consent, governance-target, unlisted-source, unsigned-index) is returned to
 * the caller as `{needs: confirmation}` — the modals of task 11.3 answer them.
 */
export async function callRoute(path, body = {}, { method = "POST", ask = (text) => window.confirm(text) } = {}) {
  const confirmations = { ...(body.confirmations || {}) };
  for (let round = 0; round < 6; round += 1) {
    const reply = await apiFetch(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...body, confirmations }) });
    const document = reply.body || {};
    if (reply.status !== 409 || !document.confirmation) return { ...document, httpStatus: reply.status, ok: reply.ok && document.ok !== false };
    const question = document.confirmation;
    if (question.kind === "yes-no") {
      if (!ask(question.text)) return { ...document, httpStatus: reply.status, ok: false, declined: true, error: `declined: ${question.text}` };
      confirmations.yes = [...(Array.isArray(confirmations.yes) ? confirmations.yes : []), question.text];
      continue;
    }
    return { ...document, httpStatus: reply.status, ok: false, needs: question };
  }
  return { ok: false, error: "too many confirmation rounds" };
}

export function compatBadge(row) {
  const text = row.hostCompat || "unknown";
  const style = text.startsWith("in-range") ? styles.ok : text.includes("strict") ? styles.danger : styles.warn;
  return React.createElement("span", { style: { ...styles.badge, ...style }, "data-testid": `floofycrew-compat-${row.id}` }, text);
}

/** The source tier (Requirement 8.11): unlisted (a git reference, a path or an archive), listed (a registry record), tested (listed + a `tested` matrix cell for this host). */
export function tierBadge(row) {
  const tier = row.tier || "unlisted";
  const style = tier === "tested" ? styles.ok : tier === "listed" ? {} : styles.warn;
  const title = tier === "tested" ? "listed in a registry and graded tested on this host version by the Forge" : tier === "listed" ? "installed from a registry record (curated; index verified or allowed unsigned)" : "unlisted source: no curator review, no compatibility data — installed from a git reference, a path or an archive on your consent";
  return React.createElement("span", { style: { ...styles.badge, ...style }, title, "data-testid": `floofycrew-tier-${row.id}` }, `tier: ${tier}`);
}

function stateBadge(row) {
  if (row.active) return React.createElement("span", { style: { ...styles.badge, ...styles.ok } }, "active");
  const reason = row.reason || (row.enabled === false ? "UserDisabled" : "inactive");
  return React.createElement("span", { style: { ...styles.badge, ...(reason === "Error" || reason === "Quarantined" ? styles.danger : styles.warn) }, title: row.detail || "" }, reason);
}

function Parts({ parts }) {
  return React.createElement(
    "ul",
    { style: { margin: 0, paddingLeft: "1rem" } },
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
    { style: { margin: 0, paddingLeft: "1rem" } },
    items.map((item, index) => React.createElement("li", { key: index, style: item.kind === "governance" ? styles.warn : undefined, title: item.message }, `${item.kind === "governance" ? "governance " : ""}${item.code}`)),
  );
}

export default function FloofyCrewManager() {
  const [state, setState] = React.useState(null);
  const [rows, setRows] = React.useState([]);
  const [registry, setRegistry] = React.useState(null);
  const [statusMeta, setStatusMeta] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [output, setOutput] = React.useState("");
  const [error, setError] = React.useState("");

  const refresh = React.useCallback(async () => {
    const [stateReply, statusReply, registryReply] = await Promise.all([apiFetch("/state"), runCli(["status"]), apiFetch("/registry")]);
    if (stateReply.ok) setState(stateReply.body);
    if (statusReply.ok && statusReply.json) {
      setRows(statusReply.json.mods || []);
      setStatusMeta(statusReply.json);
    } else if (!statusReply.ok) {
      setError(statusReply.error || statusReply.stderr || `status failed (HTTP ${statusReply.httpStatus})`);
    }
    if (registryReply.ok) setRegistry(registryReply.body);
  }, []);

  React.useEffect(() => {
    ensureStarted().catch(() => undefined);
    refresh().catch((exc) => setError(String(exc)));
  }, [refresh]);

  const act = React.useCallback(
    async (path, body = {}) => {
      setBusy(true);
      setError("");
      try {
        const result = await callRoute(path, body);
        const argv = Array.isArray(result.argv) ? result.argv : [];
        const text = [argv.length ? `$ floofy ${argv.join(" ")}` : `→ ${path}`, ...(result.transcript || []), result.error ? `error: ${result.error}` : "", result.needs ? `needs a ${result.needs.kind} confirmation: ${result.needs.text}` : "", result.reloaded ? "(Loader reloaded)" : ""].filter(Boolean).join("\n");
        setOutput(text);
        if (!result.ok && !result.declined) setError(result.error || (result.needs ? `${result.needs.kind} confirmation needed` : `HTTP ${result.httpStatus}`));
      } catch (exc) {
        setError(String(exc));
      } finally {
        setBusy(false);
        await refresh().catch(() => undefined);
      }
    },
    [refresh],
  );

  const applyNow = React.useCallback(async () => {
    setBusy(true);
    try {
      const reply = await apiFetch("/reload", { method: "POST" });
      setOutput(reply.ok ? "Loader reloaded: pending installs applied, enabled.json re-read, patches re-applied" : `reload failed (HTTP ${reply.status})`);
    } finally {
      setBusy(false);
      await refresh().catch(() => undefined);
    }
  }, [refresh]);

  const host = (state && state.host) || {};
  const consent = (state && state.consent) || {};
  const pending = (statusMeta && statusMeta.pending) || [];
  const quarantine = (statusMeta && statusMeta.quarantine) || {};
  const updates = (registry && registry.updates) || {};

  return React.createElement(
    "div",
    { style: styles.page, "data-testid": "floofycrew-manager" },
    React.createElement("div", { style: styles.banner, "data-testid": "floofycrew-banner", role: "note" }, `FloofyCrew mod manager — UNOFFICIAL. ${UNOFFICIAL} Mods run with the gateway's privileges on your consent; governance verdicts below are warnings, never gates.`),
    React.createElement(
      "div",
      { style: styles.card },
      React.createElement("div", null, React.createElement("strong", null, "Loader: "), state ? `${state.loader} (FloofyCrew ${state.loaderVersion || "?"}, API ${state.api_version || "?"}, booted ${state.bootedAt || "?"})` : "loading…", state && state.vanilla ? " — vanilla boot (every mod disabled once)" : ""),
      React.createElement("div", null, React.createElement("strong", null, "Host: "), `${host.edition || "?"} ${host.version || "?"} (${host.channel || "channel unknown"})`),
      React.createElement(
        "div",
        { style: consent.required ? styles.danger : undefined, "data-testid": "floofycrew-consent" },
        React.createElement("strong", null, "Consent: "),
        consent.required ? `required — ${consent.message || "run floofy init"} (the acknowledgement is CLI-only: floofy init)` : `acknowledged by ${consent.by || "?"} on ${consent.acknowledgedAt || "?"}`,
      ),
      pending.length
        ? React.createElement("div", { style: styles.warn, "data-testid": "floofycrew-pending" }, `Staged (restart to apply): ${pending.join(", ")} `, React.createElement("button", { style: styles.button, onClick: applyNow, disabled: busy }, "Apply now"))
        : null,
      (state && state.governance && state.governance.length)
        ? React.createElement(
            "ul",
            { style: { ...styles.warn, margin: "0.4rem 0 0", paddingLeft: "1rem" } },
            state.governance.map((warning, index) => React.createElement("li", { key: index }, `governance ${warning.code}: ${warning.message}`)),
          )
        : null,
      React.createElement("div", { style: { ...styles.muted, marginTop: "0.4rem" } }, "Install new mods with `floofy install` — the disclosure, the confirmations for code parts and the typed per-file confirmation for governance-altering targets stay in the CLI."),
    ),
    React.createElement(
      "div",
      { style: styles.card },
      React.createElement("div", { style: { ...styles.row, ...styles.head } }, React.createElement("span", null, "Mod"), React.createElement("span", null, "State"), React.createElement("span", null, "Host compat · source tier"), React.createElement("span", null, "Parts (seam)"), React.createElement("span", null, "Actions")),
      rows.length === 0 ? React.createElement("div", { style: { ...styles.muted, padding: "0.6rem 0" } }, "no mods installed (floofy install <mod>)") : null,
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
            React.createElement("button", { style: styles.button, disabled: busy, "data-testid": `floofycrew-toggle-${row.id}`, onClick: () => act(`/mods/${encodeURIComponent(row.id)}/${row.enabled ? "disable" : "enable"}`) }, row.enabled ? "Disable" : "Enable"),
            update && update.update ? React.createElement("button", { style: styles.button, disabled: busy, onClick: () => act(`/mods/${encodeURIComponent(row.id)}/update`, { now: true }) }, `Update to ${update.candidate}`) : null,
            React.createElement("button", { style: styles.button, disabled: busy, onClick: () => (window.confirm(`Park ${row.id} in the quarantine for host ${host.version}?`) ? act(`/mods/${encodeURIComponent(row.id)}/yeet`, { reason: "manual yeet (app)" }) : null) }, "Yeet"),
            React.createElement("button", { style: { ...styles.button, ...styles.danger }, disabled: busy, "data-testid": `floofycrew-uninstall-${row.id}`, onClick: () => act(`/mods/${encodeURIComponent(row.id)}/uninstall`, { now: true }) }, "Uninstall"),
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
            .map(([version, mods]) =>
              React.createElement("div", { key: version }, React.createElement("code", null, version), `: ${(mods || []).join(", ") || "-"} `, React.createElement("button", { style: styles.button, disabled: busy, onClick: () => act("/restore", { version }) }, "Restore set")),
            ),
        )
      : null,
    error ? React.createElement("div", { style: { ...styles.card, ...styles.danger }, "data-testid": "floofycrew-error" }, error) : null,
    output ? React.createElement("pre", { style: { ...styles.card, ...styles.mono }, "data-testid": "floofycrew-output" }, output) : null,
  );
}
