// FloofyCrew manager App — the per-mod warnings in words (Requirement 16.1, 11.2).
//
// The Loader attaches two kinds of note to a mod row: validator and resolver
// findings under `warnings[]` ({code, message, path?}) and host governance notes
// under `governance[]` ({code, message, affectedTargets}). Their codes are stable
// identifiers for tests and the audit log (`UndeclaredHost`, `RecommendsMissing`),
// not labels; the table shows a short plain-words label per distinct code, with a
// count when a code repeats, and keeps every full message in the tooltip. Pure
// data, no React: `node --test loader-app/tests/ui` covers it.

/** Plain-words labels per code. Anything unlisted falls back to the code split into words. */
export const LABELS = Object.freeze({
  // floofy_core.validator (the manifest, the parts, the network scan)
  UndeclaredHost: "contacts a host it does not declare",
  PlaintextNetwork: "uses a plaintext (http/ws) URL",
  CredentialsHint: "may ask for credentials",
  GovernanceAltering: "touches a host governance file",
  SideMismatch: "a part targets the wrong side",
  UnlistedFile: "ships a file its manifest does not list",
  // floofy_core.resolver (soft dependencies, host range)
  RecommendsMissing: "a recommended mod is missing",
  SuggestsMissing: "a suggested mod is missing",
  ConflictDeclared: "another mod declares a conflict with it",
  HostVersionOutOfRange: "host version outside its declared range",
  HostEditionMismatch: "not declared for this host edition",
  HostChannelMismatch: "not declared for this host channel",
  // floofy_core.governance (the host's policy files — information, never a gate)
  AppAdmissionBanned: "banned by the host's app admission policy",
  AppAdmissionEnforced: "host app admission is in enforce mode",
  CapabilityDisabled: "host policy disables a capability it uses",
  ThemeInstallClosed: "host policy disables theme install",
  ThirdPartyAppsDisabled: "host policy disables third-party apps",
  UpdateSourcePinned: "host policy pins updates",
  UpdatePinWillWipePatches: "a pinned host update will wipe its patches",
  NoReapplyTrigger: "no re-apply trigger is installed",
});

/** `RecommendsMissing` → "recommends missing": the fallback for a code without a label. */
export function wordsOfCode(code) {
  return String(code || "")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .toLowerCase()
    .trim();
}

/** The label for one code. */
export function labelFor(code) {
  return LABELS[code] || wordsOfCode(code) || "warning";
}

/**
 * Group a row's warnings for display: one entry per (kind, code), with the count
 * and every message (deduplicated, in order) for the tooltip. Governance notes
 * come after validator/resolver findings and are flagged `kind: "governance"`.
 */
export function describeWarnings(row) {
  const groups = new Map();
  const add = (kind, entry) => {
    if (!entry || typeof entry !== "object") return;
    const code = String(entry.code || "Warning");
    const key = `${kind}:${code}`;
    const group = groups.get(key) || { kind, code, label: labelFor(code), count: 0, messages: [] };
    group.count += 1;
    const message = [entry.message, entry.path ? `[${entry.path}]` : ""].filter(Boolean).join(" ");
    if (message && !group.messages.includes(message)) group.messages.push(message);
    groups.set(key, group);
  };
  for (const w of (row && row.warnings) || []) add("warning", w);
  for (const w of (row && row.governance) || []) add("governance", w);
  return [...groups.values()];
}

/** The one-line text of a group: the label, "×N" when the code repeats. */
export function groupText(group) {
  return `${group.kind === "governance" ? "governance: " : ""}${group.label}${group.count > 1 ? ` ×${group.count}` : ""}`;
}

/** The tooltip of a group: the code (for the audit log) and every message. */
export function groupTitle(group) {
  return [group.code, ...group.messages].join("\n");
}
