// custom-themes — the runtime part: keep the active custom theme's extra layer applied.
//
// The host paints a custom pack's palette on its own (the `<style id="mc-custom-theme-<slug>">`
// it injects after fetching the pack). What it cannot paint is the theme's extra CSS — the
// rules its runtime allowlist would drop — so this part fetches the compiled sheet of the
// active custom theme from the mod's own backend route (`GET css/<slug>`, palette + scoped
// extra layer) and holds it in `<style id="floofy-custom-themes">`, re-fetching when
//
//   * `data-theme` on <html> changes (the user picked another theme),
//   * the host announces a change to its custom themes (`mc-custom-themes-changed`),
//   * the editor page saved or installed something (`floofy-custom-themes-changed`).
//
// Every sheet it applies is also cached in localStorage (`floofy-custom-themes-css:<slug>`)
// for the boot hook, which inlines it on the next load of this client before anything paints;
// and it asks the backend once per document to refresh the first-frame sheet the boot hook
// links on a cold client (`POST refresh`), so a `floofy gc` or a CLI-side theme install never
// leaves a stale first frame behind. Same-origin only; nothing here reaches another origin.
"use strict";

const STYLE_ID = "floofy-custom-themes";
const BOOT_ID = "floofy-custom-themes-boot";
const CACHE_PREFIX = "floofy-custom-themes-css:";
const CHANGED_EVENT = "floofy-custom-themes-changed";
const HOST_CHANGED_EVENT = "mc-custom-themes-changed";

function activeSlug(doc) {
  const value = doc.documentElement.getAttribute("data-theme") || "";
  const match = /^custom-(.+)-(dark|light)$/.exec(value);
  return match ? match[1] : null;
}

export default function activate(ctx) {
  const doc = document;
  const apiBase = `/api/apps/floofycrew/mods/${encodeURIComponent(ctx.modId)}/api/`;
  let applied = null;
  let generation = 0;
  let disposed = false;

  function styleNode() {
    let node = doc.getElementById(STYLE_ID);
    if (!node) {
      node = doc.createElement("style");
      node.id = STYLE_ID;
      node.setAttribute("data-floofy-mod", ctx.modId);
      doc.head.appendChild(node);
    }
    return node;
  }

  async function apply(force = false) {
    const slug = activeSlug(doc);
    if (!slug) {
      if (applied !== null) {
        const node = doc.getElementById(STYLE_ID);
        if (node) node.textContent = "";
        applied = null;
      }
      return;
    }
    if (slug === applied && !force) return;
    const mine = ++generation;
    try {
      const response = await fetch(`${apiBase}css/${encodeURIComponent(slug)}`, { credentials: "same-origin", cache: "no-store" });
      if (mine !== generation || disposed) return;
      if (!response.ok) {
        ctx.log.info(`no compiled sheet for ${slug} (HTTP ${response.status}); the host's own palette stays`);
        styleNode().textContent = "";
        applied = slug;
        try {
          localStorage.removeItem(CACHE_PREFIX + slug);
        } catch {}
        return;
      }
      const css = await response.text();
      if (mine !== generation || disposed) return;
      styleNode().textContent = css;
      applied = slug;
      try {
        localStorage.setItem(CACHE_PREFIX + slug, css);
      } catch {}
      // The first frame came from the boot hook's element (inline cache or render-blocking link);
      // from here on this <style> is authoritative, so drop it rather than keep two copies.
      const bootNode = doc.getElementById(BOOT_ID);
      if (bootNode && bootNode.parentNode) bootNode.parentNode.removeChild(bootNode);
      ctx.log.info(`applied the compiled sheet of ${slug} (${css.length} chars)`);
    } catch (error) {
      ctx.log.warn(`could not fetch the compiled sheet of ${slug}`, error);
    }
  }

  const observer = new MutationObserver(() => void apply(false));
  observer.observe(doc.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  const onChanged = () => void apply(true);
  window.addEventListener(CHANGED_EVENT, onChanged);
  window.addEventListener(HOST_CHANGED_EVENT, onChanged);

  void apply(false);
  // keep the first-frame sheet current (fail-open: a failure here only costs the next cold frame)
  fetch(`${apiBase}refresh`, { method: "POST", credentials: "same-origin" }).catch(() => {});

  return () => {
    disposed = true;
    observer.disconnect();
    window.removeEventListener(CHANGED_EVENT, onChanged);
    window.removeEventListener(HOST_CHANGED_EVENT, onChanged);
    const node = doc.getElementById(STYLE_ID);
    if (node && node.parentNode) node.parentNode.removeChild(node);
  };
}

export function deactivate() {}
