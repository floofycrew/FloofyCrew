// The per-mod warnings in words (`ui/app/warnings.mjs`) under plain `node --test`: every code the
// validator, the resolver and the governance reader emit has a plain-words label, an unknown code
// falls back to its words, repeats collapse to one line with a count, and the codes stay in the tooltip.
import assert from "node:assert/strict";
import { test } from "node:test";

import { LABELS, describeWarnings, groupText, groupTitle, labelFor, wordsOfCode } from "../../ui/app/warnings.mjs";

// floofy_core.validator / resolver / governance — the codes the Loader can attach to a row
const EMITTED = [
  "UndeclaredHost", "PlaintextNetwork", "CredentialsHint", "GovernanceAltering", "SideMismatch", "UnlistedFile",
  "RecommendsMissing", "SuggestsMissing", "ConflictDeclared", "HostVersionOutOfRange", "HostEditionMismatch", "HostChannelMismatch",
  "AppAdmissionBanned", "AppAdmissionEnforced", "CapabilityDisabled", "ThemeInstallClosed", "ThirdPartyAppsDisabled",
  "UpdateSourcePinned", "UpdatePinWillWipePatches", "NoReapplyTrigger",
];

test("every emitted code has a plain-words label that is not the code itself", () => {
  for (const code of EMITTED) {
    const label = labelFor(code);
    assert.ok(LABELS[code], `${code} has a label`);
    assert.notEqual(label, code);
    assert.match(label, /^[a-z]/, `${code}: a label is a phrase, not an identifier`);
    assert.doesNotMatch(label, /[A-Z][a-z]+[A-Z]/, `${code}: no CamelCase leaks into the label`);
  }
});

test("an unknown code falls back to its words, never to the raw identifier", () => {
  assert.equal(wordsOfCode("SomethingNewHappened"), "something new happened");
  assert.equal(wordsOfCode("HTTPSRequired"), "https required");
  assert.equal(labelFor("SomethingNewHappened"), "something new happened");
  assert.equal(labelFor(""), "warning");
  assert.equal(labelFor(undefined), "warning");
});

test("repeats collapse to one line with a count; the code and every message stay in the tooltip", () => {
  const row = {
    id: "kirocrew-ide",
    warnings: [
      { code: "UndeclaredHost", message: "https:// URL to a.example, which is not declared in network.hosts[]", path: "bridge.py:10" },
      { code: "UndeclaredHost", message: "https:// URL to b.example, which is not declared in network.hosts[]", path: "bridge.py:12" },
      { code: "UndeclaredHost", message: "https:// URL to b.example, which is not declared in network.hosts[]", path: "bridge.py:12" },
      { code: "CredentialsHint", message: "code mentions credentials but network.credentials is false", path: "bridge.py:40" },
    ],
    governance: [{ code: "CapabilityDisabled", message: "the host's policy disables capabilities.theme_install", affectedTargets: ["kirocrew-ide"] }],
  };
  const groups = describeWarnings(row);
  assert.deepEqual(groups.map(groupText), ["contacts a host it does not declare ×3", "may ask for credentials", "governance: host policy disables a capability it uses"]);
  assert.deepEqual(groups.map((g) => g.kind), ["warning", "warning", "governance"], "findings first, governance after");
  const hosts = groups[0];
  assert.equal(hosts.code, "UndeclaredHost");
  assert.equal(hosts.messages.length, 2, "an identical message is listed once");
  const title = groupTitle(hosts);
  assert.ok(title.startsWith("UndeclaredHost\n"), "the code leads the tooltip for the audit log");
  assert.ok(title.includes("a.example") && title.includes("b.example") && title.includes("[bridge.py:10]"), title);
  for (const text of groups.map(groupText)) assert.doesNotMatch(text, /[A-Z][a-z]+[A-Z]/, `no raw code on the line: ${text}`);
});

test("a row without notes describes to nothing; malformed entries are skipped", () => {
  assert.deepEqual(describeWarnings({ id: "x" }), []);
  assert.deepEqual(describeWarnings(null), []);
  const groups = describeWarnings({ warnings: [null, "oops", { message: "no code at all" }] });
  assert.equal(groups.length, 1);
  assert.equal(groupText(groups[0]), "warning");
});
