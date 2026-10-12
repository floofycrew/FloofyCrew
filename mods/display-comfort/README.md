# display-comfort

Persistent chat text size and an arbitrary page zoom for the KiroCrew dashboard —
the two most-asked-for display quality-of-life controls, as one FloofyCrew mod.

- **Chat text scale** (50–250 %): every message bubble — text, code blocks and
  spacing scale together. A toggle extends the same scale to the composer.
- **Page zoom** (25–300 %, 1 % steps): the whole dashboard at any percentage,
  not just the browser's fixed zoom stops.

Both settings live in the FloofyCrew App under **Mods → Display comfort**, apply
live as you drag, persist in the mod's config, and are re-asserted **before the
first paint** on the next load (a localStorage cache read by the mod's boot
hook), so a reload never flashes the default sizes.

## How it works

| Part | What it does |
|---|---|
| `ui/page.mjs` | the settings page: two sliders + composer toggle + reset, persisted through `floofy.mod(id).config`, previewed live, broadcast as `floofy-display-comfort-changed` |
| `spa/runtime.mjs` | applies the stored settings: one custom property on `<html>` for the chat scale, `data-floofy-dc-composer` for the composer opt-in, `zoom` on the document element for the page zoom; undoes all three on disable |
| `spa/boot.js` + `spa/boot.css` | first frame: the boot hook re-asserts the cached values before anything paints, and the baked stylesheet carries the bubble rules |

Chat scaling uses CSS `zoom` on `[data-testid="message-bubble"]` rather than
`font-size`, because the host styles message text with explicit rem/px utility
classes that would ignore an inherited font-size — zoom scales the bubble's
text, code and spacing proportionally and lays out correctly in Chromium (the
dashboard's browser and the desktop app alike).

Everything is fail-open: no stored settings, an out-of-range value, or a host
that renames the bubble marker leaves the dashboard exactly as stock. No
network beyond the Loader's same-origin config route; no browser pop-ups.

## Provenance

Original work for this mod; no third-party artwork. Unofficial — not part of
KiroCrew. Addresses the upstream requests for a persistent chat font-size
control and a free-form zoom value.
