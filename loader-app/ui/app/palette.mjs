// FloofyCrew manager App — the palette and the shared styles (Requirement 15.1 mirrored; task 11.2).
//
// PALETTE mirrors the 24-bit column of `floofy_core.cli.style.PALETTE` (the
// sunset colours of the branding), tone for tone, so the App and the terminal
// say the same thing in the same colour; `loader-app/tests/test_ui_shell.py`
// pins the two tables to each other. `WEIGHTS` mirrors `floofy_core.cli.style.WEIGHTS`.
// Everything here is plain objects for React's inline `style` — no stylesheet,
// no bundler, no third-party origin (Requirement 4.6).
"use strict";

/** tone -> hex colour, from the 24-bit SGR parameters `38;2;r;g;b` of the CLI palette. */
export const PALETTE = Object.freeze({
  heading: "#ff9a3d",
  ok: "#50c878",
  warn: "#ffb347",
  danger: "#ff5f73",
  muted: "#a0a0aa",
  accent: "#ff6b8b",
});

/** The tones the CLI paints bold (their 24-bit SGR starts with `1;`). */
export const BOLD_TONES = Object.freeze(["heading", "danger"]);

export const WEIGHTS = Object.freeze({ bold: { fontWeight: 700 }, dim: { opacity: 0.7 }, italic: { fontStyle: "italic" } });

/** A React `style` for one tone (colour + the CLI's weight). */
export function tone(name) {
  const colour = PALETTE[name];
  if (!colour) return {};
  return BOLD_TONES.includes(name) ? { color: colour, fontWeight: 700 } : { color: colour };
}

export const UNOFFICIAL = "FloofyCrew is unofficial and not affiliated with Kiro or KiroCrew.";

const border = "1px solid var(--border, rgba(127,127,127,.35))";
const borderStrong = "1px solid var(--border-strong, rgba(127,127,127,.6))";

/** The one corner radius of the App: squared. Cards, banners, buttons, inputs, badges, the modal — nothing rounder than this. */
export const RADIUS = "2px";

export const styles = Object.freeze({
  // the bottom padding leaves room under the footer so the page never ends flush against the viewport edge
  page: { padding: "1.25rem 1.25rem 6rem", display: "flex", flexDirection: "column", gap: "1.25rem", fontSize: "0.9rem", color: "var(--text, inherit)", minHeight: "100%" },
  // the footer: a rule, then the unofficial line and the author, small and muted; the extra top margin sets it apart from the last card
  footer: { borderTop: border, marginTop: "1.5rem", paddingTop: "0.9rem", display: "flex", flexWrap: "wrap", gap: "0.3rem 1.2rem", fontSize: "0.8rem", opacity: 0.75 },
  // the banner's "don't show this again": a plain text button on the banner's right edge
  bannerDismiss: { marginLeft: "auto", padding: "0.1rem 0.45rem", border: "none", background: "transparent", color: "inherit", cursor: "pointer", fontSize: "0.78rem", fontWeight: 500, textDecoration: "underline", opacity: 0.85, whiteSpace: "nowrap" },
  // a page is a column of cards with the same gap as the shell around it
  pageBody: { display: "flex", flexDirection: "column", gap: "1.25rem" },
  header: { display: "flex", alignItems: "center", gap: "0.9rem", flexWrap: "wrap" },
  headerActions: { marginLeft: "auto", display: "flex", alignItems: "center", gap: "0.5rem" },
  title: { fontSize: "1.25rem", fontWeight: 700, color: PALETTE.heading, margin: 0 },
  banner: { border: `1px solid ${PALETTE.warn}`, background: "rgba(255,179,71,.12)", padding: "0.6rem 0.9rem", borderRadius: RADIUS, fontWeight: 600, display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" },
  nav: { display: "flex", gap: "0.35rem", flexWrap: "wrap", borderBottom: border, paddingBottom: "0.5rem" },
  // The tab border is spelled as longhands, and the active variant sets exactly the same keys: React diffs inline
  // styles key by key, so a tab that goes from active back to inactive keeps every declaration. With a `border`
  // shorthand here and `borderColor` only on the active variant, leaving the active state unset `borderColor`,
  // which fell back to currentColor — the grey frame that appeared on a tab after it had been clicked once.
  navItem: { padding: "0.35rem 0.8rem", borderRadius: `${RADIUS} ${RADIUS} 0 0`, borderWidth: "1px", borderStyle: "solid", borderColor: "transparent", borderBottomColor: "transparent", background: "transparent", color: "inherit", cursor: "pointer", fontSize: "0.9rem", fontWeight: 400, outlineOffset: "-2px" },
  navItemActive: { borderColor: PALETTE.accent, borderBottomColor: "transparent", color: PALETTE.accent, fontWeight: 600 },
  card: { border, borderRadius: RADIUS, padding: "0.9rem 1.1rem", background: "var(--card, transparent)" },
  cardIntro: { opacity: 0.75, margin: "-0.2rem 0 0.7rem", lineHeight: 1.45 },
  // a plain table for lists whose entries carry several short fields (the registry sources)
  table: { width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" },
  th: { textAlign: "left", fontWeight: 600, opacity: 0.8, padding: "0.35rem 0.6rem", borderBottom: borderStrong, whiteSpace: "nowrap" },
  td: { padding: "0.55rem 0.6rem", borderBottom: border, verticalAlign: "top" },
  cardTitle: { margin: "0 0 0.5rem", fontSize: "1rem", fontWeight: 600 },
  row: { borderTop: border, padding: "0.6rem 0", display: "grid", gridTemplateColumns: "minmax(12rem, 1.4fr) minmax(8rem, 1fr) minmax(8rem, 1fr) minmax(14rem, 2fr) minmax(12rem, 1.2fr)", gap: "0.75rem", alignItems: "start" },
  head: { fontWeight: 600, opacity: 0.8, borderTop: "none" },
  badge: { display: "inline-block", padding: "0.1rem 0.45rem", borderRadius: RADIUS, border: borderStrong, fontSize: "0.75rem", marginRight: "0.3rem", whiteSpace: "nowrap" },
  button: { padding: "0.25rem 0.6rem", borderRadius: RADIUS, border: borderStrong, background: "var(--bg-elevated, transparent)", color: "inherit", cursor: "pointer", marginRight: "0.35rem", marginBottom: "0.3rem", fontSize: "0.8rem" },
  primary: { borderColor: PALETTE.accent, color: PALETTE.accent, fontWeight: 600 },
  // the filled red button: the gateway restart — the one action on this surface that interrupts the dashboard itself
  restart: { background: PALETTE.danger, borderColor: PALETTE.danger, color: "#fff", fontWeight: 700 },
  input: { padding: "0.3rem 0.5rem", borderRadius: RADIUS, border: borderStrong, background: "var(--bg-elevated, transparent)", color: "inherit", fontSize: "0.85rem", minWidth: "16rem", fontFamily: "inherit" },
  muted: { opacity: 0.75 },
  mono: { fontFamily: "var(--mono, ui-monospace, monospace)", fontSize: "0.78rem", whiteSpace: "pre-wrap", maxHeight: "18rem", overflow: "auto", margin: 0 },
  warn: tone("warn"),
  danger: tone("danger"),
  ok: tone("ok"),
  accent: tone("accent"),
  list: { margin: 0, paddingLeft: "1rem" },
  kv: { display: "grid", gridTemplateColumns: "max-content 1fr", gap: "0.2rem 0.9rem", alignItems: "baseline" },
  command: { fontFamily: "var(--mono, ui-monospace, monospace)", fontSize: "0.8rem", background: "rgba(127,127,127,.15)", padding: "0.05rem 0.35rem", borderRadius: RADIUS },
  overlay: { position: "fixed", inset: 0, background: "rgba(0,0,0,.55)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000, padding: "1rem" },
  modal: { background: "var(--bg, #1a1a1f)", color: "var(--text, #eee)", border: `1px solid ${PALETTE.accent}`, borderRadius: RADIUS, padding: "1.1rem 1.3rem", maxWidth: "44rem", width: "100%", maxHeight: "90vh", overflow: "auto", boxShadow: "0 12px 40px rgba(0,0,0,.5)" },
});

/** The equivalent terminal command for a capability the App lacks (Requirement 16.2: shown, never hidden). */
export function commandNote(React, text, command) {
  return React.createElement("div", { style: { ...styles.muted, marginTop: "0.3rem" } }, text, " ", React.createElement("code", { style: styles.command }, command));
}
