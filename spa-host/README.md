# spa-host

The dashboard-side runtime, plain ES modules served from the Loader app's `ui/`
(`/apps/floofycrew/ui/…`): the always-on host (`host.mjs`, loaded by the one-line
`index.html` tag the Patcher injects — spike 1.3), `window.floofy` (`host`,
`api_version`, `surfaces` located by content fingerprints, `patches` inspection,
`events`, `report()`), the error boundary each `spa` part runs in, the reporter
mode the Forge drives with Playwright, and the pre-hydration boot script the
Patcher bakes into `index.html`. Same-origin only and no CSP changes (spike 1.4);
it never loads code from third-party origins and never references the internal
edition — CI scans this directory for internal identifiers. The build output is
copied into `loader-app/ui/` at packaging time.
