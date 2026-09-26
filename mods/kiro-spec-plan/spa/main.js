// Kiro Spec & Plan — the dashboard half.
//
// Marks the four mode agents wherever the host lists agents (the agent selector
// popover: role="listbox" rows whose first monospace span is the agent name)
// so they read as one category — "Kiro mode" — rather than four odd agent
// names. Purely cosmetic: the agents work (or don't) regardless; when the
// gateway half reports the KAS backend is off, the badge says so instead, so the
// picker itself tells the user what is missing.

const MODES = new Set(["spec", "quick-spec", "bug-fix", "plan"]);
const MARK = "data-kiro-spec-plan";
const STYLE_ID = "kiro-spec-plan-style";
const GHOST = '<svg viewBox="0 0 24 24" width="12" height="12" aria-hidden="true"><path fill="currentColor" fill-rule="evenodd" d="M12 2a8 8 0 0 0-8 8v11.2c0 .6.7 1 1.2.6l2.3-1.8 2.3 1.8c.4.3.9.3 1.2 0L12 20l1 1.8c.3.3.8.3 1.2 0l2.3-1.8 2.3 1.8c.5.4 1.2 0 1.2-.6V10a8 8 0 0 0-8-8Zm-3 7.5a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3Zm6 0a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3Z"/></svg>';

let observer = null;
let facts = { kas: true };

function ensureStyle() {
  if (document.getElementById(STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = STYLE_ID;
  style.textContent = [
    "[" + MARK + '="badge"]{display:inline-flex;align-items:center;gap:3px;padding:0 6px;border-radius:999px;font:600 10px/16px system-ui;letter-spacing:.02em;flex:none;',
    "background:var(--accent-subtle,rgba(124,58,237,.12));color:var(--accent,#7c3aed);border:1px solid color-mix(in srgb,var(--accent,#7c3aed) 30%,transparent)}",
    "[" + MARK + '="badge"][data-off]{background:var(--warning-subtle,rgba(234,179,8,.15));color:var(--warning,#b45309);border-color:color-mix(in srgb,var(--warning,#b45309) 35%,transparent)}',
  ].join("");
  document.head.appendChild(style);
}

function badgeFor() {
  const b = document.createElement("span");
  b.setAttribute(MARK, "badge");
  b.innerHTML = GHOST + "<span>" + (facts.kas ? "Kiro mode" : "needs KAS") + "</span>";
  b.title = facts.kas
    ? "A built-in Kiro mode, run by the kiro-cli v3 engine (Kiro Spec & Plan)"
    : "Kiro Spec & Plan: KiroCrew is not on the KAS backend — run " + (facts.hint || "kirocrew config set agent.acp_backend kas");
  if (!facts.kas) b.setAttribute("data-off", "1");
  return b;
}

/** Badge every agent row naming one of the modes; idempotent, cheap enough to run on each mutation. */
export function decorate(root = document) {
  let n = 0;
  for (const row of root.querySelectorAll('[role="option"]')) {
    const name = row.querySelector("span.font-mono");
    if (!name || !MODES.has(name.textContent.trim())) continue;
    const line = name.parentElement;
    if (!line || line.querySelector("[" + MARK + '="badge"]')) continue;
    line.appendChild(badgeFor());
    n += 1;
  }
  return n;
}

export function activate(floofy) {
  ensureStyle();
  try {
    const read = floofy.config && floofy.config.read ? floofy.config.read() : null;
    if (read && typeof read.then === "function") read.then((f) => { if (f && typeof f.kas === "boolean") facts = f; }).catch(() => undefined);
    else if (read && typeof read.kas === "boolean") facts = read;
  } catch { /* exports unavailable: assume live */ }
  decorate();
  observer = new MutationObserver(() => decorate());
  observer.observe(document.body, { childList: true, subtree: true });
}

export function deactivate() {
  if (observer) observer.disconnect();
  observer = null;
  document.querySelectorAll("[" + MARK + "]").forEach((el) => el.remove());
  document.getElementById(STYLE_ID)?.remove();
}
