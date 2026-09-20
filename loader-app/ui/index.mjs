// FloofyCrew Loader — manager page entry (`ui.entry`; the real page lands in 6.7).
//
// The host's AppHost renders the default export of this module as a React
// component when the user opens /apps/floofycrew (design "Spike outcomes" 1.3:
// ui.entry loads only on page open). `react` resolves through the host's inline
// import map (index.html: react -> /vendor/react.mjs), so no bundling is needed.
//
// Importing `./host.mjs` here makes the SPA host available on the manager page
// even when no `index.html` loader tag is patched in (a runtime-only UI mod
// install modifies no payload file — Requirement 4.2). Both paths resolve to the
// same module URL, so the host starts once per document.
import React from "react";

import { ensureStarted } from "./host.mjs";

export default function FloofyCrewManagerPlaceholder() {
  const [status, setStatus] = React.useState("starting");
  React.useEffect(() => {
    let cancelled = false;
    ensureStarted().then(
      (floofy) => {
        if (!cancelled) setStatus(floofy ? `SPA host ${floofy.version} ready (${floofy.loaded().length} mod(s) loaded)` : "SPA host idle (Loader state unavailable)");
      },
      () => {
        if (!cancelled) setStatus("SPA host failed to start");
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);
  return React.createElement(
    "div",
    { className: "p-6", "data-testid": "floofycrew-manager-placeholder" },
    React.createElement("p", null, "FloofyCrew (unofficial) \u2014 manager page lands in 6.7"),
    React.createElement("p", { className: "text-muted", "data-testid": "floofycrew-spa-host-status" }, status),
  );
}
