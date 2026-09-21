// The manager App's protocol client (`ui/app/api.mjs`) under plain `node --test` (task 11.3; Requirement 16.3).
//
// `callRoute` speaks the Loader's 409 protocol: a question is put to the asker,
// its answer is merged into `confirmations` (lists accumulate), a `$body`
// patch changes the request itself (the staged-vs-apply-now choice), `null`
// stops with `declined` and NOTHING is re-posted; no answer is ever invented.
import assert from "node:assert/strict";
import { test } from "node:test";

import { browserAsk, callRoute, mergeConfirmations, transcriptOf, waitForGateway } from "../../ui/app/api.mjs";

function fakeLoader(script) {
  // script: a function (body, round) -> {status, body}; records every request
  const calls = [];
  const fetchImpl = async (path, init) => {
    const body = JSON.parse(init.body);
    calls.push({ path, method: init.method, body });
    const reply = script(body, calls.length);
    return { status: reply.status, ok: reply.status >= 200 && reply.status < 300, body: reply.body };
  };
  return { calls, fetchImpl };
}

test("mergeConfirmations accumulates lists and replaces scalars", () => {
  const merged = mergeConfirmations({ yes: ["a"], consent: false }, { yes: ["b", "a"], consent: true, governanceTargets: ["x"] });
  assert.deepEqual(merged, { yes: ["a", "b"], consent: true, governanceTargets: ["x"] });
});

test("a 409 question goes to the asker and the answer is re-posted; a 200 ends the round trip", async () => {
  const { calls, fetchImpl } = fakeLoader((body, round) => {
    if (!body.confirmations || body.confirmations.yes === undefined) return { status: 409, body: { ok: false, confirmation: { kind: "yes-no", text: "Uninstall alpha 1.0.0?", expects: "confirm", targets: [], detail: "missing" }, argv: ["uninstall", "alpha", "--now"] } };
    return { status: 200, body: { ok: true, argv: ["uninstall", "alpha", "--now"], transcript: ["alpha: removed."], reloaded: true } };
  });
  const asked = [];
  const result = await callRoute("/mods/alpha/uninstall", { now: true }, { fetchImpl, ask: async (q) => { asked.push(q.kind); return { yes: [q.text] }; } });
  assert.equal(result.ok, true);
  assert.deepEqual(asked, ["yes-no"]);
  assert.equal(calls.length, 2);
  assert.deepEqual(calls[1].body, { now: true, confirmations: { yes: ["Uninstall alpha 1.0.0?"] } });
  assert.match(transcriptOf(result, "/mods/alpha/uninstall"), /\$ floofy uninstall alpha --now\nalpha: removed\.\n\(Loader reloaded\)/);
});

test("null from the asker stops: declined, nothing re-posted, nothing invented", async () => {
  const { calls, fetchImpl } = fakeLoader(() => ({ status: 409, body: { ok: false, confirmation: { kind: "consent", text: "WARNING", expects: "agree", targets: [], detail: "missing" }, argv: ["enable", "alpha"] } }));
  const result = await callRoute("/mods/alpha/enable", {}, { fetchImpl, ask: async () => null });
  assert.equal(result.ok, false);
  assert.equal(result.declined, true);
  assert.equal(result.needs.kind, "consent");
  assert.equal(calls.length, 1, "no second request without an answer");
  assert.deepEqual(calls[0].body, { confirmations: {} });
  assert.match(transcriptOf(result, "/mods/alpha/enable"), /not confirmed \(consent\); nothing was changed/);
});

test("several questions accumulate; a $body patch changes the request (apply now)", async () => {
  const { calls, fetchImpl } = fakeLoader((body) => {
    const c = body.confirmations || {};
    if (!c.yes) return { status: 409, body: { ok: false, confirmation: { kind: "yes-no", text: "This mod has python-hook parts. Install govy 1.0.0?", expects: "confirm", targets: [], detail: "missing" }, argv: ["install", "/src/govy"], disclosure: { id: "govy", governanceTargets: ["security_policy.json"] } } };
    if (!c.governanceTargets || !c.governanceTargets.includes("security_policy.json")) return { status: 409, body: { ok: false, confirmation: { kind: "governance-target", text: "Type the exact path", expects: "typed-path", targets: ["security_policy.json"], detail: c.governanceTargets ? "mismatch" : "missing" }, argv: ["install", "/src/govy", "--now"] } };
    return { status: 200, body: { ok: true, argv: ["install", "/src/govy", "--now"], transcript: [], json: { placed: { how: "installed" } } } };
  });
  const seen = [];
  const answers = [{ yes: ["This mod has python-hook parts. Install govy 1.0.0?"], $body: { now: true } }, { governanceTargets: ["security_policy"] }, { governanceTargets: ["security_policy.json"] }];
  const result = await callRoute("/mods/install", { source: "/src/govy" }, { fetchImpl, ask: async (q) => { seen.push(`${q.kind}:${q.detail}`); return answers.shift(); } });
  assert.equal(result.ok, true);
  assert.deepEqual(seen, ["yes-no:missing", "governance-target:missing", "governance-target:mismatch"]);
  assert.equal(calls.length, 4);
  assert.equal(calls[1].body.now, true, "the staging choice patched the request");
  assert.deepEqual(calls[3].body.confirmations, { yes: ["This mod has python-hook parts. Install govy 1.0.0?"], governanceTargets: ["security_policy", "security_policy.json"] });
  for (const call of calls) assert.equal(JSON.stringify(call.body).includes("--yes"), false);
});

test("the browser asker knows only yes/no through confirm(); every other kind is left unanswered", async () => {
  globalThis.window = { confirm: () => true };
  try {
    assert.deepEqual(browserAsk({ kind: "yes-no", text: "Sure?" }), { yes: ["Sure?"] });
    assert.equal(browserAsk({ kind: "consent", text: "WARNING" }), null);
    assert.equal(browserAsk({ kind: "governance-target", text: "Type", targets: ["x"] }), null);
    assert.equal(browserAsk({ kind: "unlisted-source", text: "Type I ACCEPT" }), null);
    assert.equal(browserAsk({ kind: "unsigned-index", text: "TRUST LOOSENED" }), null);
  } finally {
    delete globalThis.window;
  }
});

test("the round trip gives up after MAX_ROUNDS instead of looping", async () => {
  const { calls, fetchImpl } = fakeLoader(() => ({ status: 409, body: { ok: false, confirmation: { kind: "yes-no", text: "again?", expects: "confirm", targets: [], detail: "missing" } } }));
  const result = await callRoute("/x", {}, { fetchImpl, ask: async () => ({ yes: true }) });
  assert.equal(result.ok, false);
  assert.match(result.error, /too many confirmation rounds/);
  assert.equal(calls.length, 8);
});


// --- the restart wait (1.1.6) -------------------------------------------------------------------------------------

function clock() {
  // a fake clock: `sleep` advances it, `now` reads it, so the poll loop runs without real time
  let t = 0;
  return { now: () => t, sleep: async (ms) => { t += ms; } };
}

test("waitForGateway resolves true once /health went away and answers again", async () => {
  const { now, sleep } = clock();
  const replies = [{ ok: true, body: { bootedAt: "t0" } }, null, null, { ok: true, body: { bootedAt: "t1" } }];
  let i = 0;
  const back = await waitForGateway({ fetchImpl: async () => { const r = replies[Math.min(i, replies.length - 1)]; i += 1; if (r === null) throw new Error("ECONNREFUSED"); return r; }, now, sleep, timeoutMs: 60000, intervalMs: 1000 });
  assert.equal(back, true);
  assert.equal(i, 4);
});

test("waitForGateway resolves true on a new bootedAt even when no poll saw the gateway down", async () => {
  const { now, sleep } = clock();
  const replies = [{ ok: true, body: { bootedAt: "t0" } }, { ok: true, body: { bootedAt: "t0" } }, { ok: true, body: { bootedAt: "t9" } }];
  let i = 0;
  const back = await waitForGateway({ fetchImpl: async () => replies[Math.min(i++, replies.length - 1)], now, sleep, timeoutMs: 60000, intervalMs: 1000 });
  assert.equal(back, true);
  assert.equal(i, 3);
});

test("waitForGateway resolves false when the same gateway keeps answering until the budget runs out", async () => {
  const { now, sleep } = clock();
  let polls = 0;
  const back = await waitForGateway({ fetchImpl: async () => { polls += 1; return { ok: true, body: { bootedAt: "t0" } }; }, now, sleep, timeoutMs: 10000, intervalMs: 1000 });
  assert.equal(back, false);
  assert.equal(polls, 10);
});
