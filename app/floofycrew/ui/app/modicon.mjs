// FloofyCrew manager App — a mod's face, shared by the Mod pages list and the Installed mods table.
//
// Preference order: an explicit `src` (a ui part's own icon, served by the Loader
// while the mod is active), else the manifest `icon` through the Loader's
// `GET /mods/{id}/icon` (any installed mod, active or not), else a puzzle glyph.
// A failed load falls back to the glyph rather than a broken-image box.
import React from "react";

import { API } from "./api.mjs";
import { styles } from "./palette.mjs";

export function ModIcon({ id, hasIcon, src }) {
  const [failed, setFailed] = React.useState(false);
  const url = src || (hasIcon ? `${API}/mods/${encodeURIComponent(id)}/icon` : null);
  if (!url || failed) return React.createElement("div", { style: styles.modIconFallback, "aria-hidden": "true", "data-testid": `floofycrew-mod-icon-fallback-${id}` }, "🧩");
  return React.createElement("img", { src: url, alt: "", style: styles.modIcon, onError: () => setFailed(true), "data-testid": `floofycrew-mod-icon-${id}` });
}

/**
 * The on/off switch, the shape KiroCrew's own settings use: a 38×22 pill, accent when on, the strong border colour
 * when off, a white knob sliding between the ends. `role="switch"` + `aria-checked`; Enter/Space toggle like a click.
 */
export function Switch({ checked, onChange, disabled, label, testid }) {
  return React.createElement(
    "button",
    {
      type: "button",
      role: "switch",
      "aria-checked": Boolean(checked),
      "aria-label": label,
      title: label,
      disabled: Boolean(disabled),
      "data-testid": testid,
      onClick: () => !disabled && onChange(!checked),
      style: { ...styles.switchTrack, background: checked ? styles.switchOn.background : styles.switchOff.background, cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1 },
    },
    React.createElement("span", { style: { ...styles.switchKnob, left: checked ? "18px" : "2px" } }),
  );
}
