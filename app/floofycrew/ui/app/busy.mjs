// FloofyCrew manager App — "please wait" made visible (Requirement 16.2).
//
// Every typed action (install, update, enable, uninstall, refresh…) goes through
// `manager.act()`, and most of them take seconds: the Loader runs the floofy
// CLI, which clones a repository or reads a registry before it can even ASK the
// confirmation question, then does the real work after the answer. The manager
// records the act in flight as `activity` (phases in `activity.mjs`), and two
// things show it: the button that started the act (`BusyButton`: a spinner and
// a busy label, disabled meanwhile) and `ActivityLine`, one fixed line in the
// corner that says in words what is being waited for — visible wherever the
// page is scrolled to. Both read the same `activity`; nothing is guessed.
import React from "react";

import { describeActivity } from "./activity.mjs";
import { styles } from "./palette.mjs";

export { describeActivity, isRunning, wordsFor } from "./activity.mjs";

const reducedMotion = (() => {
  try {
    return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
})();

/**
 * The spinner: an SVG ring with a rotating arc, animated by SMIL (no stylesheet, no keyframes to inject into the host
 * page). Inherits `currentColor`. With `prefers-reduced-motion: reduce` the arc stands still — still a ring, no motion.
 */
export function Spinner({ size = 14, label }) {
  return React.createElement(
    "svg",
    { width: size, height: size, viewBox: "0 0 24 24", role: label ? "img" : undefined, "aria-label": label, "aria-hidden": label ? undefined : "true", style: { display: "inline-block", verticalAlign: "-0.15em", flex: "none" }, "data-testid": "floofycrew-spinner" },
    React.createElement("circle", { cx: 12, cy: 12, r: 9, fill: "none", stroke: "currentColor", strokeWidth: 3, strokeOpacity: 0.25 }),
    React.createElement(
      "path",
      { d: "M21 12a9 9 0 0 0-9-9", fill: "none", stroke: "currentColor", strokeWidth: 3, strokeLinecap: "round" },
      reducedMotion ? null : React.createElement("animateTransform", { attributeName: "transform", type: "rotate", from: "0 12 12", to: "360 12 12", dur: "0.9s", repeatCount: "indefinite" }),
    ),
  );
}

/**
 * A button for one act: `label` at rest; while ITS act runs (`running`), a spinner and `busyLabel`, disabled. Other
 * buttons are disabled by `manager.busy` as before — only the one that was clicked spins.
 */
export function BusyButton({ running, label, busyLabel, style, disabled, testid, title, onClick, type = "button" }) {
  return React.createElement(
    "button",
    { type, style: style || styles.button, disabled: Boolean(disabled) || Boolean(running), title, "aria-busy": running ? "true" : undefined, "data-testid": testid, onClick },
    running ? React.createElement("span", { style: styles.busyLabel }, React.createElement(Spinner), busyLabel || label) : label,
  );
}

/** The one fixed line in the corner: a spinner and what is being waited for. Hidden while a question is on screen. */
export function ActivityLine({ activity }) {
  const text = describeActivity(activity);
  if (!text) return null;
  return React.createElement("div", { style: styles.activity, role: "status", "aria-live": "polite", "data-testid": "floofycrew-activity", "data-phase": activity.phase }, React.createElement(Spinner, { size: 16 }), React.createElement("span", null, text));
}
