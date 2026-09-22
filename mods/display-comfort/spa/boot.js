// display-comfort — first-frame hook (inlined by the Patcher as the body of function (ctx); ES5 only).
//
// Re-assert the cached settings before anything paints, so a reload never flashes
// the default sizes: the chat scale as one custom property on <html> (the baked
// boot stylesheet turns it into zoom on every message bubble), the composer opt-in
// as a data attribute, and the page zoom directly on the document element.
//
// The cache is what the runtime part / settings page stored on the last apply
// (localStorage 'floofy-display-comfort'). Everything fail-open: no cache, a
// tampered cache, or an out-of-range number leaves the host exactly as it would
// paint without this mod. The runtime part re-applies the authoritative config
// once the dashboard is up.
var raw = null;
try { raw = localStorage.getItem('floofy-display-comfort'); } catch (storageError) {}
if (raw) {
  var settings = null;
  try { settings = JSON.parse(raw); } catch (parseError) {}
  if (settings && typeof settings === 'object') {
    var el = ctx.element || document.documentElement;
    var chat = Math.round(Number(settings.chatScale));
    if (isFinite(chat) && chat >= 50 && chat <= 250 && chat !== 100) {
      el.style.setProperty('--floofy-dc-chat-scale', String(chat / 100));
    }
    if (settings.composerToo === true) el.setAttribute('data-floofy-dc-composer', 'on');
    var zoom = Math.round(Number(settings.pageZoom));
    if (isFinite(zoom) && zoom >= 25 && zoom <= 300 && zoom !== 100) {
      el.style.zoom = String(zoom / 100);
    }
  }
}
