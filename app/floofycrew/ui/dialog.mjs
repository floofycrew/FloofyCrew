// FloofyCrew SPA host — `floofy.mod(id).dialog`, in-document dialogs for a mod's page (Requirement 16.4).
//
// The dashboard runs inside the KiroCrew desktop shell as well as in a browser,
// and the shell has no `window.alert` / `confirm` / `prompt`: a mod page that
// relies on them silently does nothing there. These dialogs are rendered into
// the document instead — the same overlay the FloofyCrew App uses for its own
// confirmations (`loader-app/ui/app/confirm.mjs`: `data-testid="floofycrew-modal"`,
// the accent border, the host's colour tokens) so a mod's question looks like
// the App's — with keyboard handling (Enter answers, Escape cancels, focus goes
// to the first field or the primary button) and one dialog at a time (a second
// call waits for the first to close).
//
// Framework-free: plain DOM, inline styles, no stylesheet, no third-party
// origin (Requirement 4.6). Injectable `document` keeps it unit-testable.
"use strict";

const ACCENT = "#ff6b8b";
const DANGER = "#ff5f73";
const BORDER_STRONG = "1px solid var(--border-strong, rgba(127,127,127,.6))";

const STYLE = Object.freeze({
  overlay: { position: "fixed", inset: "0", background: "rgba(0,0,0,.55)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: "1000", padding: "1rem" },
  modal: { background: "var(--bg, #1a1a1f)", color: "var(--text, #eee)", border: `1px solid ${ACCENT}`, borderRadius: "10px", padding: "1.1rem 1.3rem", maxWidth: "34rem", width: "100%", maxHeight: "90vh", overflow: "auto", boxShadow: "0 12px 40px rgba(0,0,0,.5)", fontSize: "0.9rem" },
  title: { margin: "0 0 0.5rem", fontSize: "1.1rem", fontWeight: "600", color: "#ff9a3d" },
  text: { whiteSpace: "pre-wrap", margin: "0 0 0.6rem", lineHeight: "1.45" },
  field: { display: "flex", flexDirection: "column", gap: "0.2rem", margin: "0.5rem 0" },
  label: { fontSize: "0.8rem", opacity: "0.8" },
  input: { padding: "0.3rem 0.5rem", borderRadius: "6px", border: BORDER_STRONG, background: "var(--bg-elevated, transparent)", color: "inherit", fontSize: "0.85rem", fontFamily: "inherit", width: "100%", boxSizing: "border-box" },
  error: { color: DANGER, fontSize: "0.78rem", minHeight: "1em" },
  buttons: { display: "flex", gap: "0.5rem", justifyContent: "flex-end", marginTop: "0.8rem" },
  button: { padding: "0.25rem 0.6rem", borderRadius: "6px", border: BORDER_STRONG, background: "var(--bg-elevated, transparent)", color: "inherit", cursor: "pointer", fontSize: "0.8rem", fontFamily: "inherit" },
  primary: { borderColor: ACCENT, color: ACCENT, fontWeight: "600" },
  danger: { borderColor: DANGER, color: DANGER, fontWeight: "600" },
});

function element(doc, tag, style, attrs = {}) {
  const node = doc.createElement(tag);
  if (style) Object.assign(node.style, style);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "text") node.textContent = String(value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  return node;
}

/** Normalise a field spec: `{name, label, value, placeholder, validate}`; `validate(value, values)` returns an error string or a falsy value. */
function normaliseField(field, index) {
  if (typeof field === "string") field = { name: field };
  if (!field || typeof field !== "object" || typeof field.name !== "string" || !field.name) throw new TypeError(`dialog.form: fields[${index}] needs a name`);
  return { name: field.name, label: field.label ?? field.name, value: field.value == null ? "" : String(field.value), placeholder: field.placeholder == null ? "" : String(field.placeholder), validate: typeof field.validate === "function" ? field.validate : null, transform: typeof field.transform === "function" ? field.transform : null };
}

/**
 * Build the dialogs of one mod.
 * @param {string} modId
 * @param {{document?: Document}} [env]
 */
export function createDialogs(modId, env = {}) {
  const doc = env.document ?? (typeof document !== "undefined" ? document : null);
  let queue = Promise.resolve();

  /** Run `open` after the previous dialog closed; never rejects the chain. */
  function serialise(open) {
    const next = queue.then(open, open);
    queue = next.then(
      () => undefined,
      () => undefined,
    );
    return next;
  }

  /**
   * The general dialog: a title, a text, zero or more text fields and two buttons.
   * Resolves with `{ [field.name]: value }` (an empty object for a fieldless dialog) or `null` when cancelled.
   */
  function form(options = {}) {
    if (!doc || !doc.body) return Promise.reject(new Error(`[floofy:${modId}] dialog: no document to render into`));
    let fields;
    try {
      fields = (Array.isArray(options.fields) ? options.fields : []).map(normaliseField);
    } catch (error) {
      return Promise.reject(error);
    }
    const kind = String(options.kind || (fields.length ? "prompt" : "confirm"));
    return serialise(
      () =>
        new Promise((resolve) => {
          const overlay = element(doc, "div", STYLE.overlay, { "data-testid": "floofycrew-modal", "data-kind": `mod-${kind}`, "data-mod": modId });
          const modal = element(doc, "div", STYLE.modal, { role: "dialog", "aria-modal": "true", "aria-label": options.title || modId, "data-testid": "floofycrew-mod-dialog" });
          overlay.append(modal);
          if (options.title) modal.append(element(doc, "h2", STYLE.title, { text: options.title }));
          if (options.text) modal.append(element(doc, "p", STYLE.text, { text: options.text, "data-testid": "floofycrew-mod-dialog-text" }));

          const inputs = new Map();
          const errors = new Map();
          for (const field of fields) {
            const wrapper = element(doc, "label", STYLE.field);
            wrapper.append(element(doc, "span", STYLE.label, { text: field.label }));
            const input = element(doc, "input", STYLE.input, { type: "text", autocomplete: "off", spellcheck: "false", placeholder: field.placeholder || null, "data-testid": `floofycrew-mod-dialog-field-${field.name}`, "aria-label": field.label });
            input.value = field.value;
            const error = element(doc, "span", STYLE.error, { "data-testid": `floofycrew-mod-dialog-error-${field.name}` });
            wrapper.append(input, error);
            modal.append(wrapper);
            inputs.set(field.name, input);
            errors.set(field.name, error);
          }

          const buttons = element(doc, "div", STYLE.buttons);
          const cancel = element(doc, "button", STYLE.button, { type: "button", text: options.cancelLabel || "Cancel", "data-testid": "floofycrew-mod-dialog-cancel" });
          const ok = element(doc, "button", { ...STYLE.button, ...(options.danger ? STYLE.danger : STYLE.primary) }, { type: "button", text: options.okLabel || "OK", "data-testid": "floofycrew-mod-dialog-ok" });
          if (options.cancelLabel !== false) buttons.append(cancel);
          buttons.append(ok);
          modal.append(buttons);

          const values = () => Object.fromEntries(fields.map((field) => [field.name, inputs.get(field.name).value]));
          // an error is shown once the user touched that field (or tried to submit); the button is disabled from the start
          const touched = new Set();
          const validate = () => {
            const current = values();
            let valid = true;
            for (const field of fields) {
              const message = field.validate ? field.validate(current[field.name], current) : null;
              errors.get(field.name).textContent = message && touched.has(field.name) ? String(message) : "";
              if (message) valid = false;
            }
            ok.disabled = !valid;
            ok.style.opacity = valid ? "" : "0.5";
            ok.style.cursor = valid ? "pointer" : "default";
            return valid;
          };
          for (const [name, input] of inputs) {
            const field = fields.find((f) => f.name === name);
            input.addEventListener("input", () => {
              touched.add(name);
              if (field.transform) {
                const changed = field.transform(input.value, values());
                if (typeof changed === "object" && changed) for (const [other, value] of Object.entries(changed)) if (inputs.has(other) && other !== name) inputs.get(other).value = String(value);
              }
              validate();
            });
          }

          let done = false;
          const finish = (result) => {
            if (done) return;
            done = true;
            doc.removeEventListener("keydown", onKey, true);
            overlay.remove();
            resolve(result);
          };
          const submit = () => {
            for (const field of fields) touched.add(field.name);
            if (!validate()) return;
            finish(values());
          };
          const onKey = (event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              finish(null);
            } else if (event.key === "Enter" && (event.target === ok || inputs.size === 0 || Array.from(inputs.values()).includes(event.target))) {
              event.preventDefault();
              event.stopPropagation();
              submit();
            }
          };
          cancel.addEventListener("click", () => finish(null));
          ok.addEventListener("click", submit);
          overlay.addEventListener("click", (event) => {
            if (event.target === overlay) finish(null);
          });
          doc.addEventListener("keydown", onKey, true);
          doc.body.append(overlay);
          validate();
          const first = inputs.size ? inputs.values().next().value : ok;
          if (first && typeof first.focus === "function") first.focus();
          if (first && first !== ok && typeof first.select === "function") first.select();
        }),
    );
  }

  return Object.freeze({
    form,
    /** A yes/no question; resolves `true` when confirmed. */
    async confirm(text, options = {}) {
      return (await form({ ...options, kind: "confirm", text, okLabel: options.okLabel || "Yes", cancelLabel: options.cancelLabel || "No", fields: [] })) !== null;
    },
    /** One text field; resolves with the string, or `null` when cancelled. */
    async prompt(text, options = {}) {
      const result = await form({ ...options, kind: "prompt", text, fields: [{ name: "value", label: options.label || "", value: options.value, placeholder: options.placeholder, validate: options.validate }] });
      return result === null ? null : result.value;
    },
    /** A notice with a single button. */
    async alert(text, options = {}) {
      await form({ ...options, kind: "alert", text, okLabel: options.okLabel || "OK", cancelLabel: false, fields: [] });
    },
  });
}
