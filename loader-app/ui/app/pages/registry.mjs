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

import { apiFetch } from "../api.mjs";
import { commandNote, styles } from "../palette.mjs";

const TRUST_LEVELS = ["index", "owner"];

function SourceRow({ source, manager }) {
  const cache = source.cache || {};
  const state = !cache || Object.keys(cache).length === 0 ? "never fetched" : cache.usable ? `usable: ${cache.mods ?? "?"} mod(s), signature ${(cache.signature || {}).status || "?"}, fetched ${cache.fetchedAt || "?"}` : `refused: ${cache.refusal || cache.error || "?"}`;
  const key = encodeURIComponent(source.key);
  return React.createElement(
    "div",
    { style: { borderTop: styles.row.borderTop, padding: "0.5rem 0" }, "data-testid": `floofycrew-source-${source.label}` },
    React.createElement("div", null, React.createElement("strong", null, source.label), " ", React.createElement("code", null, source.url)),
    React.createElement("div", { style: styles.muted }, `trust=${source.trust} · ${source.allowUnsigned ? "UNSIGNED INDEXES ACCEPTED" : "signature required"} · key ${source.keyId || (source.publicKey ? "pinned" : "-")} · ${state}`),
    React.createElement(
      "div",
      { style: { marginTop: "0.3rem" } },
      React.createElement("button", { style: styles.button, disabled: manager.busy, onClick: () => manager.act(`/registries/${key}/refresh`) }, "Refresh"),
      React.createElement("button", { style: styles.button, disabled: manager.busy, onClick: () => manager.act(`/registries/${key}/trust`, { trust: source.trust === "index" ? "owner" : "index", allowUnsigned: Boolean(source.allowUnsigned) }) }, `Trust: ${source.trust} → ${source.trust === "index" ? "owner" : "index"}`),
      React.createElement("button", { style: { ...styles.button, ...(source.allowUnsigned ? {} : styles.warn) }, disabled: manager.busy, "data-testid": `floofycrew-source-unsigned-${source.label}`, onClick: () => manager.act(`/registries/${key}/trust`, { trust: source.trust, allowUnsigned: !source.allowUnsigned }) }, source.allowUnsigned ? "Require a verified signature again" : "Accept unsigned indexes…"),
      React.createElement("button", { style: { ...styles.button, ...styles.danger }, disabled: manager.busy, onClick: () => manager.act(`/registries/${key}`, {}, { method: "DELETE" }) }, "Remove"),
    ),
  );
}

function AddSourceForm({ manager }) {
  const [url, setUrl] = React.useState("");
  const [trust, setTrust] = React.useState("index");
  const [allowUnsigned, setAllowUnsigned] = React.useState(false);
  const [name, setName] = React.useState("");
  const [publicKey, setPublicKey] = React.useState("");
  const submit = async (event) => {
    event.preventDefault();
    if (!url.trim()) return;
    const result = await manager.act("/registries", { url: url.trim(), trust, allowUnsigned, name: name.trim() || undefined, publicKey: publicKey.trim() || undefined, refresh: true });
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
    React.createElement("button", { type: "submit", style: { ...styles.button, ...styles.primary }, disabled: manager.busy || !url.trim(), "data-testid": "floofycrew-add-source-submit" }, "Add source"),
  );
}

/** One search hit: the tier an install would have here, the version known to work, the compat verdict, an Install button. */
export function SearchHit({ hit, manager }) {
  const works = hit.worksHere ? `works here: ${hit.worksHere}${hit.verdict ? ` (${hit.verdict})` : " (untested)"}` : `nothing known to work here${hit.why ? ` — ${hit.why}` : ""}`;
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

  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      "div",
      { style: styles.card, "data-testid": "floofycrew-sources" },
      React.createElement("h2", { style: styles.cardTitle }, "Sources"),
      React.createElement("div", { style: styles.muted }, `merged cache: ${cache.mods ?? 0} mod(s)${cache.generatedAt ? `, generated ${cache.generatedAt}` : ""}${(cache.notes || []).length ? ` — ${cache.notes.join("; ")}` : ""}`),
      sources.length === 0 ? React.createElement("div", { style: { ...styles.muted, padding: "0.4rem 0" } }, "no registry source configured") : sources.map((source) => (source.error ? React.createElement("div", { key: "error", style: styles.danger }, source.error) : React.createElement(SourceRow, { key: source.key || source.url, source, manager }))),
      React.createElement(
        "div",
        { style: { marginTop: "0.5rem" } },
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
      React.createElement("div", { style: styles.muted }, "ssh://<host>/<path>@<tag> or https://<host>/<owner>/<repo>[.git]@<tag> (optional #<subdirectory>): a shallow clone with your own git credentials; the unlisted-source line is confirmed in the next step."),
      React.createElement(
        "form",
        { onSubmit: async (e) => { e.preventDefault(); if (reference.trim()) { const r = await manager.act("/mods/install", { source: reference.trim() }); if (r && r.ok) setReference(""); } }, style: { display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.4rem" } },
        React.createElement("input", { style: { ...styles.input, minWidth: "28rem" }, value: reference, onChange: (e) => setReference(e.target.value), placeholder: "ssh://…@tag or https://…@tag", "aria-label": "git reference", "data-testid": "floofycrew-reference-input" }),
        React.createElement("button", { type: "submit", style: { ...styles.button, ...styles.primary }, disabled: busy || !reference.trim(), "data-testid": "floofycrew-reference-submit" }, "Install (staged)"),
      ),
    ),
  );
}
