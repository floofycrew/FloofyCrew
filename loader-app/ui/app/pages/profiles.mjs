// FloofyCrew manager App — the Profiles page (Requirement 7.5, 16.2; task 11.2).
//
// Named mod sets with pinned versions: list, save the installed set, show what
// switching would do (`profile use --check`), switch (staged, like installs) or
// switch now, export to a file, import from a file — the same handlers as
// `floofy profile …`, through the typed routes.
import React from "react";

import { apiFetch } from "../api.mjs";
import { commandNote, styles } from "../palette.mjs";

/** What a profile is, in one line (the page's intro). */
export const PROFILES_INTRO = "A profile is a named snapshot of your installed mods with their exact versions. Save one before you experiment, switch back to it to restore that set, or export it to carry a setup to another machine.";

export function ProfilesPage({ manager }) {
  const { busy, act, output } = manager;
  const [profiles, setProfiles] = React.useState(null);
  const [name, setName] = React.useState("");
  const [file, setFile] = React.useState("");
  const [exportTo, setExportTo] = React.useState({});

  const load = React.useCallback(async () => {
    const reply = await apiFetch("/profiles");
    setProfiles(reply.ok && reply.body && reply.body.json ? reply.body.json.profiles || [] : []);
  }, []);
  React.useEffect(() => {
    load().catch(() => setProfiles([]));
  }, [load, output]);

  const run = async (path, body) => {
    await act(path, body || {});
    await load().catch(() => undefined);
  };

  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      "div",
      { style: styles.card, "data-testid": "floofycrew-profiles" },
      React.createElement("h2", { style: styles.cardTitle }, "Profiles"),
      React.createElement("div", { style: styles.cardIntro, "data-testid": "floofycrew-profiles-intro" }, PROFILES_INTRO),
      profiles === null ? React.createElement("div", { style: styles.muted }, "loading…") : profiles.length === 0 ? React.createElement("div", { style: { ...styles.muted, padding: "0.4rem 0" } }, "no profiles saved") : null,
      (profiles || []).map((profile) =>
        React.createElement(
          "div",
          { key: profile.name, style: { borderTop: styles.row.borderTop, padding: "0.5rem 0" }, "data-testid": `floofycrew-profile-${profile.name}` },
          React.createElement("div", null, React.createElement("strong", null, profile.name), " ", React.createElement("span", { style: styles.muted }, `${profile.mods ?? "?"} mod(s), saved ${profile.savedAt || "?"} on host ${profile.hostVersion || "?"}`), profile.error ? React.createElement("span", { style: styles.danger }, ` ${profile.error}`) : null),
          React.createElement(
            "div",
            { style: { marginTop: "0.3rem", display: "flex", gap: "0.3rem", flexWrap: "wrap", alignItems: "center" } },
            React.createElement("button", { style: styles.button, disabled: busy, onClick: () => run(`/profiles/${encodeURIComponent(profile.name)}/use`, { check: true }) }, "Show the plan"),
            React.createElement("button", { style: { ...styles.button, ...styles.primary }, disabled: busy, onClick: () => run(`/profiles/${encodeURIComponent(profile.name)}/use`, {}) }, "Switch (staged)"),
            React.createElement("button", { style: styles.button, disabled: busy, onClick: () => run(`/profiles/${encodeURIComponent(profile.name)}/use`, { now: true }) }, "Switch now"),
            React.createElement("input", { style: { ...styles.input, minWidth: "18rem" }, value: exportTo[profile.name] || "", onChange: (e) => setExportTo({ ...exportTo, [profile.name]: e.target.value }), placeholder: `export to … /${profile.name}.floofy-profile.json`, "aria-label": `export path for ${profile.name}` }),
            React.createElement("button", { style: styles.button, disabled: busy || !(exportTo[profile.name] || "").trim(), onClick: () => run(`/profiles/${encodeURIComponent(profile.name)}/export`, { file: exportTo[profile.name].trim() }) }, "Export"),
          ),
        ),
      ),
      React.createElement(
        "form",
        { onSubmit: async (e) => { e.preventDefault(); if (name.trim()) { await run("/profiles", { name: name.trim() }); setName(""); } }, style: { display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.6rem" } },
        React.createElement("input", { style: styles.input, value: name, onChange: (e) => setName(e.target.value), placeholder: "profile name (letters, digits, - _ .)", "aria-label": "profile name", "data-testid": "floofycrew-profile-name" }),
        React.createElement("button", { type: "submit", style: { ...styles.button, ...styles.primary }, disabled: busy || !name.trim(), "data-testid": "floofycrew-profile-save" }, "Save the installed set"),
      ),
      React.createElement(
        "form",
        { onSubmit: async (e) => { e.preventDefault(); if (file.trim()) { await run("/profiles/import", { file: file.trim() }); setFile(""); } }, style: { display: "flex", gap: "0.5rem", alignItems: "center", marginTop: "0.4rem" } },
        React.createElement("input", { style: { ...styles.input, minWidth: "22rem" }, value: file, onChange: (e) => setFile(e.target.value), placeholder: "path of a profile file on this machine", "aria-label": "profile file", "data-testid": "floofycrew-profile-import-file" }),
        React.createElement("button", { type: "submit", style: styles.button, disabled: busy || !file.trim() }, "Import"),
      ),
      commandNote(React, "The same at a terminal:", "floofy profile {list,save,use [--now|--check],export,import}"),
    ),
  );
}
