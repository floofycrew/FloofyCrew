// FloofyCrew manager App — the host-level action buttons shared by the shell and the pages (1.1.6).
//
// `RestartButton` is the red one: `POST /host/restart` asks its yes-no question
// through the App's modal (the 409 protocol; nothing is answered for the user)
// and then the Loader hands the restart to `kirocrew restart`; the shell waits
// for the new gateway and reloads the dashboard. `UpdateAndRestartButton` runs
// `self-update --now` first (the CLI's own "Install FloofyCrew X over Y?"
// question, same modal) and the restart after it. Both take the `manager` of
// `useManager()`, which owns the flows (`restartHost`, `selfUpdateAndRestart`).
import React from "react";

import { styles } from "./palette.mjs";

/** The red button: restart the gateway after staged installs / a Loader update / a vanilla request. */
export function RestartButton({ manager, label = "Restart KiroCrew", testid = "floofycrew-restart", style = {} }) {
  return React.createElement(
    "button",
    { type: "button", style: { ...styles.button, ...styles.restart, marginBottom: 0, ...style }, disabled: manager.busy || manager.restarting, "data-testid": testid, title: "Restart the KiroCrew gateway now (kirocrew restart). Staged installs, a Loader app update and a vanilla request take effect at the start.", onClick: () => manager.restartHost() },
    manager.restarting ? "Restarting…" : label,
  );
}

/** "Update & restart", shown when a newer FloofyCrew supports this host: self-update --now, then the restart — each step asks first. */
export function UpdateAndRestartButton({ manager, version, testid = "floofycrew-self-update-restart" }) {
  return React.createElement(
    "button",
    { type: "button", style: { ...styles.button, ...styles.primary }, disabled: manager.busy || manager.restarting, "data-testid": testid, title: "floofy self-update --now, then kirocrew restart — each step asks first", onClick: () => manager.selfUpdateAndRestart() },
    `Update to ${version || "the newest release"} & restart`,
  );
}
