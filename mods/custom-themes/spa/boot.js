// custom-themes — first-frame hook (inlined by the Patcher as the body of function (ctx); ES5 only).
//
// When the active colour theme is a custom pack, get its compiled sheet (palette + extra
// layer) in front of the first paint:
//
//   1. a WARM client has the sheet the runtime part cached in localStorage the last time
//      this theme was applied — it is written inline, synchronously, so not even the
//      frame between this script and the rest of <head> can show the stock colours;
//   2. a COLD client (empty storage, another browser, the pre-auth token page) gets a
//      render-blocking <link> to the sheet the mod's backend keeps for every installed
//      custom theme, served by the Loader app's unauthenticated ui route (no-cache).
//
// document.write() during parsing inserts parser-processed nodes, which is what makes
// both synchronous with respect to the host's own stylesheets. The runtime part replaces
// either with its own <style> once the dashboard is up. Fail-open: without a cache and
// without the sheet (404) the host paints as it would have without this mod. The cached
// text came from the mod's own compiler, which refuses a closing style tag; the guard
// below re-checks before inlining so a tampered cache cannot break out of the element.
if (ctx.color && ctx.color.indexOf('custom-') === 0 && typeof document.write === 'function' && document.readyState === 'loading') {
  var cached = null;
  try { cached = localStorage.getItem('floofy-custom-themes-css:' + ctx.color.slice(7)); } catch (storageError) {}
  if (cached && cached.length < 262144 && !/<\/style/i.test(cached) && cached.indexOf('custom-' + ctx.color.slice(7) + '-') !== -1) {
    document.write('<style id="floofy-custom-themes-boot" data-floofy-mod="custom-themes">' + cached + '</style>');
  } else {
    document.write('<link rel="stylesheet" id="floofy-custom-themes-boot" data-floofy-mod="custom-themes" href="/apps/floofycrew/ui/boot/custom-themes/themes.css">');
  }
}
