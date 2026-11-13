// FloofyCrew manager App — the act in flight, as data (no React here; `busy.mjs` renders it).
//
// `useManager.act()` records what it is doing as `activity = {path, body, phase}`:
//
// * `asking`     — the first request; for a route with a question this is the
//                  wait BEFORE the confirmation modal appears (the CLI clones
//                  the repository / reads the registry to build the disclosure),
// * `confirming` — the question is on screen; the user's turn, nothing spins,
// * `running`    — after the answer: the install, the update, the uninstall,
// * `refreshing` — the Loader state and the mod list are re-read.
//
// `isRunning` answers a button's "is that me?"; `describeActivity` turns the
// activity into the sentence the activity line shows. Nothing here is guessed
// from timing: the phases are set by the controller as the protocol advances.
"use strict";

export const PHASES = Object.freeze(["asking", "confirming", "running", "refreshing"]);

/** Is `activity` the act `path` (and, when `match` is given, with those request fields — `undefined` matches an absent field)? */
export function isRunning(activity, path, match) {
  if (!activity || activity.path !== path) return false;
  if (!match) return true;
  const body = activity.body || {};
  return Object.entries(match).every(([key, value]) => body[key] === value);
}

const modOf = (path) => {
  const match = /^\/mods\/([^/]+)\/(update|enable|disable|uninstall)$/.exec(path || "");
  return match ? { id: decodeURIComponent(match[1]), verb: match[2] } : null;
};

/** The sentences per route: `asking` only where a question is expected (there the first wait ends in the modal, and the line says so). */
export function wordsFor(path, body = {}) {
  const source = body.source ? String(body.source) : "the mod";
  const mod = modOf(path);
  if (path === "/mods/install") return { asking: `Reading ${source} and preparing the install — the confirmation opens next…`, running: `Installing ${source}…` };
  if (path === "/mods/update-all") return { asking: "Checking every update — the confirmation opens next…", running: "Updating every mod…" };
  if (path === "/mods/update-check") return { running: "Asking every source for updates…" };
  if (mod && mod.verb === "update") return { asking: `Checking the update for ${mod.id} — the confirmation opens next…`, running: `Updating ${mod.id}…` };
  if (mod && mod.verb === "enable") return { running: `Enabling ${mod.id}…` };
  if (mod && mod.verb === "disable") return { running: `Disabling ${mod.id}…` };
  if (mod && mod.verb === "uninstall") return { running: `Uninstalling ${mod.id}…` };
  if (path === "/self-update") return { asking: "Checking the FloofyCrew release — the confirmation opens next…", running: "Updating FloofyCrew…" };
  if (path === "/reload") return { running: "Reloading the Loader…" };
  if (/^\/registries(\/[^/]+)?\/refresh$/.test(path)) return { running: "Refreshing the registry…" };
  if (path === "/registries/defaults") return { running: "Recording the edition's default source…" };
  if (path === "/registries") return { running: "Adding the source…" };
  if (/^\/registries\/[^/]+\/trust$/.test(path)) return { running: "Changing the source's trust…" };
  if (/^\/registries\/[^/]+$/.test(path)) return { running: "Removing the source…" };
  if (/^\/profiles\/[^/]+\/use$/.test(path)) return { asking: "Planning the profile switch…", running: "Switching the profile…" };
  if (path === "/restore") return { running: `Restoring the quarantined set${body.version ? ` for ${body.version}` : ""}…` };
  return { running: "Working…" };
}

/** The activity line's text for this phase; `null` while a question is on screen (nothing to wait for) or when idle. */
export function describeActivity(activity) {
  if (!activity || activity.phase === "confirming") return null;
  const words = wordsFor(activity.path, activity.body || {});
  if (activity.phase === "refreshing") return `${words.running} almost done, re-reading the mod list`;
  return words[activity.phase] || words.running;
}
