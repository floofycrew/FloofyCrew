// FloofyCrew manager App — the Registry page (Requirement 16.2, 16.6, 8.3, 8.4, 8.11; task 11.2 → 11.4).
//
// The sources with their trust settings and last-refresh verdict (refresh,
// change trust, accept/require unsigned indexes — behind the same loosening
// warning as `--allow-unsigned` — remove, add, record the edition defaults),
// a search over the merged cache with the source tier and the host-compat
// verdict each record would install with, install from a record or from a
// pasted git reference, and per installed mod the update availability with
// the changelog link — everything through the typed routes.
import React from "react";

import { UpdateAndRestartButton } from "../actions.mjs";
import { apiFetch } from "../api.mjs";
import { commandNote, styles } from "../palette.mjs";

const TRUST_LEVELS = ["index", "owner"];

/** The last-refresh verdict of one source, as two short cells: the signature and the fetch. */
export function sourceVerdict(source) {
  const cache = source.cache || {};
  if (!cache || Object.keys(cache).length === 0) return { signature: "—", fetch: "never fetched", tone: "muted" };
  if (cache.usable) return { signature: (cache.signature || {}).status || "?", fetch: `${cache.mods ?? "?"} mod(s), fetched ${cache.fetchedAt || "?"}`, tone: "ok" };
  return { signature: (cache.signature || {}).status || "—", fetch: `refused: ${cache.refusal || cache.error || "?"}`, tone: "danger" };
}

/** One source as a table row: name and URL, trust, signature policy and pinned key, the last refresh, the actions. */
function SourceRow({ source, manager }) {
  const key = encodeURIComponent(source.key);
  const verdict = sourceVerdict(source);
  const flip = source.trust === "index" ? "owner" : "index";
  return React.createElement(
    "tr",
    { "data-testid": `floofycrew-source-${source.label}` },
    React.createElement("td", { style: styles.td }, React.createElement("div", null, React.createElement("strong", null, source.label)), React.createElement("code", { style: { ...styles.command, wordBreak: "break-all" } }, source.url)),
    React.createElement("td", { style: styles.td }, React.createElement("span", { style: styles.badge }, source.trust)),
    React.createElement("td", { style: styles.td }, React.createElement("div", { style: source.allowUnsigned ? styles.warn : undefined }, source.allowUnsigned ? "unsigned indexes accepted" : "signature required"), React.createElement("div", { style: styles.muted }, `key ${source.keyId || (source.publicKey ? "pinned" : "—")}`)),
    React.createElement("td", { style: styles.td }, React.createElement("div", { style: styles[verdict.tone] || styles.muted }, verdict.signature), React.createElement("div", { style: styles.muted }, verdict.fetch)),
    React.createElement(
      "td",
      { style: { ...styles.td, whiteSpace: "nowrap" } },
      React.createElement("button", { style: styles.button, disabled: manager.busy, onClick: () => manager.act(`/registries/${key}/refresh`) }, "Refresh"),
      React.createElement("button", { style: styles.button, disabled: manager.busy, onClick: () => manager.act(`/registries/${key}/trust`, { trust: flip, allowUnsigned: Boolean(source.allowUnsigned) }) }, `Trust → ${flip}`),
      React.createElement("br", null),
      React.createElement("button", { style: { ...styles.button, ...(source.allowUnsigned ? {} : styles.warn) }, disabled: manager.busy, "data-testid": `floofycrew-source-unsigned-${source.label}`, onClick: () => manager.act(`/registries/${key}/trust`, { trust: source.trust, allowUnsigned: !source.allowUnsigned }) }, source.allowUnsigned ? "Require a signature again" : "Accept unsigned…"),
      React.createElement("button", { style: { ...styles.button, ...styles.danger }, disabled: manager.busy, onClick: () => manager.act(`/registries/${key}`, {}, { method: "DELETE" }) }, "Remove"),
    ),
  );
}

/** The sources table (Registry › Sources): one row per configured source, the columns the CLI's `registry list` prints. */
function SourcesTable({ sources, manager }) {
  const errors = sources.filter((s) => s.error);
  const rows = sources.filter((s) => !s.error);
  return React.createElement(
    React.Fragment,
    null,
    errors.map((source, index) => React.createElement("div", { key: `error-${index}`, style: styles.danger }, source.error)),
    rows.length === 0
      ? null
      : React.createElement(
          "table",
          { style: { ...styles.table, marginTop: "0.5rem" }, "data-testid": "floofycrew-sources-table" },
          React.createElement("thead", null, React.createElement("tr", null, ["Source", "Trust", "Signature policy", "Last refresh", "Actions"].map((label) => React.createElement("th", { key: label, style: styles.th, scope: "col" }, label)))),
          React.createElement("tbody", null, rows.map((source) => React.createElement(SourceRow, { key: source.key || source.url, source, manager }))),
        ),
  );
}

function AddSourceForm({ manager }) {
  const [url, setUrl] = React.useState("");
  const [trust, setTrust] = React.useState("index");
  const [allowUnsigned, setAllowUnsigned] = React.useState(false);
  const [name, setName] = React.useState("");
  const [publicKey, setPublicKey] = React.useState("");
  const [refresh, setRefresh] = React.useState(true);
  const submit = async (event) => {
    event.preventDefault();
    if (!url.trim()) return;
    const result = await manager.act("/registries", { url: url.trim(), trust, allowUnsigned, name: name.trim() || undefined, publicKey: publicKey.trim() || undefined, refresh });
    if (result && result.ok) {
      setUrl("");
      setName("");
      setPublicKey("");
      setAllowUnsigned(false);
    }
  };
  return React.createElement(
    "form",
    { onSubmit: submit, style: { display: "flex", gap: "0.5rem", flexWrap: "wrap", alignItems: "center" }, "data-testid": "floofycrew-add-source" },
    React.createElement("input", { style: { ...styles.input, minWidth: "22rem" }, value: url, onChange: (e) => setUrl(e.target.value), placeholder: "https URL of the registry directory (index.json, compat.json, .sig)", "aria-label": "registry URL", "data-testid": "floofycrew-add-source-url" }),
    React.createElement("input", { style: { ...styles.input, minWidth: "8rem" }, value: name, onChange: (e) => setName(e.target.value), placeholder: "name (optional)", "aria-label": "source name", "data-testid": "floofycrew-add-source-name" }),
    React.createElement("input", { style: { ...styles.input, minWidth: "18rem" }, value: publicKey, onChange: (e) => setPublicKey(e.target.value), placeholder: "pinned Ed25519 public key (optional)", "aria-label": "public key", "data-testid": "floofycrew-add-source-key" }),
    React.createElement(
      "select",
      { style: styles.input, value: trust, onChange: (e) => setTrust(e.target.value), "aria-label": "trust level" },
      TRUST_LEVELS.map((level) => React.createElement("option", { key: level, value: level }, `trust: ${level}`)),
    ),
    React.createElement("label", { style: styles.muted }, React.createElement("input", { type: "checkbox", checked: allowUnsigned, onChange: (e) => setAllowUnsigned(e.target.checked), "data-testid": "floofycrew-add-source-unsigned" }), " accept unsigned indexes (warned, audited)"),
    React.createElement("label", { style: styles.muted }, React.createElement("input", { type: "checkbox", checked: refresh, onChange: (e) => setRefresh(e.target.checked), "data-testid": "floofycrew-add-source-refresh" }), " fetch now"),
    React.createElement("button", { type: "submit", style: { ...styles.button, ...styles.primary }, disabled: manager.busy || !url.trim(), "data-testid": "floofycrew-add-source-submit" }, "Add source"),
  );
}

/** The matrix verdict of one version on this host: the folded compat cell (tested / expected / broken), else untested. */
export function compatCell(version, hostVersion) {
  const cell = version && version.compat && hostVersion ? version.compat[hostVersion] : undefined;
  return cell || "untested";
}

/** One search hit: the tier an install would have here, the version known to work, every version's compat cell for this host, Install. */
export function SearchHit({ hit, manager }) {
  const hostVersion = ((manager.state || {}).host || {}).version;
  const works = hit.worksHere ? `works here: ${hit.worksHere}${hit.verdict ? ` (${hit.verdict})` : " (untested)"}` : `nothing known to work here${hit.why ? ` — ${hit.why}` : ""}`;
  const cellStyle = (cell) => (cell === "tested" ? styles.ok : cell === "expected" ? styles.warn : cell === "broken" ? styles.danger : styles.muted);
  return React.createElement(
    "div",
    { style: { borderTop: styles.row.borderTop, padding: "0.5rem 0" }, "data-testid": `floofycrew-hit-${hit.key}` },
    React.createElement("div", null, React.createElement("strong", null, hit.name || hit.key), " ", React.createElement("code", null, hit.key), " ", React.createElement("span", { style: styles.muted }, `newest ${hit.newest || "?"}`), hit.installed ? React.createElement("span", { style: { ...styles.badge, marginLeft: "0.4rem" } }, `installed ${hit.installed}`) : null),
    React.createElement("div", { style: styles.muted }, hit.description || ""),
    React.createElement(
      "div",
      { style: { marginTop: "0.3rem" } },
      React.createElement("span", { style: { ...styles.badge, ...(hit.tier === "tested" ? styles.ok : {}) }, "data-testid": `floofycrew-hit-tier-${hit.key}` }, `tier: ${hit.tier || "listed"}`),
      React.createElement("span", { style: { ...styles.badge, ...(hit.verdict === "tested" ? styles.ok : hit.verdict === "expected" ? styles.warn : hit.worksHere ? {} : styles.danger) }, "data-testid": `floofycrew-hit-compat-${hit.key}` }, works),
      React.createElement("span", { style: styles.badge }, `${hit.record || "archive"} record · ${hit.source || ""}`),
    ),
    React.createElement(
      "div",
      { style: { marginTop: "0.3rem" }, "data-testid": `floofycrew-hit-versions-${hit.key}` },
      React.createElement("span", { style: styles.muted }, `versions on ${hostVersion || "this host"}: `),
      (hit.versions || []).map((version) => React.createElement("span", { key: version.version, style: { ...styles.badge, ...cellStyle(compatCell(version, hostVersion)) }, title: version.changelog || "" }, `${version.version}: ${compatCell(version, hostVersion)}${version.yanked ? " (yanked)" : ""}`)),
    ),
    React.createElement(
      "div",
      { style: { marginTop: "0.3rem" } },
      React.createElement("button", { style: { ...styles.button, ...styles.primary }, disabled: manager.busy || !hit.worksHere, "data-testid": `floofycrew-hit-install-${hit.key}`, onClick: () => manager.act("/mods/install", { source: hit.key }) }, hit.installed ? "Reinstall (staged)" : "Install (staged)"),
      React.createElement("button", { style: styles.button, disabled: manager.busy || !hit.worksHere, onClick: () => manager.act("/mods/install", { source: hit.key, now: true }) }, "Install now"),
    ),
  );
}

export function RegistryPage({ manager, rest }) {
  const { registry, busy } = manager;
  const [query, setQuery] = React.useState(rest || "");
  const [hits, setHits] = React.useState(null);
  const [reference, setReference] = React.useState("");
  const [checking, setChecking] = React.useState(false);
  const [checked, setChecked] = React.useState(null);
  const sources = (registry && registry.sources) || [];
  const cache = (registry && registry.cache) || {};

  const search = React.useCallback(async (text) => {
    const reply = await apiFetch(`/search?q=${encodeURIComponent(text || "")}`);
    if (reply.ok && reply.body && reply.body.json) setHits(reply.body.json.mods || []);
    else setHits([]);
  }, []);
  React.useEffect(() => {
    search(query).catch(() => setHits([]));
  }, [registry]); // eslint-disable-line react-hooks/exhaustive-deps

  const updates = Object.entries((registry && registry.updates) || {}).filter(([, u]) => u && u.update);
  const selfUpdate = registry && registry.selfUpdate;

  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      "div",
      { style: styles.card, "data-testid": "floofycrew-updates" },
      React.createElement("h2", { style: styles.cardTitle }, "Updates"),
      selfUpdate && selfUpdate.available
        ? React.createElement(
            "div",
            { style: { ...styles.warn, marginBottom: "0.6rem", display: "flex", flexWrap: "wrap", alignItems: "center", gap: "0.4rem 0.6rem" }, "data-testid": "floofycrew-self-update" },
            React.createElement("span", null, `FloofyCrew ${selfUpdate.version} is available for this host`, selfUpdate.notes ? React.createElement(React.Fragment, null, " — ", React.createElement("a", { href: selfUpdate.notes, target: "_blank", rel: "noopener noreferrer", style: styles.accent }, "release notes")) : null),
            React.createElement(UpdateAndRestartButton, { manager, version: selfUpdate.version, testid: "floofycrew-self-update-restart-registry" }),
            React.createElement("span", { style: styles.muted }, "or at a terminal: ", React.createElement("code", { style: styles.command }, "floofy self-update")),
          )
        : null,
      updates.length === 0
        ? React.createElement("div", { style: styles.muted }, "every installed mod is at the newest version the registry cache knows to work here")
        : updates.map(([id, update]) =>
            React.createElement(
              "div",
              { key: id, style: { borderTop: styles.row.borderTop, padding: "0.4rem 0" }, "data-testid": `floofycrew-update-row-${id}` },
              React.createElement("strong", null, id),
              ` ${update.installed} → ${update.candidate}${update.verdict ? ` (${update.verdict} here)` : " (untested here)"} `,
              update.changelog ? React.createElement("a", { href: update.changelog, target: "_blank", rel: "noopener noreferrer", style: styles.accent, "data-testid": `floofycrew-update-changelog-${id}` }, "release notes") : null,
              React.createElement("div", { style: { marginTop: "0.2rem" } }, React.createElement("button", { style: { ...styles.button, ...styles.primary }, disabled: busy, onClick: () => manager.act(`/mods/${encodeURIComponent(id)}/update`, {}) }, "Update (staged)"), React.createElement("button", { style: styles.button, disabled: busy, onClick: () => manager.act(`/mods/${encodeURIComponent(id)}/update`, { now: true }) }, "Update now")),
            ),
          ),
      React.createElement(
        "div",
        { style: { marginTop: "0.5rem", display: "flex", alignItems: "center", flexWrap: "wrap", gap: "0.2rem 0.4rem" } },
        React.createElement(
          "button",
          {
            style: styles.button,
            disabled: busy || checking,
            "data-testid": "floofycrew-update-check",
            onClick: async () => {
              setChecking(true);
              setChecked(null);
              try {
                const result = await manager.act("/mods/update-check", {});
                setChecked(result && result.ok ? { ok: true } : { ok: false, error: (result && result.error) || "the check failed" });
              } finally {
                setChecking(false);
              }
            },
          },
          checking ? "Checking…" : "Check for updates",
        ),
        checking ? React.createElement("span", { style: styles.muted, "data-testid": "floofycrew-update-check-busy" }, "asking every source…") : null,
        // `updates` re-renders from the refresh act() ran, so the count below is the post-check truth
        checked ? React.createElement("span", { style: checked.ok ? (updates.length ? styles.ok : styles.muted) : styles.danger, "data-testid": "floofycrew-update-check-result" }, checked.ok ? (updates.length ? `checked — ${updates.length} update(s) available above` : "checked — everything is at the newest version known to work here") : `check failed: ${checked.error}`) : null,
        updates.length ? React.createElement("button", { style: { ...styles.button, ...styles.primary }, disabled: busy, "data-testid": "floofycrew-update-all", onClick: () => manager.act("/mods/update-all", {}) }, `Update all ${updates.length} (staged)`) : null,
        updates.length ? React.createElement("button", { style: styles.button, disabled: busy, onClick: () => manager.act("/mods/update-all", { now: true }) }, "Update all now") : null,
      ),
      commandNote(React, "The same at a terminal:", "floofy update [--all] [--check] [--now]"),
    ),
    React.createElement(
      "div",
      { style: styles.card, "data-testid": "floofycrew-sources" },
      React.createElement("h2", { style: styles.cardTitle }, "Sources"),
      React.createElement("div", { style: styles.cardIntro }, "Where mods come from: each source is a signed registry directory (index.json, compat.json, .sig). The merged cache is what Search and Updates read."),
      React.createElement("div", { style: styles.muted }, `merged cache: ${cache.mods ?? 0} mod(s)${cache.generatedAt ? `, generated ${cache.generatedAt}` : ""}${(cache.notes || []).length ? ` — ${cache.notes.join("; ")}` : ""}`),
      sources.length === 0 ? React.createElement("div", { style: { ...styles.muted, padding: "0.4rem 0" } }, "no registry source configured") : React.createElement(SourcesTable, { sources, manager }),
      React.createElement(
        "div",
        { style: { marginTop: "0.8rem" } },
        React.createElement("button", { style: styles.button, disabled: busy, onClick: () => manager.act("/registries/refresh") }, "Refresh every source"),
        React.createElement("button", { style: styles.button, disabled: busy, "data-testid": "floofycrew-registry-defaults", onClick: () => manager.act("/registries/defaults", { refresh: true, hostRegistry: true }) }, "Record the edition's default source"),
      ),
      React.createElement("h3", { style: { ...styles.cardTitle, marginTop: "0.8rem", fontSize: "0.9rem" } }, "Add a source"),
      React.createElement(AddSourceForm, { manager }),
      commandNote(React, "The same at a terminal:", "floofy registry add <url> [--trust index|owner] [--allow-unsigned] [--public-key KEY]"),
    ),
    React.createElement(
      "div",
      { style: styles.card, "data-testid": "floofycrew-search" },
      React.createElement("h2", { style: styles.cardTitle }, "Search the registry"),
      React.createElement(
        "form",
        { onSubmit: (e) => { e.preventDefault(); search(query).catch(() => setHits([])); }, style: { display: "flex", gap: "0.5rem", alignItems: "center" } },
        React.createElement("input", { style: { ...styles.input, minWidth: "20rem" }, value: query, onChange: (e) => setQuery(e.target.value), placeholder: "words to match in id, name, description and tags (empty lists everything)", "aria-label": "search", "data-testid": "floofycrew-search-input" }),
        React.createElement("button", { type: "submit", style: styles.button, disabled: busy, "data-testid": "floofycrew-search-submit" }, "Search"),
      ),
      hits === null ? React.createElement("div", { style: styles.muted }, "searching…") : hits.length === 0 ? React.createElement("div", { style: { ...styles.muted, padding: "0.4rem 0" } }, "no match in the registry cache (refresh a source, or record the edition's default source)") : hits.map((hit) => React.createElement(SearchHit, { key: hit.key, hit, manager })),
    ),
    React.createElement(
      "div",
      { style: styles.card, "data-testid": "floofycrew-install-reference" },
      React.createElement("h2", { style: styles.cardTitle }, "Install from a git reference"),
      React.createElement("div", { style: styles.muted }, "ssh://<host>/<path> or https://<host>/<owner>/<repo>[.git], optional @<tag> and #<subdirectory>: the repository's default branch unless a tag pins a point, shallow-cloned with your own git credentials; the version and host compatibility come from the mod's floofy.json, and the unlisted-source line is confirmed in the next step."),
      React.createElement(
        "form",
        { onSubmit: async (e) => { e.preventDefault(); if (reference.trim()) { const r = await manager.act("/mods/install", { source: reference.trim() }); if (r && r.ok) setReference(""); } }, style: { display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.4rem" } },
        React.createElement("input", { style: { ...styles.input, minWidth: "28rem" }, value: reference, onChange: (e) => setReference(e.target.value), placeholder: "ssh://… or https://… (@tag optional — default branch without it)", "aria-label": "git reference", "data-testid": "floofycrew-reference-input" }),
        React.createElement("button", { type: "submit", style: { ...styles.button, ...styles.primary }, disabled: busy || !reference.trim(), "data-testid": "floofycrew-reference-submit" }, "Install (staged)"),
      ),
    ),
  );
}
