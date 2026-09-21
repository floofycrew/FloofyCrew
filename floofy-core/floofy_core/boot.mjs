// FloofyCrew SPA host — the pre-hydration BOOT script (Requirement 4.3).
//
// This file is a TEMPLATE. The Patcher (floofy_core.boot_script) copies the
// function between the two marker comments into ONE inline classic script
// `<script id="floofy-boot">…floofyBoot({...})</script>` placed before the
// first `<script` of every payload's index.html, so it runs before anything
// can paint and before the host's own inline bootstrap. Inline scripts are
// admitted by the served CSP ('unsafe-inline' — spike 1.4). Generalised from
// the O3R theme patcher's boot block:
//
// 1. theme bootstrap — mirror the host's own formula for <html data-theme /
//    data-mode / data-mode-pref> from localStorage (`mc-color-theme`,
//    `mc-theme`) with the host config's dashboard.theme_color / theme_mode
//    captured at patch time as the fallback, so a cold client (empty storage,
//    pre-auth page, fresh browser) still paints the configured theme;
// 2. per mod, in load order (only when `when.theme` matches the active colour
//    theme, if declared): assert `attributes` on <html>, add the favicon link,
//    set the `--theme-logo` custom property, set the title, run the mod's own
//    boot hook (the `spa` part file, inlined as `function (ctx)`);
// 3. drift guard — a MutationObserver re-asserts the attributes until the host
//    settles (its own theme <style id="mc-custom-theme-…"> or favicon
//    <link id="mc-theme-favicon"> lands, or 5 s pass); the moment the host's
//    favicon link exists the boot favicon links are removed, so the host's
//    authoritative icon is the only themed <link rel=icon>.
//
// The baked CSS is NOT here: the Patcher appends one <style id="floofy-boot-css-<id>">
// per mod just before </head>, after the host's stylesheets (source order wins
// on equal specificity). Constraints inside the template: ES5 only, no
// backticks, no template literals, no `</script` sequence, everything fail-open.
"use strict";

/* floofy-boot-template-start */
function floofyBoot(config) {
  try {
    var doc = document;
    var el = doc.documentElement;
    var defaults = config.defaults || {};
    var color = null;
    var pref = null;
    try {
      color = localStorage.getItem('mc-color-theme');
      pref = localStorage.getItem('mc-theme');
    } catch (e) {}
    color = color || defaults.color || 'kiro';
    pref = pref || defaults.mode || 'system';
    var mode = pref === 'system'
      ? (window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
      : pref;
    if (config.themeBootstrap !== false) {
      el.setAttribute('data-theme', color === 'emerald' ? mode : color + '-' + mode);
      el.setAttribute('data-mode', mode);
      el.setAttribute('data-mode-pref', pref);
    }
    var state = { color: color, mode: mode, pref: pref, applied: [], skipped: [], settled: false, faviconYielded: false, errors: [] };
    var guarded = [];
    var mods = config.mods || [];
    for (var i = 0; i < mods.length; i++) {
      var mod = mods[i];
      try {
        if (mod.when && mod.when.theme && mod.when.theme !== color) {
          state.skipped.push(mod.id);
          continue;
        }
        if (mod.attributes) {
          for (var name in mod.attributes) {
            if (Object.prototype.hasOwnProperty.call(mod.attributes, name)) {
              el.setAttribute(name, mod.attributes[name]);
              guarded.push([name, mod.attributes[name]]);
            }
          }
        }
        if (mod.favicon) {
          var link = doc.createElement('link');
          link.id = 'floofy-boot-favicon-' + mod.id;
          link.rel = 'icon';
          link.href = mod.favicon;
          (doc.head || el).appendChild(link);
        }
        if (mod.logo) el.style.setProperty('--theme-logo', "url('" + mod.logo + "')");
        if (mod.title) doc.title = mod.title;
        if (typeof mod.hook === 'function') mod.hook({ id: mod.id, color: color, mode: mode, pref: pref, element: el });
        state.applied.push(mod.id);
      } catch (modError) {
        state.errors.push(mod.id + ': ' + (modError && modError.message ? modError.message : String(modError)));
      }
    }
    var settle = function () {
      if (state.settled) return;
      state.settled = true;
      try { attrObserver.disconnect(); } catch (e) {}
      dropBootFavicons();
    };
    var dropBootFavicons = function () {
      // the host's own <link id="mc-theme-favicon"> is authoritative once present
      if (!doc.getElementById('mc-theme-favicon')) return false;
      for (var j = 0; j < mods.length; j++) {
        var stale = doc.getElementById('floofy-boot-favicon-' + mods[j].id);
        if (stale && stale.parentNode) stale.parentNode.removeChild(stale);
      }
      state.faviconYielded = true;
      try { headObserver.disconnect(); } catch (e) {}
      return true;
    };
    var headObserver = { disconnect: function () {} };
    var attrObserver = { disconnect: function () {} };
    if (typeof MutationObserver === 'function') {
      headObserver = new MutationObserver(function (records) {
        for (var r = 0; r < records.length; r++) {
          var added = records[r].addedNodes;
          for (var a = 0; a < added.length; a++) {
            var node = added[a];
            if (!node || node.nodeType !== 1) continue;
            var id = node.id || '';
            if (node.tagName === 'STYLE' && id.indexOf('mc-custom-theme-') === 0) settle();
            if (node.tagName === 'LINK' && id === 'mc-theme-favicon') { settle(); dropBootFavicons(); }
          }
        }
      });
      if (doc.head) headObserver.observe(doc.head, { childList: true });
      if (guarded.length) {
        attrObserver = new MutationObserver(function () {
          if (state.settled) return;
          for (var g = 0; g < guarded.length; g++) {
            if (el.getAttribute(guarded[g][0]) !== guarded[g][1]) el.setAttribute(guarded[g][0], guarded[g][1]);
          }
        });
        attrObserver.observe(el, { attributes: true });
      }
      setTimeout(function () { settle(); if (!state.faviconYielded) { try { headObserver.disconnect(); } catch (e) {} } }, config.settleTimeoutMs || 5000);
    }
    state.settle = settle;
    window.__floofyBoot = state;
  } catch (e) {
    try { window.__floofyBootError = String(e && e.message ? e.message : e); } catch (ignored) {}
  }
}
/* floofy-boot-template-end */

export { floofyBoot };

/** Marker id of the inline boot script the Patcher injects. */
export const BOOT_SCRIPT_ID = "floofy-boot";
/** Prefix of the per-mod baked-CSS style elements. */
export const BOOT_CSS_ID_PREFIX = "floofy-boot-css-";
