// FloofyCrew manager App — the Doctor page (Requirement 7.4, 16.2; task 11.2).
//
// `floofy doctor` through `POST /doctor`: the problems first, then the host and
// its payloads (dormant ones flagged), the Loader, the consent record, the
// re-apply triggers and the early shim, the host's governance as information
// (what its policy would have said and which mods cross it), drift per payload,
// the compat verdict for this host, and the transcript the CLI would print.
import React from "react";

import { commandNote, styles } from "../palette.mjs";

function KV({ rows }) {
  return React.createElement(
    "div",
    { style: styles.kv },
    rows.flatMap(([key, value]) => [React.createElement("span", { key: `${key}-k`, style: styles.muted }, key), React.createElement("span", { key: `${key}-v` }, value == null || value === "" ? "—" : String(value))]),
  );
}

function Card({ title, testid, children }) {
  return React.createElement("div", { style: styles.card, "data-testid": testid }, React.createElement("h2", { style: styles.cardTitle }, title), children);
}

export function DoctorPage({ manager }) {
  const { act, busy } = manager;
  const [report, setReport] = React.useState(null);
  const [transcript, setTranscript] = React.useState([]);
  const run = React.useCallback(async () => {
    const result = await act("/doctor", {});
    if (result && result.ok) {
      setReport(result.json || null);
      setTranscript(result.transcript || []);
    }
  }, [act]);
  React.useEffect(() => {
    run().catch(() => undefined);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!report) return React.createElement("div", { style: styles.card, "data-testid": "floofycrew-doctor" }, busy ? "running floofy doctor…" : "no report yet ", React.createElement("button", { style: styles.button, onClick: run, disabled: busy }, "Run doctor"));
  const host = report.host || {};
  const loader = report.loader || {};
  const consent = report.consent || {};
  const governance = report.governance || {};
  const compat = report.compat || {};
  const triggers = ((report.triggers || {}).managers || []).map((t) => `${t.kind || t.name || "trigger"}: ${t.installed ? "installed" : "absent"}${t.detail ? ` (${t.detail})` : ""}`);
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      "div",
      { style: { ...styles.card, ...(report.problems && report.problems.length ? {} : styles.ok) }, "data-testid": "floofycrew-doctor" },
      React.createElement("h2", { style: styles.cardTitle }, report.problems && report.problems.length ? `${report.problems.length} problem(s)` : "no problems found"),
      React.createElement("ul", { style: { ...styles.list, ...styles.warn } }, (report.problems || []).map((problem, index) => React.createElement("li", { key: index }, problem))),
      React.createElement("button", { style: styles.button, onClick: run, disabled: busy, "data-testid": "floofycrew-doctor-run" }, "Run again"),
      commandNote(React, "The same at a terminal:", "floofy doctor"),
    ),
    React.createElement(Card, { title: "Host", testid: "floofycrew-doctor-host" }, React.createElement(KV, { rows: [["edition", host.edition], ["version", host.version], ["channel", host.channel], ["host home", host.hostHome], ["gateway", host.gateway ? `${host.gateway.running ? "running" : "not running"}${host.gateway.servedVersion ? `, serving ${host.gateway.servedVersion}${host.gateway.via ? ` (version via ${host.gateway.via})` : ""}` : host.gateway.running ? ", served version unknown" : ""}` : "?"]] }), React.createElement("ul", { style: { ...styles.list, marginTop: "0.4rem" } }, (host.payloads || []).map((p) => React.createElement("li", { key: p.id }, React.createElement("code", null, p.id), ` ${p.version} ${p.edition}/${p.channel || "?"}${p.current ? " · current" : ""}${p.dormant ? " · dormant (not serving)" : ""} — ${p.root}`)))),
    React.createElement(Card, { title: "Loader", testid: "floofycrew-doctor-loader" }, React.createElement(KV, { rows: [["installed", loader.installed ? `yes (v${loader.version || "?"}, enabled=${loader.enabled})` : "no"], ["state", loader.state ? `${loader.state.loader} — active ${(loader.state.active || []).join(", ") || "none"} (${loader.stateSource})` : "no state"], ["failure", loader.failure ? `${loader.failure.phase}: ${loader.failure.error}` : "none"], ["early shim", report.earlyShim ? JSON.stringify(report.earlyShim).slice(0, 160) : "—"], ["triggers", triggers.join("; ") || "none"]] }), commandNote(React, "Install or repair the Loader app, the re-apply trigger and the early shim at a terminal:", "floofy init [--early]")),
    React.createElement(Card, { title: "Consent", testid: "floofycrew-doctor-consent" }, React.createElement(KV, { rows: [["status", consent.status], ["acknowledged", consent.acknowledgedAt], ["by", consent.by], ["how", consent.how], ["warning version", `${consent.warningVersion ?? "—"} (current ${consent.currentWarningVersion})`]] })),
    React.createElement(Card, { title: "Governance (information only — never a gate)", testid: "floofycrew-doctor-governance" }, React.createElement("ul", { style: { ...styles.list, ...styles.warn } }, (governance.warnings || []).map((w, index) => React.createElement("li", { key: index }, `${w.code}: ${w.message}${w.affectedTargets && w.affectedTargets.length ? ` — ${w.affectedTargets.join(", ")}` : ""}`))), (governance.warnings || []).length === 0 ? React.createElement("div", { style: styles.muted }, "the host's policy raises no warning") : null),
    React.createElement(Card, { title: "Compatibility matrix", testid: "floofycrew-doctor-compat" }, React.createElement(KV, { rows: [["row for this host", compat.row ? `${compat.row.edition}/${compat.row.channel}/${compat.row.hostVersion}` : "no cell"], ["framework", compat.row && compat.row.framework ? JSON.stringify(compat.row.framework) : "—"], ["mods", compat.row && compat.row.mods ? Object.entries(compat.row.mods).map(([k, v]) => `${k}: ${typeof v === "string" ? v : v.verdict}`).join("; ") : "—"]] })),
    React.createElement(Card, { title: "Drift per payload" }, React.createElement("pre", { style: styles.mono }, JSON.stringify(report.drift || {}, null, 2).slice(0, 4000))),
    React.createElement("details", { style: styles.card }, React.createElement("summary", null, "Transcript (what `floofy doctor` prints)"), React.createElement("pre", { style: styles.mono }, transcript.join("\n"))),
  );
}
