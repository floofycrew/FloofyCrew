// FloofyCrew manager App — a mod's own page (Requirement 16.4; task 11.5).
//
// A mod with a `ui` part is listed under Mods; opening it mounts the mod's
// module here: `import()` of the same-origin URL
// `/api/apps/floofycrew/ui/mods/<id>/<entry>` (served by the Loader for an
// active mod, manifest-listed files only; a same-origin dynamic import is
// admitted by the served CSP — spike 1.4), then `mount(container, api)` with
// `api = window.floofy.mod(id)` (config get/set, the mod's routes, theme
// tokens, state). Everything runs inside an error boundary: an import or mount
// that throws, a rejected mount, or a later error whose stack names the mod's
// URL prefix is caught, shown with the message and a **Disable <mod>** button
// (the disable route, with its confirmation), and never takes the manager page
// down — the shell, its navigation and the other pages keep working.
import React from "react";

import { API } from "../api.mjs";
import { commandNote, styles } from "../palette.mjs";

/** The mods of the Loader state that declare a `ui` part, with the part's entry/title/icon. */
export function pagesOf(state) {
  const out = [];
  for (const [id, mod] of Object.entries((state && state.mods) || {})) {
    for (const part of mod.parts || []) {
      if (part.kind !== "ui") continue;
      out.push({ id, version: mod.version, name: mod.name || id, active: Boolean(mod.active), enabled: mod.enabled !== false, reason: mod.reason || null, entry: part.entry, title: part.title || id, icon: part.icon || null, status: part.status });
    }
  }
  return out.sort((a, b) => a.title.localeCompare(b.title));
}

export function entryUrl(id, entry) {
  return `${API}/ui/mods/${encodeURIComponent(id)}/${String(entry || "").split("/").map(encodeURIComponent).join("/")}`;
}

function describe(error) {
  if (!error) return { message: "unknown error", stack: "" };
  if (typeof error === "string") return { message: error, stack: "" };
  return { message: String(error.message || error.name || error), stack: typeof error.stack === "string" ? error.stack : "" };
}

export function ModPage({ manager, modId, navigate }) {
  const { state, act, busy } = manager;
  const page = React.useMemo(() => pagesOf(state).find((p) => p.id === modId) || null, [state, modId]);
  const container = React.useRef(null);
  const [fault, setFault] = React.useState(null);
  const [mounted, setMounted] = React.useState(false);
  const cleanupRef = React.useRef(null);
  const prefix = entryUrl(modId, "");

  React.useEffect(() => {
    if (!page || !page.active || !container.current) return undefined;
    let cancelled = false;
    const target = container.current;
    target.textContent = "";
    setFault(null);
    setMounted(false);
    const onGlobal = (event) => {
      const inner = event.error ?? event.reason ?? event;
      const text = [event.filename, inner && inner.stack, inner && inner.message].filter((x) => typeof x === "string").join("\n");
      if (text.includes(prefix)) {
        setFault({ where: event.type === "unhandledrejection" ? "unhandled rejection" : "uncaught error", ...describe(inner) });
        if (typeof event.preventDefault === "function") event.preventDefault();
      }
    };
    window.addEventListener("error", onGlobal);
    window.addEventListener("unhandledrejection", onGlobal);
    (async () => {
      let where = "import";
      try {
        const module = await import(entryUrl(modId, page.entry));
        if (cancelled) return;
        const mount = typeof module.default === "function" ? module.default : module.mount;
        if (typeof mount !== "function") throw new Error(`${page.entry} has no default export mount(container, api)`);
        where = "mount";
        const api = window.floofy && typeof window.floofy.mod === "function" ? window.floofy.mod(modId) : null;
        if (!api) throw new Error("window.floofy.mod is not available: the SPA host did not start");
        const result = await mount(target, api);
        if (cancelled) return;
        cleanupRef.current = typeof result === "function" ? result : result && typeof result.unmount === "function" ? () => result.unmount() : null;
        setMounted(true);
      } catch (error) {
        if (!cancelled) setFault({ where, ...describe(error) });
      }
    })();
    return () => {
      cancelled = true;
      window.removeEventListener("error", onGlobal);
      window.removeEventListener("unhandledrejection", onGlobal);
      try {
        if (cleanupRef.current) cleanupRef.current();
      } catch {
        /* a failing cleanup never leaks out of the boundary */
      }
      cleanupRef.current = null;
      target.textContent = "";
    };
  }, [modId, page && page.active, page && page.entry]); // eslint-disable-line react-hooks/exhaustive-deps

  const back = () => navigate && navigate("mods");
  if (!page) {
    return React.createElement("div", { style: styles.card, "data-testid": "floofycrew-modpage-missing" }, React.createElement("button", { style: styles.button, onClick: back }, "← Mods"), ` ${modId} has no page (no ui part, or the mod is not installed).`);
  }
  return React.createElement(
    "div",
    { "data-testid": `floofycrew-modpage-${modId}` },
    React.createElement(
      "div",
      { style: { display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "0.6rem" } },
      React.createElement("button", { style: styles.button, onClick: back, "data-testid": "floofycrew-modpage-back" }, "← Mods"),
      page.icon ? React.createElement("img", { src: entryUrl(modId, page.icon), alt: "", width: 20, height: 20 }) : null,
      React.createElement("strong", null, page.title),
      React.createElement("code", { style: styles.muted }, `${modId} ${page.version}`),
      React.createElement("span", { style: { ...styles.badge, ...(page.active ? styles.ok : styles.warn) } }, page.active ? "active" : page.reason || (page.enabled ? "inactive" : "disabled")),
      page.active
        ? React.createElement("button", { style: { ...styles.button, marginLeft: "auto" }, disabled: busy, title: `floofy disable ${modId} — the page stops being served; the mod's settings stay`, "data-testid": "floofycrew-modpage-disable-mod", onClick: async () => { const r = await act(`/mods/${encodeURIComponent(modId)}/disable`); if (r && r.ok) back(); } }, `Disable ${modId}`)
        : null,
    ),
    !page.active
      ? React.createElement("div", { style: { ...styles.card, ...styles.warn }, "data-testid": "floofycrew-modpage-inactive" }, `${modId} is not active (${page.reason || "disabled"}); its page is not served. `, page.enabled ? null : React.createElement("button", { style: styles.button, disabled: busy, onClick: () => act(`/mods/${encodeURIComponent(modId)}/enable`) }, "Enable"))
      : null,
    fault
      ? React.createElement(
          "div",
          { style: { ...styles.card, borderColor: styles.danger.color }, role: "alert", "data-testid": "floofycrew-modpage-fault" },
          React.createElement("div", { style: styles.danger }, `${page.title} failed during ${fault.where}: ${fault.message}`),
          fault.stack ? React.createElement("pre", { style: { ...styles.mono, maxHeight: "10rem" } }, fault.stack.slice(0, 2000)) : null,
          React.createElement("div", { style: styles.muted }, "The page is isolated by an error boundary: the manager and the other mods are unaffected. Disable the mod, or reload the App to try again."),
          React.createElement(
            "div",
            { style: { marginTop: "0.4rem" } },
            React.createElement("button", { style: { ...styles.button, ...styles.danger }, disabled: busy, "data-testid": "floofycrew-modpage-disable", onClick: async () => { const r = await act(`/mods/${encodeURIComponent(modId)}/disable`); if (r && r.ok) back(); } }, `Disable ${modId}`),
            React.createElement("button", { style: styles.button, onClick: back }, "Back to Mods"),
          ),
          commandNote(React, "At a terminal:", `floofy disable ${modId}`),
        )
      : null,
    React.createElement("div", { ref: container, style: { ...styles.card, display: fault ? "none" : undefined }, "data-testid": `floofycrew-modpage-container-${modId}`, "data-mounted": mounted ? "1" : "0" }),
  );
}

/** The "Mods with pages" list of the Mods page. */
export function ModPagesList({ manager, navigate }) {
  const pages = pagesOf(manager.state);
  if (!pages.length) return null;
  return React.createElement(
    "div",
    { style: styles.card, "data-testid": "floofycrew-mod-pages" },
    React.createElement("h2", { style: styles.cardTitle }, "Mod pages"),
    React.createElement("div", { style: styles.muted }, "Mods that ship their own settings page (a ui part); a page is served only while its mod is active and runs inside an error boundary."),
    pages.map((page) =>
      React.createElement(
        "div",
        { key: page.id, style: { display: "flex", alignItems: "center", gap: "0.6rem", padding: "0.3rem 0" }, "data-testid": `floofycrew-mod-page-${page.id}` },
        React.createElement("button", { style: { ...styles.button, ...styles.primary }, disabled: !page.active, title: page.active ? "" : `${page.id} is ${page.reason || "not active"}`, "data-testid": `floofycrew-open-page-${page.id}`, onClick: () => navigate("mods", page.id) }, `Open ${page.title}`),
        React.createElement("code", null, `${page.id} ${page.version}`),
        React.createElement("span", { style: { ...styles.badge, ...(page.active ? styles.ok : styles.warn) } }, page.active ? "active" : page.reason || (page.enabled ? "inactive" : "disabled")),
      ),
    ),
  );
}
