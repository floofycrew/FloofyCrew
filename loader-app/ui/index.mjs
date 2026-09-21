// FloofyCrew — the manager App shell (`ui.entry`; Requirement 16.1, 16.2, 2.7, 7.3, 11.8).
//
// The host's AppHost renders the default export of this module as a React
// component when the user opens /apps/floofycrew (design "Spike outcomes" 1.3:
// ui.entry loads only on page open). `react` resolves through the host's inline
// import map (index.html: react -> /vendor/react.mjs); no bundler, no npm, no
// `@kirocrew/app-sdk/ui` components — plain elements and inline styles survive
// host bundle churn. The App shows the host's Apps list the display name
// `FloofyCrew` with the branding mark (app.json `iconPath: art/icon.svg`).
//
// Navigation: Mods (the manager page, the landing page) · Registry · Profiles ·
// Doctor · Audit · Settings, each a module under `./app/pages/`; the current
// page lives in the URL hash (`#/registry`) so a page can be linked to. Every
// action goes through the Loader's typed routes (`./app/api.mjs`, task 11.1) —
// the same `floofy` handler as the CLI, `actor: app` in the audit row — and a
// confirmation the CLI would ask arrives as a 409 question the App presents.
// The unofficial statement is always visible — in the banner, and in the footer
// once the banner has been dismissed with "don't show this again" (a browser
// preference; Settings › About restores it); the palette mirrors the CLI's.
// A capability the App lacks is shown with its terminal command, never hidden.
// The red "Restart KiroCrew" button (header, the staged line, Settings) is the
// one action that touches the host process: POST /host/restart asks first and
// hands the restart to `kirocrew restart`; "Update & restart" chains it after
// `self-update --now` when a newer FloofyCrew supports this host.
//
// Importing `./host.mjs` here makes the SPA host available on the manager page
// even when no `index.html` loader tag is patched in (Requirement 4.2).
import React from "react";

import { ensureStarted } from "./host.mjs";
import { RestartButton } from "./app/actions.mjs";
import { API, callRoute, getRegistry, getState, getStatus, reload as reloadLoader, transcriptOf, waitForGateway } from "./app/api.mjs";
import { ConfirmationHost, useAsker } from "./app/confirm.mjs";
import { RADIUS, UNOFFICIAL, styles } from "./app/palette.mjs";
import { AuditPage } from "./app/pages/audit.mjs";
import { DoctorPage } from "./app/pages/doctor.mjs";
import { ModsPage } from "./app/pages/mods.mjs";
import { ProfilesPage } from "./app/pages/profiles.mjs";
import { RegistryPage } from "./app/pages/registry.mjs";
import { SettingsPage } from "./app/pages/settings.mjs";

export { API, UNOFFICIAL, callRoute };
export { RestartButton, UpdateAndRestartButton } from "./app/actions.mjs";
export { apiFetch, runCli } from "./app/api.mjs";
export { compatBadge, tierBadge } from "./app/pages/mods.mjs";

/** app.json `iconPath` — the branding mark (`branding/logo.svg`), served by the host from `/apps/floofycrew/art/<iconPath>`. */
export const ICON_PATH = "art/icon.svg";

/** The navigation, in order; the first entry is the landing page (Requirement 16.1). */
export const PAGES = Object.freeze([
  { key: "mods", label: "Mods", component: ModsPage },
  { key: "registry", label: "Registry", component: RegistryPage },
  { key: "profiles", label: "Profiles", component: ProfilesPage },
  { key: "doctor", label: "Doctor", component: DoctorPage },
  { key: "audit", label: "Audit", component: AuditPage },
  { key: "settings", label: "Settings", component: SettingsPage },
]);

/** The page named by the hash (`#/registry`, `#/mods/<id>`), defaulting to the landing page. */
export function pageFromHash(hash) {
  const match = /^#\/?([a-z]+)(?:\/(.*))?$/.exec(hash || "");
  const key = match ? match[1] : "mods";
  return { key: PAGES.some((p) => p.key === key) ? key : "mods", rest: match && match[2] ? decodeURIComponent(match[2]) : "" };
}

/**
 * The App's shared controller: the Loader state, the status rows, the registry document, the output pane
 * and `act(path, body)` — one typed route call through the confirmation protocol, then a refresh.
 */
export function useManager({ ask } = {}) {
  const [state, setState] = React.useState(null);
  const [rows, setRows] = React.useState([]);
  const [statusMeta, setStatusMeta] = React.useState(null);
  const [registry, setRegistry] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [output, setOutput] = React.useState("");
  const [error, setError] = React.useState("");

  const refresh = React.useCallback(async () => {
    const [stateReply, statusReply, registryReply] = await Promise.all([getState(), getStatus(), getRegistry()]);
    if (stateReply.ok) setState(stateReply.body);
    if (statusReply.ok && statusReply.json) {
      setRows(statusReply.json.mods || []);
      setStatusMeta(statusReply.json);
    } else if (!statusReply.ok) {
      setError(statusReply.error || statusReply.stderr || `status failed (HTTP ${statusReply.httpStatus})`);
    }
    if (registryReply.ok) setRegistry(registryReply.body);
  }, []);

  React.useEffect(() => {
    ensureStarted().catch(() => undefined);
    refresh().catch((exc) => setError(String(exc)));
  }, [refresh]);

  const act = React.useCallback(
    async (path, body = {}, options = {}) => {
      setBusy(true);
      setError("");
      let result = null;
      try {
        result = await callRoute(path, body, { ask, ...options });
        setOutput(transcriptOf(result, path));
        if (!result.ok && !result.declined) setError(result.error || (result.needs ? `${result.needs.kind} confirmation needed` : `HTTP ${result.httpStatus}`));
      } catch (exc) {
        setError(String(exc));
        result = { ok: false, error: String(exc) };
      } finally {
        setBusy(false);
        await refresh().catch(() => undefined);
      }
      return result;
    },
    [ask, refresh],
  );

  const applyNow = React.useCallback(async () => {
    setBusy(true);
    try {
      const reply = await reloadLoader();
      setOutput(reply.ok ? "Loader reloaded: pending installs applied, enabled.json re-read, patches re-applied" : `reload failed (HTTP ${reply.status})`);
    } finally {
      setBusy(false);
      await refresh().catch(() => undefined);
    }
  }, [refresh]);

  // The red button: POST /host/restart asks its yes-no question through the same protocol (the modal), then the Loader
  // hands the restart to `kirocrew restart`; this side only waits for the new gateway and reloads the dashboard.
  const [restarting, setRestarting] = React.useState(false);
  const restartHost = React.useCallback(async () => {
    setBusy(true);
    setError("");
    let result = null;
    try {
      result = await callRoute("/host/restart", {}, { ask });
      setOutput(transcriptOf(result, "/host/restart"));
      if (result.ok && result.restarting) {
        setRestarting(true);
        waitForGateway().then((back) => {
          if (back && typeof location !== "undefined") location.reload();
          else setRestarting(false);
        });
      } else if (!result.ok && !result.declined) {
        setError(result.error || `HTTP ${result.httpStatus}`);
      }
    } catch (exc) {
      setError(String(exc));
      result = { ok: false, error: String(exc) };
    } finally {
      setBusy(false);
    }
    return result;
  }, [ask]);

  // "Update & restart": the CLI's own install question first (yes-no 409), then the restart's; nothing is answered for the user.
  const selfUpdateAndRestart = React.useCallback(async () => {
    const updated = await act("/self-update", { now: true });
    if (!updated || !updated.ok) return updated;
    return restartHost();
  }, [act, restartHost]);

  return { state, rows, statusMeta, registry, busy, output, error, restarting, refresh, act, applyNow, restartHost, selfUpdateAndRestart, setOutput, setError, ask };
}



function Header({ manager }) {
  const state = manager.state;
  const host = (state && state.host) || {};
  return React.createElement(
    "div",
    { style: styles.header, "data-testid": "floofycrew-header" },
    // the host serves an installed app's declared art at /apps/<name>/art/<iconPath verbatim> (kiro_crew/apps/routes.py handle_app_art_file)
    React.createElement("img", { src: `/apps/floofycrew/art/${ICON_PATH}`, alt: "", width: 36, height: 36, style: { borderRadius: RADIUS }, "data-testid": "floofycrew-icon" }),
    React.createElement("h1", { style: styles.title }, "FloofyCrew"),
    React.createElement("span", { style: styles.muted }, state ? `Loader ${state.loader} · FloofyCrew ${state.loaderVersion || "?"} · API ${state.api_version || "?"}` : "loading…"),
    React.createElement("span", { style: styles.muted }, `host ${host.edition || "?"} ${host.version || "?"} (${host.channel || "channel unknown"})`),
    React.createElement("div", { style: styles.headerActions }, React.createElement(RestartButton, { manager, testid: "floofycrew-restart-header" })),
  );
}

/** The author line of the footer. */
export const AUTHOR = "Oscar Tseng";
/** Where the banner preference lives: the browser's localStorage, per dashboard origin — a UI preference, not Loader state. */
export const BANNER_PREFERENCE_KEY = "floofycrew.banner.hidden";

function readBannerHidden() {
  try {
    return typeof localStorage !== "undefined" && localStorage.getItem(BANNER_PREFERENCE_KEY) === "1";
  } catch {
    return false;
  }
}

/**
 * The banner preference: "don't show this again" hides the long unofficial banner on this browser; Settings brings it
 * back. The unofficial statement itself stays on every page — the footer carries it — so hiding the banner never hides
 * what FloofyCrew is (Requirement 11.8).
 */
export function useBannerPreference() {
  const [hidden, setHidden] = React.useState(readBannerHidden);
  const set = React.useCallback((value) => {
    setHidden(value);
    try {
      if (value) localStorage.setItem(BANNER_PREFERENCE_KEY, "1");
      else localStorage.removeItem(BANNER_PREFERENCE_KEY);
    } catch {
      /* no storage: the preference lasts for this page only */
    }
  }, []);
  return { hidden, hide: () => set(true), show: () => set(false) };
}

function Banner({ banner }) {
  if (banner.hidden) return null;
  return React.createElement(
    "div",
    { style: styles.banner, "data-testid": "floofycrew-banner", role: "note" },
    React.createElement("span", null, `FloofyCrew mod manager — UNOFFICIAL. ${UNOFFICIAL} Mods run with the gateway's privileges on your consent; governance verdicts are warnings, never gates.`),
    React.createElement("button", { type: "button", style: styles.bannerDismiss, "data-testid": "floofycrew-banner-dismiss", title: "Hide this banner on this browser; Settings › About shows it again. The footer keeps the unofficial statement.", onClick: banner.hide }, "Don't show this again"),
  );
}

function Footer({ state }) {
  return React.createElement(
    "footer",
    { style: styles.footer, "data-testid": "floofycrew-footer" },
    React.createElement("span", null, UNOFFICIAL),
    React.createElement("span", null, `FloofyCrew ${(state && state.loaderVersion) || "?"}`),
    React.createElement("span", { "data-testid": "floofycrew-footer-author" }, `author: ${AUTHOR}`),
  );
}

function Nav({ current, onSelect }) {
  return React.createElement(
    "nav",
    { style: styles.nav, "data-testid": "floofycrew-nav", "aria-label": "FloofyCrew sections" },
    PAGES.map((page) =>
      React.createElement(
        "button",
        { key: page.key, type: "button", style: { ...styles.navItem, ...(page.key === current ? styles.navItemActive : {}) }, "data-testid": `floofycrew-nav-${page.key}`, "aria-current": page.key === current ? "page" : undefined, onClick: () => onSelect(page.key) },
        page.label,
      ),
    ),
  );
}

export default function FloofyCrewApp({ ask: askOverride, initialPage } = {}) {
  const asker = useAsker();
  const ask = askOverride || asker.ask;
  const [route, setRoute] = React.useState(() => (initialPage ? { key: initialPage, rest: "" } : pageFromHash(typeof location !== "undefined" ? location.hash : "")));
  React.useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const onHash = () => setRoute(pageFromHash(location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const select = React.useCallback((key, rest = "") => {
    setRoute({ key, rest });
    if (typeof location !== "undefined") {
      const hash = rest ? `#/${key}/${encodeURIComponent(rest)}` : `#/${key}`;
      if (location.hash !== hash) history.replaceState(null, "", hash);
    }
  }, []);
  const manager = useManager({ ask });
  const page = PAGES.find((p) => p.key === route.key) || PAGES[0];
  // switching sections re-reads the Loader (state, status, registry) so a page never shows what a terminal changed meanwhile
  const { refresh } = manager;
  React.useEffect(() => {
    refresh().catch(() => undefined);
  }, [route.key, refresh]);
  // the one-time warning on request (Settings, the "consent required" line): the consent route asks, the modal answers
  const openConsent = React.useCallback(({ reaccept = false } = {}) => manager.act("/consent", reaccept ? { reaccept: true } : {}), [manager.act]);
  const banner = useBannerPreference();

  return React.createElement(
    "div",
    { style: styles.page, "data-testid": "floofycrew-manager" },
    React.createElement(Header, { manager }),
    React.createElement(Banner, { banner }),
    React.createElement(Nav, { current: page.key, onSelect: select }),
    manager.restarting ? React.createElement("div", { style: { ...styles.card, ...styles.warn }, "data-testid": "floofycrew-restarting", role: "status" }, "Restarting the KiroCrew gateway — the dashboard reloads when the new gateway answers (up to two minutes). If it does not come back, run `kirocrew restart` at a terminal.") : null,
    React.createElement("section", { style: styles.pageBody, "data-testid": `floofycrew-page-${page.key}` }, React.createElement(page.component, { manager, rest: route.rest, navigate: select, openConsent, banner })),
    manager.error ? React.createElement("div", { style: { ...styles.card, ...styles.danger }, "data-testid": "floofycrew-error" }, manager.error) : null,
    manager.output ? React.createElement("pre", { style: { ...styles.card, ...styles.mono }, "data-testid": "floofycrew-output" }, manager.output) : null,
    React.createElement(Footer, { state: manager.state }),
    askOverride ? null : React.createElement(ConfirmationHost, { pending: asker.pending, answer: asker.answer }),
  );
}
