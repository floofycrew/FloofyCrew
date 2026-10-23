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

const borderColor = "var(--border, rgba(127,127,127,.35))";
const border = `1px solid ${borderColor}`;
const borderStrong = "1px solid var(--border-strong, rgba(127,127,127,.6))";

/** The one corner radius of the App: squared. Cards, banners, buttons, inputs, badges, the modal — nothing rounder than this. */
export const RADIUS = "2px";

/** The one exception: the on/off switch is a pill, because it copies the shape of KiroCrew's own switches (half its 22px height). */
export const SWITCH_RADIUS = "11px";

/** The page's outer padding and the gap between its blocks — the masthead glow starts a padding above the header (see `mastheadGlow`). */
const PAGE_GAP = "1.25rem";

/**
 * The glow's vertical feather: transparent at the window edge, solid by the time it reaches the wordmark, solid down to
 * the top of the tab row, then fading to nothing across the tab row so it is gone by the tabs' rule. `fadeFrom` is the
 * tab row's top in px from the glow's top (measured; `null` before the first measurement falls back to the last 2.5rem).
 */
export function mastheadFeather(fadeFrom) {
  const from = typeof fadeFrom === "number" ? `${fadeFrom}px` : "calc(100% - 2.5rem)";
  return `linear-gradient(180deg, rgba(0,0,0,0) 0, #000 2.5rem, #000 ${from}, rgba(0,0,0,0) 100%)`;
}

export const styles = Object.freeze({
  // the bottom padding leaves room under the footer so the page never ends flush against the viewport edge
  page: { padding: `${PAGE_GAP} ${PAGE_GAP} 6rem`, display: "flex", flexDirection: "column", gap: PAGE_GAP, fontSize: "0.9rem", color: "var(--text, inherit)", minHeight: "100%" },
  // the footer: a rule, then the unofficial line and the author, small and muted; the extra top margin sets it apart from the last card
  footer: { borderTop: border, marginTop: "1.5rem", paddingTop: "0.9rem", display: "flex", flexWrap: "wrap", gap: "0.3rem 1.2rem", fontSize: "0.8rem", opacity: 0.75 },
  // the banner's "don't show this again": a plain text button on the banner's right edge
  bannerDismiss: { marginLeft: "auto", padding: "0.1rem 0.45rem", border: "none", background: "transparent", color: "inherit", cursor: "pointer", fontSize: "0.78rem", fontWeight: 500, textDecoration: "underline", opacity: 0.85, whiteSpace: "nowrap" },
  // a page is a column of cards with the same gap as the shell around it
  pageBody: { display: "flex", flexDirection: "column", gap: "1.25rem" },
  // the masthead groups the header, the banner and the tabs (same gap as the page, so the layout is unchanged) and owns
  // the sunset glow: the brand's pink → orange at low alpha (host text stays legible) on a backdrop layer behind all
  // three, fading out towards the right. Vertically it starts a page padding above the header (the window edge) and
  // runs down to the tabs' bottom rule, covering the tab row; the selected tab is cut out of it (see `mastheadGlowMask`
  // in index.mjs), so it shows the plain page and reads as joined to the content below. No border, no box.
  masthead: { position: "relative", isolation: "isolate", display: "flex", flexDirection: "column", gap: PAGE_GAP },
  mastheadGlow: { position: "absolute", top: `-${PAGE_GAP}`, bottom: 0, left: 0, right: 0, zIndex: -1, pointerEvents: "none", background: "linear-gradient(90deg, rgba(255,107,139,.22) 0%, rgba(255,154,61,.18) 55%, rgba(255,154,61,0) 100%)", WebkitMaskRepeat: "no-repeat", maskRepeat: "no-repeat", WebkitMaskComposite: "source-out", maskComposite: "subtract" },
  header: { display: "flex", alignItems: "center", gap: "0.9rem", flexWrap: "wrap", padding: "0.9rem 1rem" },
  // the wordmark (mark + "FloofyCrew" text, the peach-on-dark lockup) replaces the plain title
  wordmark: { height: "2.4rem", width: "auto", display: "block" },
  // visually hidden but read by screen readers (and counted by innerText): the page still has an h1 "FloofyCrew"
  srOnly: { position: "absolute", width: "1px", height: "1px", padding: 0, margin: "-1px", overflow: "hidden", clip: "rect(0 0 0 0)", whiteSpace: "nowrap", border: 0 },
  // the "Mod pages" row: [icon + mod name] | mod id — compact, one line; the NAME is the link that opens the page
  // (Requirement 16.4). No separate open control: the name reads as a link (accent, underlined) and is the click target.
  pageRow: { borderTop: border, padding: "0.35rem 0", display: "grid", gridTemplateColumns: "minmax(12rem, 1.4fr) minmax(10rem, 1fr)", gap: "0.75rem", alignItems: "center", minWidth: 0 },
  pageCell: { display: "flex", alignItems: "center", flexWrap: "wrap", gap: "0.3rem 0.6rem", minWidth: 0 },
  pageLink: { display: "inline-flex", alignItems: "center", gap: "0.6rem", color: PALETTE.accent, textDecoration: "none", cursor: "pointer", minWidth: 0 },
  pageLinkName: { textDecoration: "underline", textDecorationColor: "rgba(255,107,139,.45)", textUnderlineOffset: "3px", overflowWrap: "anywhere" },
  // an inactive mod's page is not served: the same cell, dimmed, no link, the reason beside it
  pageInactive: { display: "inline-flex", alignItems: "center", gap: "0.6rem", opacity: 0.6, minWidth: 0, cursor: "not-allowed" },
  modIcon: { width: "1.75rem", height: "1.75rem", borderRadius: RADIUS, objectFit: "contain", display: "block", background: "rgba(127,127,127,.12)" },
  modIconFallback: { width: "1.75rem", height: "1.75rem", borderRadius: RADIUS, display: "grid", placeItems: "center", background: "rgba(127,127,127,.12)", fontSize: "1rem", lineHeight: 1 },
  modId: { fontFamily: "var(--mono, ui-monospace, monospace)", fontSize: "0.78rem", opacity: 0.7 },
  // a button's content while its act runs: the spinner and the busy label side by side
  busyLabel: { display: "inline-flex", alignItems: "center", gap: "0.35rem" },
  // the activity line: one fixed box in the corner of the viewport, over the page, under the modal overlay (zIndex 1000)
  activity: { position: "fixed", right: "1.25rem", bottom: "1.25rem", zIndex: 900, display: "flex", alignItems: "center", gap: "0.5rem", padding: "0.5rem 0.8rem", border: `1px solid ${PALETTE.accent}`, borderRadius: RADIUS, background: "var(--bg, #1a1a1f)", color: "var(--text, #eee)", boxShadow: "0 6px 24px rgba(0,0,0,.35)", fontSize: "0.85rem", maxWidth: "30rem" },
  // the "Installed mods" table: switch | name | id | compat · tier | actions | parts (Requirement 16.1)
  // every column may shrink to its minimum and its text wraps; nothing in a row is allowed to overflow the card
  modRow: { borderTop: border, padding: "0.5rem 0", display: "grid", gridTemplateColumns: "2.6rem minmax(8rem, 1.2fr) minmax(8rem, 1fr) minmax(9rem, 1fr) minmax(11rem, 1.3fr) minmax(8rem, 1.1fr)", gap: "0.75rem", alignItems: "center", minWidth: 0 },
  // the on/off switch (the shape KiroCrew's settings use): a pill track with a sliding white knob; the one rounded thing in the App, on purpose
  switchTrack: { position: "relative", display: "inline-flex", alignItems: "center", width: "38px", height: "22px", flexShrink: 0, borderRadius: SWITCH_RADIUS, border: "none", padding: 0, transition: "background .15s" },
  switchOn: { background: PALETTE.accent },
  switchOff: { background: "var(--border-strong, rgba(127,127,127,.6))" },
  switchKnob: { position: "absolute", top: "2px", width: "18px", height: "18px", borderRadius: SWITCH_RADIUS, background: "#fff", boxShadow: "0 1px 2px rgba(0,0,0,.35)", transition: "left .15s" },
  headerActions: { marginLeft: "auto", display: "flex", alignItems: "center", gap: "0.5rem" },
  title: { fontSize: "1.25rem", fontWeight: 700, color: PALETTE.heading, margin: 0 },
  banner: { border: `1px solid ${PALETTE.warn}`, background: "rgba(255,179,71,.12)", padding: "0.6rem 0.9rem", borderRadius: RADIUS, fontWeight: 600, display: "flex", alignItems: "center", gap: "0.6rem", flexWrap: "wrap" },
  // The tab row's rule is NOT one border on the nav: it is each tab's bottom border plus a trailing spacer that carries
  // the rest of the line. That way the selected tab, whose bottom is transparent, opens onto the page — the rule stops
  // at its left edge and resumes at its right — instead of running underneath it (which left the frame looking
  // unfinished: side borders ending above a line that continued beneath). No gap between tabs, or the rule would break;
  // the tabs' own padding spaces them. No padding under the tabs: they sit on the rule, so the tab box is the cut-out.
  nav: { display: "flex", alignItems: "flex-end", flexWrap: "wrap" },
  navSpacer: { flex: "1 1 0", alignSelf: "stretch", borderBottom: border, minWidth: "0.5rem" },
  // Tab borders are spelled PER SIDE, never with the 4-side `borderColor` key. React diffs inline styles key by
  // key and writes only what changed: on inactive→active, a 4-side `borderColor` write would repaint ALL FOUR sides —
  // including the bottom — while `borderBottomColor` is NOT re-written when it compares equal, so the css
  // `border-color` shorthand's bottom value wins and the active tab grows a bottom border after any tab switch (the
  // first render was fine, which is why it looked intermittent). Per-side keys diff independently, so the active tab's
  // bottom stays transparent through every state change while the inactive tabs' bottoms carry the rule.
  navItem: { padding: "0.2rem 0.75rem 0.25rem", borderRadius: `${RADIUS} ${RADIUS} 0 0`, borderWidth: "1px", borderStyle: "solid", borderTopColor: "transparent", borderRightColor: "transparent", borderLeftColor: "transparent", borderBottomColor: borderColor, background: "transparent", color: "inherit", cursor: "pointer", fontSize: "0.9rem", lineHeight: 1.4, fontWeight: 400, outlineOffset: "-2px" },
  navItemActive: { borderTopColor: PALETTE.accent, borderRightColor: PALETTE.accent, borderLeftColor: PALETTE.accent, borderBottomColor: "transparent", color: PALETTE.accent, fontWeight: 600 },
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
