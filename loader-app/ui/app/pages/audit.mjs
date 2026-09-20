// FloofyCrew manager App — the Audit page (Requirement 11.5, 16.2; task 11.2).
//
// `floofy audit [--tail N] [--op OP]` through `GET /audit`: every mutating
// operation with who (actor / OS user), what, when, the mod, the result, the
// governance flags and the consent reference — the same rows whichever surface
// wrote them (cli, tui, app, loader, patcher, trigger).
import React from "react";

import { apiFetch } from "../api.mjs";
import { commandNote, styles } from "../palette.mjs";

const OPS = ["", "install", "uninstall", "enable", "disable", "update", "yeet", "yeet-restore", "apply", "restore", "consent", "init", "registry-add", "registry-remove", "registry-refresh", "registry-trust-loosened", "governance-target-confirm", "profile-save", "profile-use", "net-denied"];

export function AuditPage({ manager }) {
  const [tail, setTail] = React.useState(50);
  const [op, setOp] = React.useState("");
  const [rows, setRows] = React.useState(null);
  const [path, setPath] = React.useState("");
  const load = React.useCallback(async () => {
    const query = new URLSearchParams();
    if (tail) query.set("tail", String(tail));
    if (op) query.set("op", op);
    const reply = await apiFetch(`/audit?${query}`);
    if (reply.ok && reply.body && reply.body.json) {
      setRows(reply.body.json.rows || []);
      setPath(reply.body.json.path || "");
    } else {
      setRows([]);
    }
  }, [tail, op]);
  React.useEffect(() => {
    load().catch(() => setRows([]));
  }, [load, manager.output]);

  return React.createElement(
    "div",
    { style: styles.card, "data-testid": "floofycrew-audit" },
    React.createElement("h2", { style: styles.cardTitle }, "Audit log"),
    React.createElement("div", { style: styles.muted }, path ? `${path} — every mutating operation, one row each; the consent reference ties a row to the acknowledgement it ran under` : "…"),
    React.createElement(
      "div",
      { style: { display: "flex", gap: "0.5rem", alignItems: "center", margin: "0.5rem 0" } },
      React.createElement("label", { style: styles.muted }, "last ", React.createElement("input", { type: "number", min: 1, max: 5000, style: { ...styles.input, minWidth: "5rem" }, value: tail, onChange: (e) => setTail(Math.max(1, Number(e.target.value) || 1)), "data-testid": "floofycrew-audit-tail" }), " rows"),
      React.createElement("select", { style: styles.input, value: op, onChange: (e) => setOp(e.target.value), "aria-label": "operation filter", "data-testid": "floofycrew-audit-op" }, OPS.map((name) => React.createElement("option", { key: name, value: name }, name || "every operation"))),
      React.createElement("button", { style: styles.button, onClick: load }, "Reload"),
    ),
    rows === null ? React.createElement("div", { style: styles.muted }, "loading…") : rows.length === 0 ? React.createElement("div", { style: styles.muted }, "no audit rows") : null,
    React.createElement(
      "div",
      { style: { display: "grid", gridTemplateColumns: "max-content max-content max-content 1fr", gap: "0.15rem 0.8rem", fontSize: "0.82rem" } },
      (rows || [])
        .slice()
        .reverse()
        .flatMap((row, index) => [
          React.createElement("span", { key: `${index}-ts`, style: styles.muted }, row.ts || ""),
          React.createElement("span", { key: `${index}-op`, style: row.result === "declined" || row.result === "error" || row.result === "refused" ? styles.warn : {} }, `${row.op || "?"}${row.mod ? ` ${row.mod}${row.version ? `@${row.version}` : ""}` : ""}`),
          React.createElement("span", { key: `${index}-who`, style: styles.muted }, `${row.actor || "?"}/${row.by || "?"}`),
          React.createElement("span", { key: `${index}-detail`, title: JSON.stringify(row, null, 1) }, `${row.result || ""}${row.governanceFlags && row.governanceFlags.length ? ` governance=${row.governanceFlags.join(",")}` : ""}${row.detail ? ` — ${row.detail}` : ""}`),
        ]),
    ),
    commandNote(React, "The same at a terminal:", "floofy audit [--tail N] [--op OP] [--json]"),
  );
}
