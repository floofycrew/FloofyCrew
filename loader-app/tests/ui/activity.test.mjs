// The act-in-flight data (`ui/app/activity.mjs`) under plain `node --test`: which button is "running", and what the
// activity line says per phase — the wait before the confirmation modal, the work after it, the re-read at the end.
import assert from "node:assert/strict";
import { test } from "node:test";

import { PHASES, describeActivity, isRunning, wordsFor } from "../../ui/app/activity.mjs";

test("isRunning matches the path, then the request fields the button sent (undefined = absent)", () => {
  const staged = { path: "/mods/alpha/update", body: {}, phase: "running" };
  const now = { path: "/mods/alpha/update", body: { now: true }, phase: "running" };
  assert.equal(isRunning(null, "/mods/alpha/update"), false);
  assert.equal(isRunning(staged, "/mods/beta/update"), false);
  assert.equal(isRunning(staged, "/mods/alpha/update"), true, "no match: the path alone decides");
  assert.equal(isRunning(staged, "/mods/alpha/update", { now: undefined }), true, "the staged button: now is absent");
  assert.equal(isRunning(staged, "/mods/alpha/update", { now: true }), false, "the 'now' button is not the one running");
  assert.equal(isRunning(now, "/mods/alpha/update", { now: true }), true);
  assert.equal(isRunning(now, "/mods/alpha/update", { now: undefined }), false);
  const hit = { path: "/mods/install", body: { source: "custom-themes" }, phase: "asking" };
  assert.equal(isRunning(hit, "/mods/install", { source: "custom-themes", now: undefined }), true);
  assert.equal(isRunning(hit, "/mods/install", { source: "rimuru-branding", now: undefined }), false, "only the clicked hit spins");
});

test("the activity line: the wait before the modal is named, the modal itself is silent, the work and the re-read are named", () => {
  assert.deepEqual(PHASES, ["asking", "confirming", "running", "refreshing"]);
  const install = (phase) => describeActivity({ path: "/mods/install", body: { source: "ssh://git.example/mod.git" }, phase });
  assert.match(install("asking"), /^Reading ssh:\/\/git\.example\/mod\.git and preparing the install — the confirmation opens next…$/);
  assert.equal(install("confirming"), null, "a question on screen is the user's turn, nothing spins");
  assert.equal(install("running"), "Installing ssh://git.example/mod.git…");
  assert.equal(install("refreshing"), "Installing ssh://git.example/mod.git… almost done, re-reading the mod list");
  assert.equal(describeActivity(null), null);
});

test("every typed route the pages call has words; a route with no question has no 'asking' sentence of its own", () => {
  assert.equal(describeActivity({ path: "/mods/alpha/update", body: {}, phase: "asking" }), "Checking the update for alpha — the confirmation opens next…");
  assert.equal(describeActivity({ path: "/mods/alpha/update", body: { now: true }, phase: "running" }), "Updating alpha…");
  assert.equal(describeActivity({ path: "/mods/alpha/enable", body: {}, phase: "asking" }), "Enabling alpha…", "enable asks nothing: its first request IS the work");
  assert.equal(describeActivity({ path: "/mods/a%20b/disable", body: {}, phase: "running" }), "Disabling a b…", "the id is decoded for people");
  assert.equal(describeActivity({ path: "/mods/alpha/uninstall", body: { now: true }, phase: "running" }), "Uninstalling alpha…");
  assert.equal(describeActivity({ path: "/mods/update-all", body: {}, phase: "asking" }), "Checking every update — the confirmation opens next…");
  assert.equal(describeActivity({ path: "/mods/update-check", body: {}, phase: "asking" }), "Asking every source for updates…");
  assert.equal(describeActivity({ path: "/self-update", body: { now: true }, phase: "running" }), "Updating FloofyCrew…");
  assert.equal(describeActivity({ path: "/reload", body: {}, phase: "running" }), "Reloading the Loader…");
  assert.equal(describeActivity({ path: "/registries/refresh", body: {}, phase: "asking" }), "Refreshing the registry…");
  assert.equal(describeActivity({ path: "/registries/internal/refresh", body: {}, phase: "running" }), "Refreshing the registry…");
  assert.equal(describeActivity({ path: "/registries/internal/trust", body: {}, phase: "running" }), "Changing the source's trust…");
  assert.equal(describeActivity({ path: "/registries/internal", body: {}, phase: "running" }), "Removing the source…");
  assert.equal(describeActivity({ path: "/registries", body: { url: "https://x" }, phase: "running" }), "Adding the source…");
  assert.equal(describeActivity({ path: "/registries/defaults", body: {}, phase: "running" }), "Recording the edition's default source…");
  assert.equal(describeActivity({ path: "/profiles/work/use", body: {}, phase: "asking" }), "Planning the profile switch…");
  assert.equal(describeActivity({ path: "/restore", body: { version: "0.7.0.8" }, phase: "running" }), "Restoring the quarantined set for 0.7.0.8…");
  assert.equal(describeActivity({ path: "/consent", body: {}, phase: "running" }), "Working…", "an unknown route still says something");
  assert.equal(wordsFor("/mods/install").running, "Installing the mod…", "no source given: a generic noun, never 'undefined'");
});
