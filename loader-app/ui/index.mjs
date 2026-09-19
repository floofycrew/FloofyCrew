// FloofyCrew Loader — manager page placeholder (task 4.1; the real page lands in 6.7).
//
// The host's AppHost renders the default export of this module as a React
// component when the user opens /apps/floofycrew (design "Spike outcomes" 1.3:
// ui.entry loads only on page open). `react` resolves through the host's inline
// import map (index.html: react -> /vendor/react.mjs), so no bundling is needed.
import React from "react";

export default function FloofyCrewManagerPlaceholder() {
  return React.createElement(
    "div",
    { className: "p-6", "data-testid": "floofycrew-manager-placeholder" },
    "FloofyCrew (unofficial) \u2014 manager page lands in 6.7",
  );
}
