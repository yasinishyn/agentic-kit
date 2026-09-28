/* Live run indicator (architecture §6, PRD-03): card state from GET /api/projects/<id>/runs, refreshed on the event
   stream (run.changed, handoff.*, board.changed) and every few seconds for the "12 s ago" text. States: running
   ("working · Architect · 12 s ago"), waiting ("waiting for you"), queued (hand-off not picked up yet), stale
   ("no signal 20 min"), failed/abandoned (reason). State changes are announced in the board's aria-live region;
   prefers-reduced-motion gets static icons (runs.css). Every dynamic string is set with textContent.
   After the pickup timeout the card offers "Run headless" (PRD-04's runner) and "Copy prompt"; a live headless run
   offers "Stop" (UI token), and permission_denials of a headless run show as a badge. run.event / run.changed are
   forwarded as a "kanban:run-event" window event for the transcript panel (transcript.js). A headless run that ended
   after kanban_finish(needs_input) shows an amber "needs input: <needs_input_summary>" badge (announced) until the
   ticket is moved or a new run starts. */
(function () {
  "use strict";

  const K = window.kanban;
  const B = window.KanbanBoard;
  if (!K || !B) return;
  const el = B.el;
  const TICK_MS = 5000;
  const LIVE = ["queued", "running", "waiting"];
  const state = {project: null, stream: null, runs: new Map(), handoffs: new Map(), stale: 900, skew: 0,
                 announced: new Map(), actionKeys: ""};
  let timer = null;

  const nowSec = () => Date.now() / 1000 + state.skew;
  const projectPath = (suffix) => `/api/projects/${encodeURIComponent(state.project)}${suffix}`;

  function stageLabel(stage) {
    const board = K.board();
    const hit = board && board.stages ? board.stages.find((s) => s[0] === stage) : null;
    return hit ? hit[1] : stage;
  }

  function ago(t) {
    const s = Math.max(0, Math.round(nowSec() - Number(t || 0)));
    if (s < 60) return `${s} s ago`;
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    return `${Math.round(s / 3600)} h ago`;
  }

  function silence(t) {
    const m = Math.max(1, Math.round((nowSec() - Number(t || 0)) / 60));
    return m < 120 ? `${m} min` : `${Math.round(m / 60)} h`;
  }

  /* {cls, text, label} for a ticket, or null when nothing is going on. */
  function describe(ticketId) {
    const run = state.runs.get(ticketId);
    if (run && LIVE.includes(run.status)) {
      const beat = run.heartbeat_at || run.started_at;
      const stale = run.status !== "queued" && beat && nowSec() - beat > state.stale;
      if (stale) return {cls: "stale", text: `no signal ${silence(beat)}`};
      if (run.status === "waiting") return {cls: "waiting", text: "waiting for you"};
      if (run.status === "queued") return {cls: "queued", text: `queued · ${stageLabel(run.stage)}`};
      const who = run.kind === "headless" ? "headless" : "working";
      return {cls: "running", text: `${who} · ${stageLabel(run.stage)} · ${ago(beat)}`,
              title: run.note || ""};
    }
    if (run && run.needs_input) {  // a headless run ended while waiting: the question stays on the card
      return {cls: "needs-input", text: `needs input: ${run.needs_input_summary || ""}`,
              title: "The headless run asked for input and then ended; answer it in a session or move the ticket"};
    }
    const handoff = state.handoffs.get(ticketId);
    if (handoff) {
      const extra = handoff.pickup === "offer" ? " · no session picked it up"
        : handoff.pickup === "headless" ? " · headless run requested" : "";
      return {cls: "queued", text: `queued · ${stageLabel(handoff.stage)}${extra}`};
    }
    if (run && ["failed", "abandoned", "cancelled"].includes(run.status)) {
      return {cls: run.status, text: `${run.status}${run.reason ? " · " + run.reason : ""}`};
    }
    return null;
  }

  function paint(card, ticketId) {
    const old = card.querySelector(".run-indicator");
    if (old) old.remove();
    card.classList.remove("run-live", "run-card-waiting");
    for (const li of card.querySelectorAll(".st.st-live")) li.classList.remove("st-live");
    const d = describe(ticketId);
    const run = state.runs.get(ticketId);
    const denials = run ? Number(run.permission_denials || 0) : 0;
    if (!d && !denials) return;
    const box = el("div", {className: `run-indicator run-${d ? d.cls : "none"}`, title: d ? d.title || null : null},
                   d ? [el("span", {className: "run-icon", "aria-hidden": "true"}),
                        el("span", {className: "run-text", text: d.text})] : []);
    if (denials) {
      box.append(el("span", {className: "run-denials", text: `${denials} permission denial${denials > 1 ? "s" : ""}`,
                             title: "Tools were refused in the headless run; open the card to read the transcript"}));
    }
    const head = card.querySelector(".card-head");
    if (head && head.nextSibling) card.insertBefore(box, head.nextSibling);
    else card.append(box);
    if (d && d.cls === "running") card.classList.add("run-live");
    if (d && d.cls === "waiting") card.classList.add("run-card-waiting");
    if (run && run.subtask && LIVE.includes(run.status)) {
      for (const title of card.querySelectorAll(".st-title")) {
        if (title.getAttribute("title") === run.subtask) title.closest(".st").classList.add("st-live");
      }
    }
  }

  function paintAll(announce) {
    for (const card of document.querySelectorAll(".card[data-ticket]")) paint(card, card.dataset.ticket);
    if (!announce) return;
    const tickets = new Set([...state.runs.keys(), ...state.handoffs.keys(), ...state.announced.keys()]);
    for (const id of tickets) {
      const d = describe(id);
      const key = d ? d.cls : "";
      if ((state.announced.get(id) || "") === key) continue;
      state.announced.set(id, key);
      if (d && d.cls !== "running") K.announce(`${id}: ${d.text}`);
      else if (d) K.announce(`${id}: working on ${stageLabel(state.runs.get(id).stage)}`);
      else K.announce(`${id}: no run`);
    }
  }

  let loading = null;
  async function load() {
    if (!state.project) return;
    const project = state.project;
    let data;
    try {
      data = await K.api(projectPath("/runs"));
    } catch (err) {
      return;
    }
    if (project !== state.project) return;
    state.skew = Number(data.now) - Date.now() / 1000;
    state.stale = Number(data.stale_seconds) || 900;
    state.runs = new Map(data.runs.map((r) => [r.ticket, r]));
    state.handoffs = new Map();
    for (const h of data.handoffs) state.handoffs.set(h.ticket, h);  // newest last wins
    const first = state.announced.size === 0;
    if (first) {
      for (const id of new Set([...state.runs.keys(), ...state.handoffs.keys()])) {
        const d = describe(id);
        state.announced.set(id, d ? d.cls : "");
      }
    }
    paintAll(!first);
    const keys = [...state.handoffs.values()].filter((h) => h.pickup).map((h) => h.id)
      .concat([...state.runs.values()].filter(liveHeadless).map((r) => `${r.id}:live`)).sort().join(",");
    if (keys !== state.actionKeys) {
      state.actionKeys = keys;
      K.refresh();  // re-render so the card actions follow
    }
  }

  function scheduleLoad() {
    if (loading) return;
    loading = window.setTimeout(async () => {
      loading = null;
      await load();
    }, 120);
  }

  function liveHeadless(run) {
    return Boolean(run) && run.kind === "headless" && LIVE.includes(run.status);
  }

  function onEvent(ev) {
    if (ev.event === "run.event" || ev.event === "run.changed") {
      window.dispatchEvent(new CustomEvent("kanban:run-event", {detail: ev}));
    }
    if (["run.changed", "handoff.created", "handoff.pickup_timeout", "board.changed"].includes(ev.event)) {
      scheduleLoad();
    }
  }

  function follow() {
    const project = K.project();
    if (!project || project === state.project) return;
    state.project = project;
    state.runs = new Map();
    state.handoffs = new Map();
    state.announced = new Map();
    state.actionKeys = "";
    if (state.stream) state.stream.close();
    state.stream = K.stream(`/api/events?project=${encodeURIComponent(project)}`, onEvent);
    scheduleLoad();
  }

  async function runHeadless(ticket) {
    const h = state.handoffs.get(ticket.id);
    try {
      const res = await K.api(projectPath(`/tickets/${encodeURIComponent(ticket.id)}/runs/start`),
                              {method: "POST", body: h ? {handoff_id: h.id} : {}});
      if (res.run && res.run.status === "failed") K.announce(`Headless run for ${ticket.id} failed: ${res.run.reason}`);
      else if (res.run && res.run.status === "queued") K.announce(`Headless run for ${ticket.id} queued (all slots busy)`);
      else K.announce(`Headless run started for ${ticket.id}`);
    } catch (err) {
      K.announce(`Run headless for ${ticket.id}: ${err.message}`);
    }
    scheduleLoad();
  }

  async function stopRun(ticket) {
    const run = state.runs.get(ticket.id);
    if (!liveHeadless(run)) return;
    if (!(await window.kanban.confirm(`Stop the headless run of ${ticket.id}? Claude is interrupted (SIGTERM, then SIGKILL).`,
                          "Stop run"))) return;
    try {
      await K.api(`/api/runs/${encodeURIComponent(run.id)}/stop`, {method: "POST", body: {}});
      K.announce(`Headless run of ${ticket.id} stopped`);
    } catch (err) {
      K.announce(`Stop failed for ${ticket.id}: ${err.message}`);
    }
    scheduleLoad();
  }

  async function copyPrompt(ticket) {
    const h = state.handoffs.get(ticket.id);
    if (!h || !h.prompt) { K.announce("No prompt to copy"); return; }
    try {
      await navigator.clipboard.writeText(h.prompt);
      K.announce(`Prompt for ${ticket.id} copied; paste it into a Claude Code session in this project`);
    } catch (err) {
      K.announce(`Could not copy the prompt: ${err.message}`);
    }
  }

  K.decorateCard((card, ticket) => {
    follow();
    paint(card, ticket.id);
  });
  K.cardActions((ticket) => {
    const run = state.runs.get(ticket.id);
    if (liveHeadless(run)) return [{label: "Stop", run: stopRun}];
    if (run && LIVE.includes(run.status)) return [];
    const h = state.handoffs.get(ticket.id);
    if (!h || !h.pickup) return [];
    return [{label: "Run headless", run: runHeadless}, {label: "Copy prompt", run: copyPrompt}];
  });
  follow();
  if (timer === null) timer = window.setInterval(() => { follow(); paintAll(true); }, TICK_MS);
})();
