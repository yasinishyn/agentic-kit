/* Headless run transcript (PRD-04, architecture §8): a drawer panel with the ticket's headless runs (newest first) and
   the chosen run's events from GET /api/runs/<id>/events, paged by afterSeq and followed live through the board's
   event stream (runs.js forwards run.event and run.changed as a "kanban:run-event" window event). Stop (UI token)
   ends a live headless run. Every string that comes from a run is set with textContent (el's text), never parsed as
   HTML. */
(function () {
  "use strict";

  const K = window.kanban;
  const B = window.KanbanBoard;
  if (!K || !B) return;
  const el = B.el;
  const LIVE = ["queued", "running", "waiting"];
  const PAGE = 500;
  let view = null;  // the panel on screen: {box, ticket, runs, runId, lastSeq, list, status, stop, busy, again}

  const enc = encodeURIComponent;
  const projectPath = (suffix) => `/api/projects/${enc(K.project())}${suffix}`;

  function runLabel(run) {
    const when = new Date(Number(run.created_at || run.started_at || 0) * 1000);
    const at = Number.isNaN(when.getTime()) ? "" : when.toLocaleString();
    return `${at} · ${run.stage_label || run.stage} · ${run.status}`;
  }

  function kindClass(kind) {
    return `trk-${String(kind || "event").toLowerCase().replace(/[^a-z_]/g, "")}`;
  }

  function row(ev) {
    const at = new Date(Number(ev.at) * 1000);
    return el("li", {className: `tr-ev ${kindClass(ev.kind)}`, dataset: {seq: String(ev.seq)}}, [
      el("span", {className: "tr-kind", text: ev.kind}),
      el("time", {className: "tr-time", text: Number.isNaN(at.getTime()) ? "" : at.toLocaleTimeString()}),
      el("pre", {className: "tr-text", text: ev.text}),
    ]);
  }

  function setRun(v, run) {
    if (!run) return;
    v.runs.set(run.id, run);
    if (run.id !== v.runId) return;
    const n = Number(run.permission_denials || 0);
    v.status.textContent = `${run.status}${run.reason ? " · " + run.reason : ""}` +
      (n ? ` · ${n} permission denial${n > 1 ? "s" : ""}` : "");
    v.stop.hidden = !LIVE.includes(run.status);
    const option = v.select.querySelector(`option[value="${CSS.escape(run.id)}"]`);
    if (option) option.textContent = runLabel(run);
  }

  async function fetchMore(v) {
    if (v.busy) { v.again = true; return; }
    v.busy = true;
    try {
      for (;;) {
        const runId = v.runId;
        const data = await K.api(`/api/runs/${enc(runId)}/events?afterSeq=${v.lastSeq}&limit=${PAGE}`);
        if (view !== v || v.runId !== runId) return;
        const stick = v.list.scrollHeight - v.list.scrollTop - v.list.clientHeight < 40;
        for (const ev of data.events) {
          if (ev.seq > v.lastSeq) v.list.append(row(ev));
        }
        v.lastSeq = Math.max(v.lastSeq, Number(data.last_seq) || 0);
        setRun(v, data.run);
        if (stick) v.list.scrollTop = v.list.scrollHeight;
        if (!data.more) break;
      }
    } catch (err) {
      v.status.textContent = `Transcript unavailable: ${err.message}`;
    } finally {
      v.busy = false;
      if (v.again && view === v) { v.again = false; fetchMore(v); }
    }
  }

  function openRun(v, runId) {
    v.runId = runId;
    v.lastSeq = 0;
    v.list.replaceChildren();
    setRun(v, v.runs.get(runId));
    fetchMore(v);
  }

  async function stop(v) {
    const runId = v.runId;
    if (!(await window.kanban.confirm(`Stop the headless run of ${v.ticket.id}? Claude is interrupted (SIGTERM, then SIGKILL).`,
                          "Stop run"))) return;
    try {
      const data = await K.api(`/api/runs/${enc(runId)}/stop`, {method: "POST", body: {}});
      setRun(v, data.run);
      K.announce(`Headless run of ${v.ticket.id} stopped`);
    } catch (err) {
      K.announce(`Stop failed: ${err.message}`);
    }
    fetchMore(v);
  }

  async function render(box, ticket) {
    const v = {box, ticket, runs: new Map(), runId: null, lastSeq: 0, busy: false, again: false};
    view = v;
    box.replaceChildren(el("p", {className: "muted", text: "Loading…"}));
    let data;
    try {
      data = await K.api(projectPath(`/tickets/${enc(ticket.id)}/runs`));
    } catch (err) {
      box.replaceChildren(el("p", {className: "muted", text: `Runs unavailable: ${err.message}`}));
      return;
    }
    if (view !== v) return;
    const runs = data.runs.filter((r) => r.kind === "headless");
    if (!runs.length) {
      box.replaceChildren(el("p", {className: "muted", text: "No headless runs for this ticket."}));
      return;
    }
    v.select = el("select", {"aria-label": "Headless run"},
                  runs.map((r) => el("option", {value: r.id, text: runLabel(r)})));
    v.select.addEventListener("change", () => openRun(v, v.select.value));
    v.status = el("span", {className: "tr-status"});
    v.stop = el("button", {type: "button", className: "tr-stop", text: "Stop"});
    v.stop.addEventListener("click", () => stop(v));
    v.list = el("ol", {className: "transcript", role: "log", "aria-label": "Transcript"});
    box.replaceChildren(el("div", {className: "tr-head"}, [v.select, v.stop]), v.status, v.list);
    for (const r of runs) v.runs.set(r.id, r);
    openRun(v, runs[0].id);
  }

  window.addEventListener("kanban:run-event", (e) => {
    const v = view;
    if (!v || !v.list) return;
    if (!v.box.isConnected) { view = null; return; }
    const ev = e.detail || {};
    const data = ev.data || {};
    if (ev.event === "run.event" && data.run_id === v.runId && Number(data.seq) > v.lastSeq) {
      fetchMore(v);
    } else if (ev.event === "run.changed" && data.ticket === v.ticket.id && data.run) {
      if (data.run.kind !== "headless") return;
      if (v.runs.has(data.run.id)) {
        setRun(v, data.run);
        if (data.run.id === v.runId) fetchMore(v);
      } else {
        render(v.box, v.ticket);  // a new headless run of this ticket: show it
      }
    }
  });

  K.addDrawerPanel("transcript", "Headless runs", render);
})();
