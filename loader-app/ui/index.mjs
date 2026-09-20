// FloofyCrew Loader — the manager page (`ui.entry`; Requirement 2.7, 7.3, 11.8).
//
// The host's AppHost renders the default export of this module as a React
// component when the user opens /apps/floofycrew (design "Spike outcomes" 1.3:
// ui.entry loads only on page open). `react` resolves through the host's inline
// import map (index.html: react -> /vendor/react.mjs); no bundler, no npm.
//
// Everything the page does goes through the Loader's own routes, same-origin,
// behind the dashboard auth: GET /state (the Loader's verdicts), GET /registry
// (update availability), POST /reload ("apply now"), and POST /cli {argv} which
// runs an ALLOWLISTED `floofy` command in-process (enable/disable/uninstall/
// update/yeet/apply/status/...), so the UI and the CLI never diverge. What needs
// a human at a keyboard stays CLI-only and the page says so: the one-time
// consent (`floofy init`) and governance-altering per-file confirmations
// (`floofy install`). The page never contacts a remote origin.
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

/** Run one allowlisted floofy command in-process (POST /cli). */
export async function runCli(argv) {
  const reply = await apiFetch("/cli", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ argv }) });
  const body = reply.body || {};
  return { ...body, httpStatus: reply.status, ok: reply.ok && body.ok !== false };
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
    async (argv, { confirm } = {}) => {
      if (confirm && !window.confirm(confirm)) return;
      setBusy(true);
      setError("");
      try {
        const result = await runCli(argv);
        const text = [`$ floofy ${argv.join(" ")}`, result.stdout || "", result.stderr || "", result.json && result.json.error ? `error: ${result.json.error}` : "", result.reloaded ? "(Loader reloaded)" : ""].filter(Boolean).join("\n");
        setOutput(text);
        if (!result.ok) setError(result.error || result.stderr || `exit ${result.exit}`);
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
            React.createElement("button", { style: styles.button, disabled: busy, "data-testid": `floofycrew-toggle-${row.id}`, onClick: () => act([row.enabled ? "disable" : "enable", row.id]) }, row.enabled ? "Disable" : "Enable"),
            update && update.update ? React.createElement("button", { style: styles.button, disabled: busy, onClick: () => act(["update", row.id, "--now"]) }, `Update to ${update.candidate}`) : null,
            React.createElement("button", { style: styles.button, disabled: busy, onClick: () => act(["yeet", row.id], { confirm: `Park ${row.id} in the quarantine for host ${host.version}?` }) }, "Yeet"),
            React.createElement("button", { style: { ...styles.button, ...styles.danger }, disabled: busy, "data-testid": `floofycrew-uninstall-${row.id}`, onClick: () => act(["uninstall", row.id, "--now"], { confirm: `Uninstall ${row.id} ${row.version}?` }) }, "Uninstall"),
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
              React.createElement("div", { key: version }, React.createElement("code", null, version), `: ${(mods || []).join(", ") || "-"} `, React.createElement("button", { style: styles.button, disabled: busy, onClick: () => act(["yeet", "--restore", version]) }, "Restore set")),
            ),
        )
      : null,
    error ? React.createElement("div", { style: { ...styles.card, ...styles.danger }, "data-testid": "floofycrew-error" }, error) : null,
    output ? React.createElement("pre", { style: { ...styles.card, ...styles.mono }, "data-testid": "floofycrew-output" }, output) : null,
  );
}
