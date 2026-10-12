// Agent Orchestrator — the live hub-and-spoke graph (FloofyCrew `ui` part).
//
// Mounted by the FloofyCrew App with `api = floofy.mod("agent-orchestrator")` (docs/spa-api.md).
// Data: `GET graph?run=&since=` on the mod's own routes every 500 ms (the contract) plus the
// dashboard's own `/api/ws` (same origin, dashboard cookie) for immediate node flicker on
// `chat_status` / `tool_call` frames between polls (an optimisation; the page works without it).
// Route calls go through `api.routes.url(path)` with the query string appended AFTERWARDS:
// `routes.fetch()`/`routes.url()` percent-encode every path segment, a `?` included, so a query
// passed inside the path becomes one bogus segment the Loader rejects (404, no `run` field).
// No third-party library: SVG for edges and travelling dots, absolutely positioned cards for nodes,
// colours from the host's theme tokens so light/dark both work. Never uses window.alert/confirm.

const POLL_MS = 500;
const DOT_MS = 900;
const LOG_MAX = 500;
const KIND_COLOR = { send: "#5ac8fa", say: "#c678dd", ask: "#e5a0ff", answer: "#d19aff", report: "#7ad68a", status: "#8e9aa8", done: "#7ad68a", blocked: "#ff8a65" };
const STATE_GLYPH = { working: "◌", waiting: "◔", done: "✓", blocked: "⚠", needs_input: "?", idle: "○", stalled: "…", gone: "✕" };

export default async function mount(container, api) {
  const tokens = safe(() => api.theme.tokens(), {});
  const t = (name, fallback) => (tokens && tokens[name]) || fallback;
  // The host declares --bg/--bg-elevated/--card/--text/--text-muted/--accent/--border (spa-host KNOWN_TOKENS).
  const FG = t("--text", "#d8dee9"), FG_MUTED = t("--text-muted", "#8e9aa8"), CARD = t("--card", t("--bg-elevated", "#151b21"));
  const css = `
  .ao{font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:${FG};background:${t("--bg", "#101418")};border:1px solid ${t("--border", "#2a3139")};border-radius:10px;overflow:hidden;display:flex;flex-direction:column;min-height:720px;height:calc(100vh - 160px)}
  .ao-bar{display:flex;gap:12px;align-items:center;padding:8px 12px;border-bottom:1px solid ${t("--border", "#2a3139")};background:${t("--bg-elevated", "rgba(255,255,255,.03)")}}
  .ao-bar select,.ao-bar button{font:inherit;color:inherit;background:transparent;border:1px solid ${t("--border", "#2a3139")};border-radius:6px;padding:3px 8px}
  .ao-bar .ao-goal{opacity:.8;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ao-bar .ao-live{color:#7ad68a}.ao-bar .ao-live.off{color:${FG_MUTED}}.ao-bar .ao-live.err{color:#ff8a65}
  .ao-bar .ao-remove{cursor:pointer;opacity:.75}.ao-bar .ao-remove:hover{opacity:1;border-color:#ff8a65;color:#ff8a65}
  .ao-bar .ao-remove[hidden]{display:none}.ao-bar .ao-remove:disabled{opacity:.4;cursor:default}
  .ao-stage{position:relative;flex:1;min-height:440px}
  .ao-stage svg{position:absolute;inset:0;width:100%;height:100%}
  .ao-card{position:absolute;transform:translate(-50%,-50%);min-width:150px;max-width:220px;background:${CARD};border:1px solid ${t("--border", "#2a3139")};border-radius:6px;padding:6px 9px;box-shadow:0 2px 10px rgba(0,0,0,.25);cursor:pointer;transition:border-color .2s}
  .ao-card:hover{border-color:${t("--accent", "#5ac8fa")}}
  .ao-card.hub{border-color:${t("--accent", "#5ac8fa")};min-width:190px}
  .ao-card .n{display:flex;gap:6px;align-items:center;font-weight:600}
  .ao-card .n .glyph{color:#e5a0ff}.ao-card.hub .n .glyph{color:#5ac8fa}
  .ao-card .n .live{margin-left:auto;width:7px;height:7px;border-radius:50%;background:#8e9aa8}
  .ao-card .n .live.live{background:#7ad68a}.ao-card .n .live.quiet{background:#e5c07b}.ao-card .n .live.done{background:#5ac8fa}
  .ao-card .s{display:flex;gap:8px;align-items:baseline;margin-top:2px;opacity:.9}
  .ao-card .s .sum{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ao-card .s .st{opacity:.85}.ao-card .s .pct{opacity:.7;min-width:34px;text-align:right}
  .ao-card .bar{height:2px;background:${t("--border", "#2a3139")};margin-top:5px;border-radius:1px;overflow:hidden}
  .ao-card .bar i{display:block;height:100%;background:#e5a0ff;transition:width .4s}
  .ao-card.done .bar i{background:#7ad68a}.ao-card.blocked .bar i,.ao-card.stalled .bar i{background:#ff8a65}
  .ao-card .meta{opacity:.55;font-size:11px;margin-top:3px;display:flex;gap:8px}
  .ao-card.gone{opacity:.45}
  .ao-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;opacity:.6;text-align:center;padding:24px}
  .ao-log{border-top:1px solid ${t("--border", "#2a3139")};height:170px;overflow:auto;padding:6px 12px;flex:none}
  .ao-log h4{margin:0 0 4px;font-weight:500;opacity:.6}
  .ao-log .row{display:grid;grid-template-columns:92px 190px 1fr;gap:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .ao-log .row .ts{opacity:.45}.ao-log .row .who{opacity:.9}.ao-log .row .tx{overflow:hidden;text-overflow:ellipsis}
  .ao-foot{padding:6px 12px;border-top:1px solid ${t("--border", "#2a3139")};opacity:.7;display:flex;gap:16px}
  @keyframes ao-spin{to{transform:rotate(360deg)}}
  .ao-card .st.spin{display:inline-block;animation:ao-spin 1.2s linear infinite}
  @media (prefers-reduced-motion: reduce){.ao-card .st.spin{animation:none}}
  `;
  const style = el("style", {}, css);
  const root = el("div", { class: "ao" });
  const bar = el("div", { class: "ao-bar" });
  const runSel = el("select", { title: "run" });
  const goal = el("span", { class: "ao-goal" });
  const live = el("span", { class: "ao-live off" }, "● connecting");
  const counters = el("span", {}, "");
  // Shown only for a run whose tabs are all closed or deleted: deletes its record (bus log, statuses, meta).
  // A run whose orchestrator chat is DELETED for good disappears by itself (the hook follows the host's delete);
  // this is for the closed-not-deleted case, which the host keeps in history and we would otherwise keep forever.
  const removeBtn = el("button", { type: "button", class: "ao-remove", hidden: "", title: "delete this run's record (the chats themselves are not touched)", "data-testid": "ao-remove" }, "✕ remove");
  bar.append(runSel, goal, counters, live, removeBtn);
  const stage = el("div", { class: "ao-stage" });
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const edgesG = document.createElementNS("http://www.w3.org/2000/svg", "g");
  const dotsG = document.createElementNS("http://www.w3.org/2000/svg", "g");
  svg.append(edgesG, dotsG);
  const cards = el("div");
  stage.append(svg, cards);
  const log = el("div", { class: "ao-log" });
  const logTitle = el("h4", {}, "message bus");
  const logRows = el("div");
  log.append(logTitle, logRows);
  const foot = el("div", { class: "ao-foot" });
  root.append(bar, stage, log, foot);
  container.append(style, root);

  const state = { run: "", cursor: "", nodes: new Map(), snap: null, runs: [], dots: [], logCount: 0, ws: null, timer: null, dead: false, filter: "" };
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  runSel.addEventListener("change", () => { state.run = runSel.value; state.cursor = ""; logRows.textContent = ""; state.logCount = 0; state.allMessages = []; state.snap = null; refresh(); });

  removeBtn.addEventListener("click", async () => {
    const run = state.run; if (!run || removeBtn.disabled) return;
    const title = (state.runs.find((r) => r.run === run) || {}).title || run;
    const sure = api.dialog && api.dialog.confirm
      ? await api.dialog.confirm(`Remove the record of “${title}”? Its bus log and status lines are deleted; the chats themselves are not touched.`, { okLabel: "Remove", cancelLabel: "Keep", danger: true })
      : true;
    if (!sure) return;
    removeBtn.disabled = true; removeBtn.textContent = "removing…";
    try {
      await postJson("runs/remove", { run });
      state.run = ""; state.cursor = ""; state.snap = null; state.allMessages = []; logRows.textContent = ""; state.logCount = 0;
      await loadRuns();
      await refresh();
    } catch (e) {
      live.textContent = "● remove failed"; live.className = "ao-live err"; live.title = String(e && e.message || e);
      api.log.warn("runs/remove failed", e);
    } finally { removeBtn.disabled = false; removeBtn.textContent = "✕ remove"; }
  });

  async function postJson(path, body) {
    const response = await api.routes.fetch(path, { method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body) });
    let out = null; try { out = await response.json(); } catch { out = null; }
    if (!response.ok) throw new Error((out && out.error) || `HTTP ${response.status} from ${path}`);
    return out;
  }

  // One of the mod's routes as JSON. `routes.url()` encodes each PATH segment; the query is appended after
  // it (passing `graph?run=…` as the path would encode the `?` and produce a segment the Loader rejects).
  async function getJson(path, query) {
    const qs = query ? String(query) : "";
    const url = api.routes.url(path) + (qs ? `?${qs}` : "");
    const response = await fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } });
    let body = null;
    try { body = await response.json(); } catch { body = null; }
    if (!response.ok) throw new Error((body && body.error) || `HTTP ${response.status} from ${path}`);
    return body;
  }

  async function loadRuns() {
    const r = await getJson("runs").catch((e) => { api.log.warn("runs failed", e); return { runs: [] }; });
    state.runs = r.runs || [];
    const cur = runSel.value;
    runSel.textContent = "";
    for (const run of state.runs) {
      const o = el("option", { value: run.run }, `${run.live ? "● " : "○ "}${run.title || run.run}${run.closed ? " (closed)" : ""} · ${run.workers} workers`);
      runSel.append(o);
    }
    if (!state.runs.length) runSel.append(el("option", { value: "" }, "no orchestration runs yet"));
    if (cur && state.runs.some((r) => r.run === cur)) runSel.value = cur;
    if (state.run && !state.runs.some((r) => r.run === state.run)) {
      // the run on screen was removed (its orchestrator chat deleted, or swept): fall to the next one, not a ghost
      state.run = ""; state.cursor = ""; state.snap = null; state.allMessages = []; logRows.textContent = ""; state.logCount = 0;
    }
    if (!state.run && state.runs.length) state.run = state.runs[0].run;
    if (state.run) runSel.value = state.run;
    if (!state.run) { render(null); live.textContent = "● idle"; live.className = "ao-live off"; removeBtn.hidden = true; }
  }

  async function refresh() {
    if (state.dead) return;
    try {
      const q = new URLSearchParams();
      if (state.run) q.set("run", state.run);
      if (state.cursor) q.set("since", state.cursor);
      const snap = await getJson("graph", q.toString());
      if (!snap || !snap.run) {
        // the hook knows no run yet (nothing has spawned a worker); a route error is thrown above, not swallowed here
        render(null); live.textContent = "● idle"; live.className = "ao-live off"; removeBtn.hidden = true; return;
      }
      if (!state.run) { state.run = snap.run; await loadRuns(); }
      for (const m of snap.messages || []) appendLog(m, true);
      if (snap.cursor) state.cursor = snap.cursor;
      state.snap = snap;
      render(snap);
      // `sessions_gone`: every tab of the run is closed or deleted (the reducer may still remember the nodes, or
      // the run is drawn from disk). Such a run is archived and removable; a run with an open tab never is.
      const gone = Boolean(snap.sessions_gone || snap.from_disk);
      live.textContent = gone ? (snap.closed ? "○ closed · archived (chats gone)" : "○ archived (chats gone)") : snap.closed ? "✓ closed" : "● live";
      live.className = "ao-live" + (snap.closed || gone ? " off" : "");
      removeBtn.hidden = !gone;
    } catch (e) {
      live.textContent = "● offline"; live.className = "ao-live err"; live.title = String(e && e.message || e);
      if (!state.snap) render(null, `The graph route failed: ${e && e.message || e}`);
      api.log.warn("graph refresh failed", e);
    }
  }

  function render(snap, problem) {
    cards.textContent = ""; edgesG.textContent = "";
    const q = stage.querySelector(".ao-empty"); if (q) q.remove();
    if (!snap || !snap.orchestrator) {
      stage.append(el("div", { class: "ao-empty" }, problem || "No run on screen. Start a chat with the agent “agent-orchestrator” and give it a goal — its workers and the bus appear here as they are created."));
      foot.textContent = ""; goal.textContent = ""; counters.textContent = "";
      return;
    }
    const W = stage.clientWidth || 800, H = stage.clientHeight || 420;
    const cx = W / 2, cy = H / 2;
    const workers = snap.workers || [];
    const n = workers.length;
    const compact = n > 8;
    // An ellipse, not a circle: cards are wide and the stage is wider than it is tall, so spend the
    // horizontal room first. Radii stay inside the stage with a margin for the card's own box.
    const cardW = compact ? 120 : 230, cardH = compact ? 36 : 76;
    const rx = Math.max(140, Math.min(W * (n <= 4 ? 0.30 : 0.40), W / 2 - cardW / 2 - 8));
    const ry = Math.max(90, Math.min(H * (n <= 4 ? 0.30 : 0.40), H / 2 - cardH / 2 - 8));
    const pos = new Map();
    pos.set("orchestrator", { x: cx, y: cy });
    workers.forEach((w, i) => {
      const a = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(n, 1);
      pos.set(w.nick, { x: cx + rx * Math.cos(a), y: cy + ry * Math.sin(a) });
    });
    state.pos = pos;
    for (const w of workers) drawEdge(pos.get("orchestrator"), pos.get(w.nick), w.state === "gone" ? 0.25 : 0.6, false);
    for (const e of peerEdges(snap)) if (pos.get(e[0]) && pos.get(e[1])) drawEdge(pos.get(e[0]), pos.get(e[1]), 0.35, true);
    cards.append(card(snap.orchestrator, pos.get("orchestrator"), true, snap));
    for (const w of workers) cards.append(card(w, pos.get(w.nick), false, snap, compact));
    goal.textContent = snap.goal ? `goal: ${snap.goal}` : (snap.orchestrator.title || "");
    const c = snap.counters || {};
    counters.textContent = `workers ${c.workers ?? n} · done ${c.done ?? 0} · msgs ${snap.message_total ?? 0}`;
    logTitle.textContent = `message bus — orchestrator ⇄ ${n} agent${n === 1 ? "" : "s"}${state.filter ? ` · filter: ${state.filter} (click again to clear)` : ""}`;
    foot.textContent = "";
    foot.append(el("span", {}, `${n} agent${n === 1 ? "" : "s"}${snap.goal ? " " + snap.goal : ""} · ${snap.message_total ?? 0} messages on the bus`),
      el("span", {}, `done ${c.done ?? 0}/${n}` + (c.needs_input ? ` · needs input ${c.needs_input}` : "")));
  }

  function peerEdges(snap) {
    // Chords are remembered across polls: a poll after the cursor carries only NEW messages.
    const seen = new Set(), out = [];
    for (const m of (state.allMessages || snap.messages || [])) {
      if (m.from === "orchestrator" || m.to === "orchestrator" || m.to === "*") continue;
      const k = [m.from, m.to].sort().join("|");
      if (!seen.has(k)) { seen.add(k); out.push([m.from, m.to]); }
    }
    return out;
  }

  function drawEdge(a, b, opacity, peer) {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", a.x); line.setAttribute("y1", a.y); line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
    line.setAttribute("stroke", peer ? "#c678dd" : FG_MUTED);
    line.setAttribute("stroke-opacity", String(opacity));
    line.setAttribute("stroke-width", "1");
    line.setAttribute("stroke-dasharray", peer ? "2 6" : "3 7");
    edgesG.append(line);
  }

  function card(node, p, hub, snap, compact) {
    const d = el("div", { class: `ao-card${hub ? " hub" : ""} ${node.state}` });
    d.style.left = `${p.x}px`; d.style.top = `${p.y}px`;
    const glyph = hub ? "◆" : "✱";
    const name = hub ? "orchestrator" : node.nick || node.title;
    const head = el("div", { class: "n" }, el("span", { class: "glyph" }, glyph), el("span", {}, name), el("span", { class: `live ${node.liveness}`, title: `liveness: ${node.liveness}` }));
    d.append(head);
    if (hub) {
      const n = (snap.workers || []).length;
      d.append(el("div", { class: "s" }, el("span", { class: "sum" }, `you · ${n} worker${n === 1 ? "" : "s"}`)));
      const st = node.state === "working" ? "● leading" : node.state === "waiting" ? "○ waiting" : node.state === "needs_input" ? "? needs you" : snap.closed ? "✓ closed" : "○ idle";
      d.append(el("div", { class: "meta" }, el("span", {}, st), el("span", {}, node.summary ? trunc(node.summary, 48) : "")));
    } else {
      const g = STATE_GLYPH[node.state] || "○";
      const stEl = el("span", { class: `st${node.state === "working" ? " spin" : ""}` }, g);
      const pct = node.progress == null ? "" : `${node.progress}%`;
      d.append(el("div", { class: "s" }, el("span", { class: "sum", title: node.summary || "" }, shortTitle(node) ), stEl, el("span", { class: "pct" }, pct)));
      if (!compact) {
        const bar = el("div", { class: "bar" }, el("i"));
        bar.firstChild.style.width = `${node.progress == null ? 0 : node.progress}%`;
        d.append(bar);
        d.append(el("div", { class: "meta" }, el("span", { title: node.summary_source || "" }, trunc(node.summary || "", 60)), el("span", {}, age(node.age_secs))));
      }
    }
    d.title = `${node.title || ""}\n${node.agent || ""}${node.model ? " · " + node.model : ""}\n${node.summary || ""}\nclick: open tab · shift-click: filter the bus log`;
    d.addEventListener("click", (ev) => {
      if (ev.shiftKey) { state.filter = state.filter === name ? "" : name; rerenderLog(); render(state.snap); return; }
      if (node.href) location.assign(node.href);
    });
    return d;
  }

  function shortTitle(node) {
    const tt = (node.title || "").replace(/^worker-\d+:\s*/i, "");
    return trunc(tt || node.nick || "", 22);
  }

  function appendLog(m, animate) {
    if (state.filter && m.from !== state.filter && m.to !== state.filter) { state.pending = state.pending || []; }
    const row = el("div", { class: "row", "data-from": m.from, "data-to": m.to },
      el("span", { class: "ts" }, hms(m.ts)),
      el("span", { class: "who" }, coloured(m.from), " → ", coloured(m.to)),
      el("span", { class: "tx", title: m.text }, `${kindGlyph(m.kind)} ${oneLine(stripProvenance(m.text))}`));
    if (state.filter && m.from !== state.filter && m.to !== state.filter) row.style.display = "none";
    logRows.append(row);
    state.logCount++;
    while (logRows.children.length > LOG_MAX) logRows.firstChild.remove();
    if (!log.matches(":hover")) log.scrollTop = log.scrollHeight;
    state.allMessages = state.allMessages || []; state.allMessages.push(m); if (state.allMessages.length > LOG_MAX) state.allMessages.shift();
    if (animate && state.pos) launchDot(m);
  }

  function rerenderLog() {
    for (const row of logRows.children) {
      const show = !state.filter || row.dataset.from === state.filter || row.dataset.to === state.filter;
      row.style.display = show ? "" : "none";
    }
  }

  function launchDot(m) {
    const from = state.pos.get(m.from), to = m.to === "*" ? null : state.pos.get(m.to);
    const targets = to ? [to] : [...state.pos.entries()].filter(([k]) => k !== "orchestrator").map(([, v]) => v);
    if (!from) return;
    for (const tgt of targets) {
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("r", "3.2");
      dot.setAttribute("fill", KIND_COLOR[m.kind] || "#5ac8fa");
      dotsG.append(dot);
      if (reduced) { dot.setAttribute("cx", tgt.x); dot.setAttribute("cy", tgt.y); setTimeout(() => dot.remove(), 600); continue; }
      const t0 = performance.now();
      const step = (now) => {
        const k = Math.min(1, (now - t0) / DOT_MS);
        const e = k < 0.5 ? 2 * k * k : -1 + (4 - 2 * k) * k;
        dot.setAttribute("cx", from.x + (tgt.x - from.x) * e);
        dot.setAttribute("cy", from.y + (tgt.y - from.y) * e);
        dot.setAttribute("fill-opacity", String(1 - Math.max(0, k - 0.8) * 5));
        if (k < 1 && !state.dead) requestAnimationFrame(step); else dot.remove();
      };
      requestAnimationFrame(step);
    }
  }

  // Host WS: flicker a node's liveness dot immediately on activity, without waiting for the poll.
  function openWs() {
    try {
      const proto = location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${proto}//${location.host}/api/ws`);
      ws.onmessage = (ev) => {
        let f; try { f = JSON.parse(ev.data); } catch { return; }
        const d = f && f.data; if (!d || !d.slot || !state.snap) return;
        const node = [state.snap.orchestrator, ...(state.snap.workers || [])].find((n) => n && n.slot === d.slot);
        if (!node) return;
        if (f.type === "tool_call" || f.type === "chat_status" || f.type === "chat_segment") {
          const c = [...cards.children].find((c) => c.querySelector(".n span:nth-child(2)")?.textContent === (node.role === "orchestrator" ? "orchestrator" : node.nick));
          const dot = c && c.querySelector(".live"); if (dot) { dot.className = "live live"; }
        }
      };
      ws.onclose = () => { state.ws = null; if (!state.dead) setTimeout(openWs, 5000); };
      state.ws = ws;
    } catch (e) { api.log.debug("ws unavailable", e); }
  }

  function coloured(name) {
    const s = el("span", {}, name);
    s.style.color = name === "orchestrator" ? "#5ac8fa" : name === "*" ? "#8e9aa8" : hashColor(name);
    return s;
  }

  await loadRuns();
  await refresh();
  openWs();
  state.timer = setInterval(refresh, POLL_MS);
  const runsTimer = setInterval(loadRuns, 5000);
  const ro = new ResizeObserver(() => render(state.snap));
  ro.observe(stage);

  return () => {
    state.dead = true;
    clearInterval(state.timer); clearInterval(runsTimer); ro.disconnect();
    if (state.ws) try { state.ws.close(); } catch {}
    container.textContent = "";
  };
}

// --- helpers ------------------------------------------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  for (const c of children) if (c != null) e.append(c);
  return e;
}
function safe(fn, fallback) { try { return fn(); } catch { return fallback; } }
function trunc(s, n) { s = String(s || ""); return s.length <= n ? s : s.slice(0, n - 1) + "…"; }
function oneLine(s) { return String(s || "").replace(/\s+/g, " ").trim(); }
function stripProvenance(s) { return String(s || "").replace(/^\[bus message from[^\]]*\]\s*/, ""); }
function hms(ts) { const d = new Date((ts || 0) * 1000); return `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}.${String(d.getMilliseconds()).padStart(3, "0")}`; }
function p2(n) { return String(n).padStart(2, "0"); }
function age(secs) { secs = Math.floor(secs || 0); if (secs < 60) return `${secs}s`; const m = Math.floor(secs / 60); if (m < 60) return `${m}m ${secs % 60}s`; return `${Math.floor(m / 60)}h ${m % 60}m`; }
function kindGlyph(k) { return { ask: "?", answer: "↩", report: "✓", send: "⇢", say: "·", status: "~", done: "✓", blocked: "⚠" }[k] || "·"; }
function hashColor(name) {
  let h = 0; for (const ch of String(name)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  const palette = ["#e5a0ff", "#ffb86b", "#7ad68a", "#f28fad", "#8be9fd", "#f1fa8c", "#ff9f80", "#a3d9ff"];
  return palette[h % palette.length];
}
