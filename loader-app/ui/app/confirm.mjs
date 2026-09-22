// FloofyCrew manager App — the confirmations (Requirement 16.3, 16.5, 11.1, 11.4, 11.7, 8.8; task 11.3).
//
// The client half of the 409 protocol of `floofy_loader.app_routes`: every
// question the CLI would ask arrives from the Loader as
// `{kind, text, expects, targets, detail}` (plus the install disclosure) and is
// presented here with the grade the CLI gives it:
//
// * `consent` — the one-time warning, the SAME text the CLI prints, a single
//   focused `[ I AGREE ]` control; Enter agrees, Esc (or Decline) declines. The
//   agreement is recorded by the Loader with `how: "app"`.
// * `yes-no` — the disclosure (parts and seams, whether payload files are
//   modified, the declared network hosts and credentials, the validator's
//   flags, the host's verdicts, the source tier, the unlisted-source line) with
//   the exact prompt and Yes / No; the staged-vs-apply-now choice sits here.
// * `governance-target` — the disclosure and one TEXT FIELD per governance-
//   altering target: the user types the exact path (submit stays disabled until
//   it matches — and the server compares again, byte for byte). Never a button.
// * `unlisted-source` — the disclosure with the unlisted-source line and an
//   explicit "I ACCEPT" item that supplies the phrase the CLI would have typed.
// * `unsigned-index` — the loosening warning and an explicit accept item.
//
// `useAsker()` gives the shell an `ask(question, reply)` for `callRoute` and
// the pending question `ConfirmationHost` renders; an answer resolves the
// promise with the confirmations to merge (or `null` = nothing happens).
import React from "react";

import { PALETTE, styles } from "./palette.mjs";

/** What the CLI has the user type for the unlisted-source line (floofy_core.consent.ACCEPT_PHRASE). */
export const ACCEPT_PHRASE = "I ACCEPT";
export const AGREE_LABEL = "[ I AGREE ]";

export function useAsker() {
  const [pending, setPending] = React.useState(null);
  const resolver = React.useRef(null);
  const ask = React.useCallback(
    (question, reply) =>
      new Promise((resolve) => {
        resolver.current = resolve;
        setPending({ question, reply });
      }),
    [],
  );
  const answer = React.useCallback((value) => {
    const resolve = resolver.current;
    resolver.current = null;
    setPending(null);
    if (resolve) resolve(value);
  }, []);
  return { ask, pending, answer };
}

function Modal({ title, tone, children, onClose, testid, kind }) {
  React.useEffect(() => {
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  return React.createElement(
    "div",
    { style: styles.overlay, "data-testid": "floofycrew-modal", "data-kind": kind, onClick: (event) => (event.target === event.currentTarget ? onClose() : undefined) },
    React.createElement(
      "div",
      { style: styles.modal, role: "dialog", "aria-modal": "true", "aria-label": title, "data-testid": testid },
      React.createElement("h2", { style: { ...styles.cardTitle, color: PALETTE[tone || "heading"], fontSize: "1.1rem" } }, title),
      children,
    ),
  );
}

/** The install disclosure (Requirement 11.7, 8.8, 11.6 c): what the CLI prints before its questions. */
export function Disclosure({ disclosure }) {
  if (!disclosure) return null;
  const network = disclosure.network || {};
  return React.createElement(
    "div",
    { style: { ...styles.card, marginBottom: "0.8rem" }, "data-testid": "floofycrew-disclosure" },
    React.createElement("div", null, React.createElement("strong", null, disclosure.name || disclosure.id), " ", React.createElement("code", null, `${disclosure.id} ${disclosure.version}`), " ", React.createElement("span", { style: { ...styles.badge, ...(disclosure.tier === "unlisted" ? styles.warn : {}) } }, `source tier: ${disclosure.tier || "unlisted"}`)),
    disclosure.unlistedSource
      ? React.createElement("div", { style: { ...styles.warn, marginTop: "0.3rem" }, "data-testid": "floofycrew-disclosure-unlisted" }, "UNLISTED SOURCE: no curator review, no compatibility data — ", React.createElement("code", null, disclosure.unlistedSource.ref || disclosure.unlistedSource.url), disclosure.unlistedSource.commit ? ` (commit ${String(disclosure.unlistedSource.commit).slice(0, 12)})` : "", "; nothing about this source is trusted beyond your consent, the mod's own files[] hashes were verified.")
      : null,
    React.createElement("div", { style: { marginTop: "0.4rem", fontWeight: 600 } }, "Parts and seams"),
    React.createElement(
      "ul",
      { style: styles.list },
      (disclosure.parts || []).map((part) => React.createElement("li", { key: part.index }, React.createElement("code", null, `${part.kind}/${part.side}`), ` ${part.path} → ${part.seam}`, part.modifiesPayload ? React.createElement("span", { style: { ...styles.badge, ...styles.warn, marginLeft: "0.3rem" } }, "MODIFIES PAYLOAD FILES") : null)),
    ),
    React.createElement("div", { style: { marginTop: "0.4rem", fontWeight: 600 } }, "Network"),
    React.createElement("div", { "data-testid": "floofycrew-disclosure-network" }, (network.hosts || []).length ? `remote hosts the mod contacts: ${network.hosts.join(", ")}` : "no remote hosts declared (only loopback is allowed without a declaration)", `; asks for credentials itself: ${network.credentials ? "yes" : "no"}`),
    (disclosure.flags || []).length
      ? React.createElement(React.Fragment, null, React.createElement("div", { style: { marginTop: "0.4rem", fontWeight: 600 } }, "Flags you are asked to accept"), React.createElement("ul", { style: { ...styles.list, ...styles.warn } }, disclosure.flags.map((flag, index) => React.createElement("li", { key: index }, `${flag.code}${flag.path ? ` [${flag.path}]` : ""}: ${flag.message}`))))
      : null,
    (disclosure.governanceTargets || []).length
      ? React.createElement("div", { style: { ...styles.danger, marginTop: "0.4rem" }, "data-testid": "floofycrew-disclosure-governance" }, `GOVERNANCE-ALTERING target(s): ${disclosure.governanceTargets.join(", ")} — each needs its path typed to confirm (Requirement 11.4)`)
      : null,
    (disclosure.hostWarnings || []).length ? React.createElement("ul", { style: { ...styles.list, ...styles.warn, marginTop: "0.4rem" } }, disclosure.hostWarnings.map((w, index) => React.createElement("li", { key: index }, `host says: ${w.code}: ${w.message}`))) : null,
    disclosure.codeParts ? React.createElement("div", { style: { ...styles.muted, marginTop: "0.4rem" } }, "Code parts (python-hook / spa) run once installed — confirming here installs the mod enabled; tick \u201cinstall switched off\u201d on the form to land it disabled.") : null,
  );
}

/** Staged for the next gateway start (default) or applied now — the explicit choice of Requirement 16.5. */
function StagingChoice({ now, setNow }) {
  return React.createElement(
    "div",
    { style: { margin: "0.6rem 0" }, "data-testid": "floofycrew-staging" },
    React.createElement("label", { style: { marginRight: "1rem" } }, React.createElement("input", { type: "radio", name: "floofy-staging", checked: !now, onChange: () => setNow(false), "data-testid": "floofycrew-staging-staged" }), " Stage for the next gateway start (default)"),
    React.createElement("label", null, React.createElement("input", { type: "radio", name: "floofy-staging", checked: now, onChange: () => setNow(true), "data-testid": "floofycrew-staging-now" }), " Apply now (the Loader reloads)"),
  );
}

function Buttons({ children }) {
  return React.createElement("div", { style: { display: "flex", gap: "0.5rem", justifyContent: "flex-end", marginTop: "0.8rem" } }, children);
}

function useAutofocus() {
  const ref = React.useRef(null);
  React.useEffect(() => {
    if (ref.current && typeof ref.current.focus === "function") ref.current.focus();
  }, []);
  return ref;
}

const isInstall = (reply) => Array.isArray(reply && reply.argv) && (reply.argv[0] === "install" || reply.argv[0] === "update" || reply.argv[0] === "profile");

export function ConsentModal({ question, onAnswer }) {
  const focus = useAutofocus();
  return React.createElement(
    Modal,
    { title: "READ THIS ONCE — FloofyCrew consent", tone: "danger", kind: "consent", testid: "floofycrew-consent-modal", onClose: () => onAnswer(null) },
    React.createElement("pre", { style: { ...styles.mono, maxHeight: "40vh", fontSize: "0.9rem", fontFamily: "inherit" }, "data-testid": "floofycrew-consent-text" }, question.text),
    React.createElement("div", { style: styles.muted }, "The acknowledgement is recorded once in consent.json (how: app) and referenced by every audit row. Enter agrees; Esc declines."),
    React.createElement(
      Buttons,
      null,
      React.createElement("button", { type: "button", style: styles.button, onClick: () => onAnswer(null), "data-testid": "floofycrew-consent-decline" }, "Decline"),
      React.createElement("button", { ref: focus, type: "button", style: { ...styles.button, ...styles.primary, fontFamily: "var(--mono, ui-monospace, monospace)" }, onClick: () => onAnswer({ consent: true }), "data-testid": "floofycrew-consent-agree" }, AGREE_LABEL),
    ),
  );
}

export function YesNoModal({ question, reply, onAnswer }) {
  const focus = useAutofocus();
  const initialNow = Array.isArray(reply.argv) && reply.argv.includes("--now");
  const [now, setNow] = React.useState(initialNow);
  const answer = { yes: [question.text] };
  if (isInstall(reply) && now !== initialNow) answer.$body = { now };
  return React.createElement(
    Modal,
    { title: "Confirm", tone: "warn", kind: "yes-no", testid: "floofycrew-yesno-modal", onClose: () => onAnswer(null) },
    React.createElement(Disclosure, { disclosure: reply.disclosure }),
    React.createElement("div", { style: { fontWeight: 600 }, "data-testid": "floofycrew-yesno-prompt" }, question.text),
    isInstall(reply) ? React.createElement(StagingChoice, { now, setNow }) : null,
    React.createElement(
      Buttons,
      null,
      React.createElement("button", { type: "button", style: styles.button, onClick: () => onAnswer(null), "data-testid": "floofycrew-yesno-no" }, "No"),
      React.createElement("button", { ref: focus, type: "button", style: { ...styles.button, ...styles.primary }, onClick: () => onAnswer(answer), "data-testid": "floofycrew-yesno-yes" }, "Yes"),
    ),
  );
}

export function GovernanceTargetModal({ question, reply, onAnswer }) {
  const targets = (reply.disclosure && reply.disclosure.governanceTargets && reply.disclosure.governanceTargets.length ? reply.disclosure.governanceTargets : question.targets) || [];
  const [typed, setTyped] = React.useState(() => Object.fromEntries(targets.map((t) => [t, ""])));
  const focus = useAutofocus();
  const complete = targets.length > 0 && targets.every((target) => typed[target] === target);
  return React.createElement(
    Modal,
    { title: "Governance-altering target: type the exact path", tone: "danger", kind: "governance-target", testid: "floofycrew-typed-modal", onClose: () => onAnswer(null) },
    React.createElement(Disclosure, { disclosure: reply.disclosure }),
    React.createElement("div", { style: styles.danger, "data-testid": "floofycrew-typed-prompt" }, question.text),
    question.detail === "mismatch" ? React.createElement("div", { style: styles.warn, "data-testid": "floofycrew-typed-mismatch" }, "The path you typed did not match the target byte for byte; nothing was changed. Type it exactly.") : null,
    React.createElement("div", { style: styles.muted }, "This confirmation is typed, never a button (Requirement 11.4); the Loader compares what you type with the target exactly."),
    targets.map((target, index) =>
      React.createElement(
        "div",
        { key: target, style: { margin: "0.5rem 0" } },
        React.createElement("label", { style: { display: "block" } }, "Target ", React.createElement("code", null, target)),
        React.createElement("input", { ref: index === 0 ? focus : undefined, style: { ...styles.input, width: "100%", boxSizing: "border-box", borderColor: typed[target] === target ? PALETTE.ok : typed[target] ? PALETTE.danger : undefined }, value: typed[target], onChange: (e) => setTyped({ ...typed, [target]: e.target.value }), placeholder: "type the exact path", autoComplete: "off", spellCheck: false, "data-testid": `floofycrew-typed-${index}`, "aria-label": `type ${target}` }),
      ),
    ),
    React.createElement(
      Buttons,
      null,
      React.createElement("button", { type: "button", style: styles.button, onClick: () => onAnswer(null), "data-testid": "floofycrew-typed-cancel" }, "Cancel"),
      React.createElement("button", { type: "button", style: { ...styles.button, ...styles.primary }, disabled: !complete, onClick: () => onAnswer({ governanceTargets: targets.map((t) => typed[t]) }), "data-testid": "floofycrew-typed-submit" }, "Confirm the typed path(s)"),
    ),
  );
}

export function UnlistedSourceModal({ question, reply, onAnswer }) {
  const focus = useAutofocus();
  return React.createElement(
    Modal,
    { title: "Unlisted source", tone: "warn", kind: "unlisted-source", testid: "floofycrew-unlisted-modal", onClose: () => onAnswer(null) },
    React.createElement(Disclosure, { disclosure: reply.disclosure }),
    React.createElement("div", { style: styles.warn, "data-testid": "floofycrew-unlisted-prompt" }, question.text),
    React.createElement("div", { style: styles.muted }, `The CLI has you type ${ACCEPT_PHRASE}; the accept item below supplies that phrase and is recorded as such. --yes never covers it.`),
    React.createElement(
      Buttons,
      null,
      React.createElement("button", { ref: focus, type: "button", style: styles.button, onClick: () => onAnswer(null), "data-testid": "floofycrew-unlisted-cancel" }, "Cancel"),
      React.createElement("button", { type: "button", style: { ...styles.button, ...styles.warn, borderColor: PALETTE.warn }, onClick: () => onAnswer({ unlistedSource: ACCEPT_PHRASE }), "data-testid": "floofycrew-unlisted-accept" }, `${ACCEPT_PHRASE} — install from this unlisted source anyway`),
    ),
  );
}

export function UnsignedIndexModal({ question, onAnswer }) {
  const focus = useAutofocus();
  return React.createElement(
    Modal,
    { title: "Trust loosening", tone: "danger", kind: "unsigned-index", testid: "floofycrew-unsigned-modal", onClose: () => onAnswer(null) },
    React.createElement("div", { style: styles.danger, "data-testid": "floofycrew-unsigned-text" }, question.text),
    React.createElement(
      Buttons,
      null,
      React.createElement("button", { ref: focus, type: "button", style: styles.button, onClick: () => onAnswer(null), "data-testid": "floofycrew-unsigned-keep" }, "Keep the signature requirement"),
      React.createElement("button", { type: "button", style: { ...styles.button, ...styles.danger, borderColor: PALETTE.danger }, onClick: () => onAnswer({ unsignedIndex: true }), "data-testid": "floofycrew-unsigned-accept" }, "Accept unsigned indexes from this source (recorded in the audit log)"),
    ),
  );
}

/** Renders the pending question of `useAsker()`; an unknown kind is refused (nothing is answered by default). */
export function ConfirmationHost({ pending, answer }) {
  if (!pending) return null;
  const { question, reply } = pending;
  const props = { question, reply: reply || {}, onAnswer: answer };
  switch (question.kind) {
    case "consent":
      return React.createElement(ConsentModal, props);
    case "yes-no":
      return React.createElement(YesNoModal, props);
    case "governance-target":
      return React.createElement(GovernanceTargetModal, props);
    case "unlisted-source":
      return React.createElement(UnlistedSourceModal, props);
    case "unsigned-index":
      return React.createElement(UnsignedIndexModal, props);
    default:
      return React.createElement(
        Modal,
        { title: `Unknown confirmation kind: ${question.kind}`, tone: "danger", kind: question.kind, testid: "floofycrew-unknown-modal", onClose: () => answer(null) },
        React.createElement("pre", { style: styles.mono }, question.text),
        React.createElement("div", { style: styles.muted }, "This App cannot answer it; use the floofy CLI."),
        React.createElement(Buttons, null, React.createElement("button", { type: "button", style: styles.button, onClick: () => answer(null) }, "Close")),
      );
  }
}
