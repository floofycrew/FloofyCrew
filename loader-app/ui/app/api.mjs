// FloofyCrew manager App — the Loader API client and the confirmation protocol (Requirement 16.2, 16.3; task 11.1–11.3).
//
// Every call is same-origin (`/api/apps/floofycrew/...`, the dashboard cookie);
// nothing here contacts another origin (Requirement 4.6). `callRoute()` speaks
// the 409 protocol of `floofy_loader.app_routes`: a route that needs a
// confirmation the request did not carry answers 409 with `confirmation`
// `{kind, text, expects, targets, detail}`; the caller's `ask(question, reply)`
// returns the answers to merge into `confirmations` (`{yes: [prompt]}`,
// `{consent: true}`, `{governanceTargets: [typed]}`, `{unlistedSource: "I ACCEPT"}`,
// `{unsignedIndex: true}`) or `null` to stop. The default asker only knows the
// browser's `confirm()` for a yes/no question; the App's modals (`confirm.mjs`)
// answer every kind. Nothing is ever answered without the user: there is no
// `--yes` on this surface, by design.
"use strict";

export const API = "/api/apps/floofycrew";
export const MAX_ROUNDS = 8;

export async function apiFetch(path, options = {}) {
  const response = await fetch(`${API}${path}`, { credentials: "same-origin", ...options });
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  return { status: response.status, ok: response.ok, body };
}

/** Run one READ-ONLY floofy command in-process (POST /cli). */
export async function runCli(argv) {
  const reply = await apiFetch("/cli", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ argv }) });
  const body = reply.body || {};
  return { ...body, httpStatus: reply.status, ok: reply.ok && body.ok !== false };
}

/** Merge one answer into the confirmations object (lists accumulate, scalars replace). */
export function mergeConfirmations(confirmations, answer) {
  const out = { ...confirmations };
  for (const [key, value] of Object.entries(answer || {})) {
    if (Array.isArray(value)) {
      const previous = Array.isArray(out[key]) ? out[key] : [];
      out[key] = [...previous, ...value.filter((v) => !previous.includes(v))];
    } else if (value !== undefined) {
      out[key] = value;
    }
  }
  return out;
}

/** The browser-only asker: `confirm()` for a yes/no question, nothing for the rest (the modals of confirm.mjs take over). */
export function browserAsk(question) {
  if (question.kind === "yes-no" && typeof window !== "undefined" && typeof window.confirm === "function") {
    return window.confirm(question.text) ? { yes: [question.text] } : null;
  }
  return null;
}

/**
 * Call one typed action route with the 409 confirmation protocol.
 * Resolves to the route's document plus `httpStatus`, `ok`; `declined: true` when the user stopped at a question
 * (`needs` carries it), `needs` alone when no asker could present the question.
 */
export async function callRoute(path, body = {}, { method = "POST", ask = browserAsk, fetchImpl = apiFetch } = {}) {
  let request = { ...body };
  let confirmations = { ...(body.confirmations || {}) };
  for (let round = 0; round < MAX_ROUNDS; round += 1) {
    const reply = await fetchImpl(path, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...request, confirmations }) });
    const document = reply.body || {};
    if (reply.status !== 409 || !document.confirmation) return { ...document, httpStatus: reply.status, ok: reply.ok && document.ok !== false };
    const question = document.confirmation;
    const answer = await ask(question, document);
    if (!answer) return { ...document, httpStatus: reply.status, ok: false, declined: true, needs: question, error: `not confirmed: ${question.kind}` };
    // `$body` patches the request itself (the staged-vs-apply-now choice made on the disclosure); the rest are confirmations
    const { $body, ...rest } = answer;
    if ($body && typeof $body === "object") request = { ...request, ...$body };
    confirmations = mergeConfirmations(confirmations, rest);
  }
  return { ok: false, httpStatus: 0, error: "too many confirmation rounds" };
}

/** A route's document as one block of text for the output pane. */
export function transcriptOf(result, path) {
  const argv = Array.isArray(result.argv) ? result.argv : [];
  return [argv.length ? `$ floofy ${argv.join(" ")}` : `→ ${path}`, ...(result.transcript || []), result.error && !result.declined ? `error: ${result.error}` : "", result.declined && result.needs ? `not confirmed (${result.needs.kind}); nothing was changed` : "", result.reloaded ? "(Loader reloaded)" : ""].filter(Boolean).join("\n");
}

export const getState = () => apiFetch("/state");
export const getHealth = () => apiFetch("/health");
export const getRegistry = () => apiFetch("/registry");
export const getStatus = () => runCli(["status"]);
export const reload = () => apiFetch("/reload", { method: "POST" });

/**
 * After `POST /host/restart` was accepted: wait for the gateway to go away and come back, polling the Loader's
 * `/health`. Resolves `true` when a fresh gateway answers — the old one stopped answering in between, or the Loader
 * reports a different `bootedAt` — and `false` when nothing new answered within the budget (the shell then stops
 * saying "restarting" and leaves the terminal hint). Same-origin only, like everything else here.
 */
export async function waitForGateway({ fetchImpl = getHealth, timeoutMs = 120000, intervalMs = 1500, sleep = (ms) => new Promise((r) => setTimeout(r, ms)), now = () => Date.now() } = {}) {
  const started = now();
  let wentAway = false;
  let firstBootedAt = null;
  while (now() - started < timeoutMs) {
    await sleep(intervalMs);
    let reply = null;
    try {
      reply = await fetchImpl();
    } catch {
      reply = null;
    }
    if (!reply || !reply.ok) {
      wentAway = true;
      continue;
    }
    const bootedAt = (reply.body && reply.body.bootedAt) || "";
    if (firstBootedAt === null) firstBootedAt = bootedAt;
    if (wentAway || (bootedAt && bootedAt !== firstBootedAt)) return true;
  }
  return false;
}
