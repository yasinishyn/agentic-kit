/* Board app: token bootstrap, API + streamed events (fetch with the Authorization header; never a token in a URL),
   header (project switcher, Add project, New ticket, connection chip, help menu), welcome (onboarding state from the
   daemon), Add/Remove project with a dry-run preview, the project sheet (project panels; core registers
   "connection"), the tabbed ticket panel (Overview · Spec · Runs · Terminal · Git), the UI plug-in API
   (window.kanban, architecture §4.1) and module loading from /ui/modules. The token comes from
   window.__KANBAN_TOKEN__ (desktop app) or the URL fragment #t=… (browser mode, moved to sessionStorage and removed
   from the address bar). Every dynamic string goes through textContent; the one innerHTML sink is the /view
   fragment, escaped server-side. */
(function () {
  "use strict";

  const KEY = "kanban.token";
  const PROJECT_KEY = "kanban.project";
  const CONNECT_CMD = "claude --dangerously-load-development-channels plugin:kanban@agentic-kit";
  const CODE_TAB_NOTE = "Code-tab sessions don't receive board moves (they have no channels). For them, use " +
                        "Copy prompt or Run headless on a queued card.";
  const SESSIONS_SHOWN = 10;
  const TAB_LABELS = {overview: "Overview", spec: "Spec", runs: "Runs", terminal: "Terminal", git: "Git"};
  const B = window.KanbanBoard;
  const el = B.el;
  const $ = (id) => document.getElementById(id);

  function readToken() {
    if (window.__KANBAN_TOKEN__) return String(window.__KANBAN_TOKEN__);
    let token = "";
    const m = /(?:^#|&)t=([^&]+)/.exec(location.hash);
    if (m) {
      token = decodeURIComponent(m[1]);
      try { sessionStorage.setItem(KEY, token); } catch (e) { /* keep it in memory only */ }
      history.replaceState(null, "", location.pathname + location.search);
    }
    if (!token) {
      try { token = sessionStorage.getItem(KEY) || ""; } catch (e) { token = ""; }
    }
    return token;
  }

  const token = readToken();
  const state = {projects: [], project: null, board: null, drawerTicket: null, drawerTab: "overview", stream: null,
                 sessions: [], onboarding: null, welcomeForced: false, view: {ticket: null, path: null}};
  const registry = {decorators: [], panels: [], projectPanels: [], beforeMove: [], cardActions: []};
  const warnedTabs = new Set();

  function announce(text) {
    const live = $("live");
    live.textContent = "";
    window.setTimeout(() => { live.textContent = text; }, 30);
    $("status").textContent = text;
  }

  async function api(path, opts) {
    const o = Object.assign({}, opts || {});
    const headers = Object.assign({}, o.headers || {}, {Authorization: `Bearer ${token}`});
    if (o.body !== undefined && typeof o.body !== "string") {
      o.body = JSON.stringify(o.body);
      headers["Content-Type"] = "application/json";
    }
    o.headers = headers;
    o.cache = "no-store";
    const resp = await fetch(path, o);
    const text = await resp.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
    if (!resp.ok) {
      const err = new Error((data && data.error) || `HTTP ${resp.status}`);
      err.status = resp.status;
      err.data = data;
      if (resp.status === 401) $("auth").hidden = false;
      throw err;
    }
    return data;
  }

  /* A user-facing reason for a failed call: 403 → reload hint; network → retry hint. */
  function reason(err) {
    if (err.status === 403) return "The board refused this action; reload the board and try again.";
    if (!err.status) return "Could not reach the Kanban service; check that it is running and try again.";
    return err.message;
  }

  /* Newline-delimited JSON over a long-lived response; reconnects with backoff until close(). onOpen runs on every
     (re)connect, so listeners can refetch what they may have missed. */
  function stream(path, onEvent, onOpen) {
    let closed = false;
    let controller = null;
    let delay = 500;
    async function run() {
      while (!closed) {
        controller = new AbortController();
        try {
          const resp = await fetch(path, {headers: {Authorization: `Bearer ${token}`}, cache: "no-store",
                                          signal: controller.signal});
          if (resp.status === 401) { $("auth").hidden = false; return; }
          if (!resp.ok || !resp.body) throw new Error(`HTTP ${resp.status}`);
          delay = 500;
          if (onOpen) { try { onOpen(); } catch (err) { console.error("stream open", err); } }
          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let buf = "";
          for (;;) {
            const {value, done} = await reader.read();
            if (done) break;
            buf += decoder.decode(value, {stream: true});
            let i;
            while ((i = buf.indexOf("\n")) >= 0) {
              const line = buf.slice(0, i).trim();
              buf = buf.slice(i + 1);
              if (!line) continue;
              try { onEvent(JSON.parse(line)); } catch (err) { console.error("event", err); }
            }
          }
        } catch (err) {
          if (closed) return;
        }
        if (closed) return;
        await new Promise((r) => window.setTimeout(r, delay));
        delay = Math.min(delay * 2, 10000);
      }
    }
    run();
    return {close() { closed = true; if (controller) controller.abort(); }};
  }

  const projectPath = (suffix) => `/api/projects/${encodeURIComponent(state.project)}${suffix}`;
  const currentProject = () => state.projects.find((p) => p.id === state.project) || null;

  async function copyText(text, what) {
    try {
      await navigator.clipboard.writeText(text);
      announce(`${what} copied`);
      return true;
    } catch (err) {
      announce(`Could not copy; select the text and copy it yourself`);
      return false;
    }
  }

  function commandRow(text) {
    const copy = el("button", {type: "button", text: "Copy"});
    copy.addEventListener("click", () => copyText(text, "Command"));
    return el("div", {className: "cmd"}, [el("code", {text, tabindex: "0"}), copy]);
  }

  // ---------------------------------------------------------------- projects
  function renderProjectList() {
    const select = $("project");
    select.replaceChildren(...state.projects.map((p) => el("option", {value: p.id, text: p.name, title: p.root})));
    select.disabled = !state.projects.length;
    if (!state.projects.length) select.append(el("option", {value: "", text: "No projects"}));
    if (state.project) select.value = state.project;
  }

  async function loadProjects() {
    const data = await api("/api/projects");
    state.projects = data.projects;
    renderProjectList();
    let saved = null;
    try { saved = localStorage.getItem(PROJECT_KEY); } catch (e) { saved = null; }
    const pick = state.projects.find((p) => p.id === saved) || state.projects[0];
    if (!pick) {
      showNoProject();
      return;
    }
    $("project").value = pick.id;
    await selectProject(pick.id);
  }

  /* No project registered (or the last one removed): welcome step 1, all-projects stream (hears project.registered). */
  function showNoProject() {
    state.project = null;
    state.board = null;
    state.sessions = [];
    state.onboarding = null;
    closeDrawer();
    closeSheet(false);
    $("board").style.gridTemplateColumns = "";
    $("board").replaceChildren(el("div", {className: "empty", text: "No project on the board yet. Add one to start."}));
    if (state.stream) state.stream.close();
    state.stream = stream("/api/events", onEvent);
    updateChip();
    renderWelcome();
    renderProjectPanels();
  }

  /* A project registered after the board opened: refresh the list, keep the current selection (pick one if none). */
  async function refreshProjects() {
    if (!state.project) {
      await loadProjects();
      return;
    }
    try {
      state.projects = (await api("/api/projects")).projects;
    } catch (err) {
      return;
    }
    renderProjectList();
  }

  async function selectProject(id) {
    state.project = id;
    state.sessions = [];
    state.onboarding = null;
    state.welcomeForced = false;
    try { localStorage.setItem(PROJECT_KEY, id); } catch (e) { /* optional */ }
    renderProjectList();
    closeDrawer();
    if (state.stream) state.stream.close();
    updateChip();
    await loadBoard();
    loadOnboarding();
    state.stream = stream(`/api/events?project=${encodeURIComponent(id)}`, onEvent, () => loadSessions());
  }

  /* project.removed (here or in another window): switch to the first remaining project or the welcome. */
  function projectRemoved(id) {
    const known = state.projects.some((p) => p.id === id);
    state.projects = state.projects.filter((p) => p.id !== id);
    renderProjectList();
    if (state.project !== id) return;
    if (known) announce("This project was removed from the board");
    if (state.projects.length) selectProject(state.projects[0].id);
    else showNoProject();
  }

  let reloadTimer = null;
  function scheduleReload(message) {
    window.clearTimeout(reloadTimer);
    reloadTimer = window.setTimeout(async () => {
      await loadBoard();
      if (message) announce(message);
    }, 150);
  }

  function onEvent(ev) {
    const data = ev.data || {};
    if (ev.event === "board.changed") {
      scheduleReload("Board updated");
      loadOnboarding();
    } else if (ev.event === "handoff.created") {
      announce(`Hand-off to ${data.stage} for ${data.ticket} (${data.status})`);
    } else if (ev.event === "project.registered") {
      refreshProjects().then(() => { announce(`New project: ${data.name}`); loadOnboarding(); });
    } else if (ev.event === "project.removed") {
      projectRemoved(data.id);
    } else if (ev.event === "session.changed") {
      loadSessions();
      loadOnboarding();
    }
  }

  async function loadBoard() {
    if (!state.project) return;
    const project = state.project;
    let board;
    try {
      board = await api(projectPath("/board"));
    } catch (err) {
      if (err.status === 404 && project === state.project) { projectRemoved(project); return; }
      announce(`Could not load the board: ${reason(err)}`);
      return;
    }
    if (project !== state.project) return;
    state.board = board;
    B.render($("board"), state.board, {
      onMove: move, onOpen: openDrawer, decorators: registry.decorators, cardActions: registry.cardActions,
    });
    if (state.drawerTicket) {
      const t = state.board.tickets.find((x) => x.id === state.drawerTicket.id);
      if (t) renderDrawer(t);
    }
    renderProjectPanels();
  }

  async function move(ticket, dst) {
    for (const fn of registry.beforeMove) {
      let ok = true;
      try { ok = await fn(ticket, ticket.status, dst); } catch (err) { console.error("beforeMove", err); ok = false; }
      if (ok === false) { announce(`Move of ${ticket.title} cancelled`); return; }
    }
    try {
      const res = await api(projectPath(`/tickets/${encodeURIComponent(ticket.id)}/move`),
                            {method: "POST", body: {stage: dst}});
      announce(`Moved ${ticket.title} to ${dst}${res.handoff ? " (hand-off " + res.handoff.status + ")" : ""}`);
    } catch (err) {
      announce(`Not moved: ${reason(err)}`);
    }
    await loadBoard();
    loadOnboarding();
  }

  // ---------------------------------------------------------------- New ticket (B14 form in a header popover)
  function toggleNewTicket(open) {
    const pop = $("new-ticket-pop");
    const show = open === undefined ? pop.hidden : open;
    pop.hidden = !show;
    $("new-ticket-toggle").setAttribute("aria-expanded", String(show));
    if (show) $("new-ticket-title").focus();
  }

  async function createTicket(ev) {
    ev.preventDefault();
    const input = $("new-ticket-title");
    const title = input.value.trim();
    if (!title) return;
    if (!state.project) { announce("Add a project first"); return; }
    try {
      const res = await api(projectPath(`/tickets`), {method: "POST", body: {title}});
      input.value = "";
      toggleNewTicket(false);
      $("new-ticket-toggle").focus();
      announce(`Created ticket ${res.ticket.title} in Discovery`);
    } catch (err) {
      announce(`Not created: ${reason(err)}`);
    }
    await loadBoard();
  }

  // ---------------------------------------------------------------- sessions, connection chip and panel
  async function loadSessions() {
    if (!state.project) return;
    const project = state.project;
    try {
      const data = await api(projectPath("/sessions"));
      if (project !== state.project) return;
      state.sessions = data.sessions.slice().sort((a, b) => Number(b.last_seen || 0) - Number(a.last_seen || 0));
    } catch (err) {
      if (err.status !== 404) return;
      state.sessions = [];
    }
    updateChip();
    renderProjectPanel("connection");
  }

  function updateChip() {
    const chip = $("conn-chip");
    $("conn-text").textContent = state.project ? B.sessionSummary(state.sessions) : "No project";
    chip.classList.toggle("on", Boolean(state.project) && B.channelsOn(state.sessions));
  }

  function ago(t) {
    const s = Math.max(0, Math.round(Date.now() / 1000 - Number(t || 0)));
    if (s < 60) return `${s} s ago`;
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    return `${Math.round(s / 3600)} h ago`;
  }

  /* Connect Claude: the app's terminal module provides window.kanban.connectClaude (PRD-06); the browser gets the
     command with Copy. Detection happens at render time, so module load order does not matter. */
  function connectControls(project, statusLine) {
    if (typeof window.kanban.connectClaude === "function") {
      const b = el("button", {type: "button", className: "btn-primary", text: "Connect Claude"});
      b.addEventListener("click", async () => {
        statusLine.className = "status-line muted";
        statusLine.textContent = "Starting Claude…";
        try {
          const res = await window.kanban.connectClaude(project);
          statusLine.textContent = res === "revealed" ? "Claude is running in the project terminal." : "Claude started in the project terminal.";
        } catch (err) {
          statusLine.className = "status-line error";
          statusLine.textContent = (err && err.message) || String(err);
        }
      });
      return [b];
    }
    return [el("p", {className: "muted", text: "In a terminal in this project, start Claude Code with channels:"}),
            commandRow(CONNECT_CMD)];
  }

  function renderConnection(box, project) {
    if (!project) {
      box.replaceChildren(el("p", {className: "muted", text: "No project selected."}));
      return;
    }
    const on = B.channelsOn(state.sessions);
    const shown = state.sessions.slice(0, SESSIONS_SHOWN);
    const list = el("ul", {className: "sessions", "aria-label": "Live Claude sessions"},
      shown.length ? shown.map((s) => el("li", {}, [
        el("span", {className: "kind", text: B.sessionLabel(s),
                    title: B.sessionLabel(s) === "unknown" ? "kind could not be detected" : null}),
        s.channel && B.sessionLabel(s) !== "CLI + channels" ? el("span", {className: "muted", text: "channels"}) : null,
        s.kind === "headless" && s.run_id ? el("span", {className: "muted", text: "headless run"}) : null,
        el("span", {className: "muted", text: `last seen ${ago(s.last_seen)}`}),
      ])) : [el("li", {className: "none", text: "No Claude session is connected to this project."})]);
    if (state.sessions.length > shown.length) {
      list.append(el("li", {className: "none", text: `and ${state.sessions.length - shown.length} more`}));
    }
    const statusLine = el("p", {className: "status-line", role: "status"});
    box.replaceChildren(
      el("p", {className: on ? "" : "muted",
               text: on ? "Channels on: board moves reach a Claude session."
                        : "No session with channels: hand-offs queue until one connects."}),
      list,
      ...(on ? [] : connectControls(project.id, statusLine)),
      statusLine,
      el("p", {className: "explain", text: CODE_TAB_NOTE}));
  }

  // ---------------------------------------------------------------- project panels (the project sheet)
  function projectPanelSection(panel, project) {
    const box = el("div", {className: "panel-body"});
    const section = el("section", {className: "panel", dataset: {panel: panel.id}},
                       [el("h3", {className: "panel-title", text: panel.title}), box]);
    try {
      panel.render(box, project);
    } catch (err) {
      console.error("project panel", panel.id, err);
      box.replaceChildren(el("p", {className: "panel-failed", text: "This panel failed to load"}));
    }
    return section;
  }

  function renderProjectPanels() {
    const project = currentProject();
    $("sheet-sub").textContent = project ? project.root : "";
    $("sheet-title").textContent = project ? project.name : "Project";
    $("project-panels").replaceChildren(...registry.projectPanels.map((p) => projectPanelSection(p, project)));
  }

  function renderProjectPanel(id) {
    const panel = registry.projectPanels.find((p) => p.id === id);
    const old = $("project-panels").querySelector(`[data-panel="${CSS.escape(id)}"]`);
    if (!panel || !old) return;
    old.replaceWith(projectPanelSection(panel, currentProject()));
  }

  function openSheet() {
    closeDrawer();
    $("project-sheet").hidden = false;
    $("conn-chip").setAttribute("aria-expanded", "true");
    $("sheet-close").focus();
  }

  function closeSheet(focusChip) {
    if ($("project-sheet").hidden) return;
    $("project-sheet").hidden = true;
    $("conn-chip").setAttribute("aria-expanded", "false");
    if (focusChip) $("conn-chip").focus();
  }

  // ---------------------------------------------------------------- welcome (architecture §3)
  async function loadOnboarding() {
    if (!state.project) { renderWelcome(); return; }
    const project = state.project;
    try {
      const data = await api(projectPath("/onboarding"));
      if (project !== state.project) return;
      state.onboarding = data;
    } catch (err) {
      if (project !== state.project) return;
      if (err.status === 404) state.onboarding = "unavailable";  // daemon older than v0.3.1: no per-project steps
    }
    renderWelcome();
  }

  async function saveOnboarding(body) {
    if (!state.project) return;
    try {
      state.onboarding = await api(projectPath("/onboarding"), {method: "POST", body});
    } catch (err) {
      announce(`Could not save: ${reason(err)}`);
    }
    renderWelcome();
  }

  function step(n, title, text, status, extra) {
    const labels = {done: "done", active: "next", locked: "not yet"};
    return el("li", {className: `step ${status}`}, [
      el("span", {className: "step-num", "aria-hidden": "true", text: status === "done" ? "✓" : String(n)}),
      el("h3", {text: title}),
      el("p", {}, [el("span", {className: "sr-only", text: `Step ${n}, ${labels[status]}: `}), text]),
      extra && extra.length ? el("div", {className: "step-actions"}, extra) : null,
    ]);
  }

  function renderWelcome() {
    const box = $("welcome");
    const ob = state.onboarding;
    const hasProject = Boolean(state.project);
    if (hasProject && (!ob || ob === "unavailable")) { box.hidden = true; return; }
    const finished = hasProject && ob.connected && ob.first_move;
    if (hasProject && !state.welcomeForced && (ob.dismissed || finished)) { box.hidden = true; return; }
    const addBtn = el("button", {type: "button", className: hasProject ? "" : "btn-primary",
                                 text: hasProject ? "Add another" : "Add project"});
    addBtn.addEventListener("click", addProjectDialog);
    const s1 = step(1, "Add a project", "A folder with .git, .claude or .SDD.", hasProject ? "done" : "active",
                    hasProject ? [] : [addBtn]);
    let s2;
    let s3;
    if (!hasProject) {
      s2 = step(2, "Connect Claude", "Start Claude Code with channels so board moves reach it.", "locked");
      s3 = step(3, "Move your first card", "Drag a card, or use its ⋯ menu.", "locked");
    } else {
      const statusLine = el("p", {className: "status-line", role: "status"});
      const extra = ob.connected ? [] : connectControls(state.project, statusLine).concat([statusLine]);
      if (!ob.connected && ob.plugin_python === "missing") {
        extra.push(el("p", {className: "fix", text: "The plugin needs Python: move Kanban.app to Applications, " +
                                                     "or install python3 (xcode-select --install)."}));
      }
      s2 = step(2, "Connect Claude", ob.connected ? "A Claude session with channels is connected."
                  : "Start Claude Code with channels in this project so board moves reach it.",
                ob.connected ? "done" : "active", extra);
      const newTicketBtn = el("button", {type: "button", className: "btn-ghost", text: "New ticket"});
      newTicketBtn.addEventListener("click", () => toggleNewTicket(true));
      s3 = step(3, "Move your first card", ob.first_move ? "Done." : "Create a ticket, then drag its card to the next "
                  + "column, or use its ⋯ menu.",
                ob.first_move ? "done" : ob.connected ? "active" : "locked", ob.first_move ? [] : [newTicketBtn]);
    }
    const head = el("div", {className: "welcome-head"}, [
      el("h2", {id: "welcome-title", text: "Get started"}),
      el("span", {className: "muted", text: "Three steps to a working board."}),
    ]);
    if (hasProject) {
      const hide = el("button", {type: "button", className: "btn-ghost", text: "Dismiss"});
      hide.addEventListener("click", () => { state.welcomeForced = false; saveOnboarding({dismissed: true}); });
      head.append(hide);
    }
    box.replaceChildren(head, el("ol", {className: "steps"}, [s1, s2, s3]));
    box.hidden = false;
  }

  // ---------------------------------------------------------------- modal dialogs (no native panels in WKWebView)
  let dialogSeq = 0;
  const FOCUSABLE = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), " +
                    "textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

  /* fill(box, close) builds the dialog and returns the element to focus; resolves with close()'s value. */
  function modal(title, fill, role) {
    const opener = document.activeElement;
    const titleId = `k-modal-${++dialogSeq}`;
    const box = el("div", {className: "k-dialog", role: role || "dialog", "aria-modal": "true",
                           "aria-labelledby": titleId, tabindex: "-1"}, [el("h2", {id: titleId, text: title})]);
    const overlay = el("div", {className: "k-overlay"}, [box]);
    const inerted = [];
    for (const node of Array.from(document.body.children)) {
      if (!node.inert) { node.inert = true; inerted.push(node); }
    }
    document.body.append(overlay);
    return new Promise((resolve) => {
      let closed = false;
      const focusables = () => Array.from(box.querySelectorAll(FOCUSABLE)).filter((n) => n.offsetParent !== null);
      function close(value) {
        if (closed) return;
        closed = true;
        document.removeEventListener("keydown", onKey, true);
        overlay.remove();
        for (const node of inerted) node.inert = false;
        if (opener && opener.isConnected && typeof opener.focus === "function") opener.focus();
        resolve(value);
      }
      function onKey(e) {
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          close(false);
        } else if (e.key === "Tab") {
          const items = focusables();
          if (!items.length) { e.preventDefault(); return; }
          const first = items[0];
          const lastItem = items[items.length - 1];
          if (e.shiftKey && (document.activeElement === first || !box.contains(document.activeElement))) {
            e.preventDefault(); lastItem.focus();
          } else if (!e.shiftKey && (document.activeElement === lastItem || !box.contains(document.activeElement))) {
            e.preventDefault(); first.focus();
          }
        }
      }
      overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(false); });
      document.addEventListener("keydown", onKey, true);
      const initial = fill(box, close);
      (initial || focusables()[0] || box).focus();
    });
  }

  let confirmSeq = 0;

  /* Modal yes/no dialog → Promise<boolean>. Focus trap, focus returns to the opener, Escape or a click outside =
     cancel, Enter on a button activates it; the message goes through textContent only. */
  function confirmDialog(message, okLabel) {
    const opener = document.activeElement;
    const titleId = `k-confirm-${++confirmSeq}`;
    const cancel = el("button", {type: "button", text: "Cancel"});
    const ok = el("button", {type: "button", className: "k-primary", text: okLabel || "OK"});
    const box = el("div", {className: "k-dialog", role: "alertdialog", "aria-modal": "true",
                           "aria-labelledby": titleId, tabindex: "-1"}, [
      el("p", {id: titleId, text: message}),
      el("div", {className: "k-buttons"}, [cancel, ok]),
    ]);
    const overlay = el("div", {className: "k-overlay"}, [box]);
    const inerted = [];
    for (const node of Array.from(document.body.children)) {
      if (!node.inert) { node.inert = true; inerted.push(node); }
    }
    document.body.append(overlay);
    return new Promise((resolve) => {
      let closed = false;
      function close(value) {
        if (closed) return;
        closed = true;
        document.removeEventListener("keydown", onKey, true);
        overlay.remove();
        for (const node of inerted) node.inert = false;
        if (opener && opener.isConnected && typeof opener.focus === "function") opener.focus();
        resolve(value);
      }
      function onKey(e) {
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          close(false);
        } else if (e.key === "Tab") {
          e.preventDefault();
          (document.activeElement === cancel ? ok : cancel).focus();
        }
      }
      cancel.addEventListener("click", () => close(false));
      ok.addEventListener("click", () => close(true));
      overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(false); });
      document.addEventListener("keydown", onKey, true);
      cancel.focus();
    });
  }

  // ---------------------------------------------------------------- Add project (ADR-006: always a dry-run preview)
  function tauriInvoke() {
    const t = window.__TAURI__;
    return t && t.core && typeof t.core.invoke === "function" ? t.core.invoke : null;
  }

  function addProjectDialog() {
    const invoke = tauriInvoke();
    return modal("Add project", (box, close) => {
      const id = `k-add-${dialogSeq}`;
      let preview = null;
      const input = el("input", {id: `${id}-path`, type: "text", spellcheck: "false", autocomplete: "off",
                                 placeholder: "/Users/you/code/my-project or ~/code/my-project",
                                 "aria-describedby": `${id}-help ${id}-error`});
      const check = el("button", {type: "button", text: "Preview"});
      const field = el("div", {className: "field", hidden: Boolean(invoke)}, [
        el("label", {for: `${id}-path`, text: "Folder path"}),
        el("div", {className: "field-row"}, [input, check]),
        el("span", {id: `${id}-help`, className: "muted", text: "An existing folder with .git, .claude or .SDD."}),
      ]);
      const error = el("p", {id: `${id}-error`, className: "error", role: "alert"});
      const previewBox = el("div", {className: "preview", hidden: true, "aria-live": "polite"});
      const cancel = el("button", {type: "button", text: "Cancel"});
      const confirm = el("button", {type: "button", className: "k-primary", text: "Add project", disabled: true});
      const parts = [];
      let pick = null;
      if (invoke) {
        pick = el("button", {type: "button", text: "Choose folder…"});
        const typeIt = el("button", {type: "button", className: "link", text: "Type a path instead"});
        typeIt.addEventListener("click", () => { field.hidden = false; typeIt.hidden = true; input.focus(); });
        pick.addEventListener("click", async () => {
          error.textContent = "";
          let chosen = null;
          try {
            chosen = await invoke("pick_folder");
          } catch (err) {
            field.hidden = false;
            typeIt.hidden = true;
            error.textContent = `The folder picker is not available (${(err && err.message) || err}); type the path.`;
            input.focus();
            return;
          }
          if (!chosen) return;  // cancelled: the dialog stays open
          input.value = String(chosen);
          runPreview();
        });
        parts.push(el("div", {className: "field-row"}, [pick, typeIt]));
      }

      function invalidate() {
        preview = null;
        confirm.disabled = true;
        previewBox.hidden = true;
      }

      async function runPreview() {
        const path = input.value.trim();
        invalidate();
        error.textContent = "";
        if (!path) { error.textContent = "Enter a folder path."; input.focus(); return; }
        check.disabled = true;
        try {
          preview = await api("/api/projects/add", {method: "POST", body: {path, dry_run: true}});
        } catch (err) {
          error.textContent = err.status === 400 ? `Cannot add this folder: ${err.message}` : reason(err);
          check.disabled = false;
          return;
        }
        check.disabled = false;
        showPreview(preview);
        confirm.disabled = false;
      }

      function showPreview(p) {
        const rows = [["Folder", p.resolved], ["Name", p.name], ["Found", p.markers.join(", ")]];
        const facts = el("dl", {className: "facts"});
        for (const [k, v] of rows) facts.append(el("dt", {text: k}), el("dd", {}, [el("code", {text: v})]));
        const extra = [];
        if (!p.has_specs) {
          const cb = el("input", {type: "checkbox", id: `${id}-specs`, checked: !p.is_home});
          extra.push(el("div", {className: "check"}, [cb, el("label", {for: `${id}-specs`},
            [el("span", {text: "Create "}), el("code", {text: ".SDD/specs"}), el("span", {text: " (where tickets live)"})])]));
        }
        if (p.is_home) {
          extra.push(el("p", {className: "callout warn", text: "This is your home folder. It qualifies only because " +
                                                               "of ~/.claude; you probably want a project folder inside it."}));
        }
        if (p.already_registered) {
          extra.push(el("p", {className: "callout info", text: "This project is already on the board; adding it switches to it."}));
        }
        previewBox.replaceChildren(el("h3", {className: "section-title", text: "Preview"}), facts, ...extra);
        previewBox.hidden = false;
      }

      async function doAdd() {
        if (!preview) return;
        const cb = previewBox.querySelector(`#${CSS.escape(id)}-specs`);
        confirm.disabled = true;
        error.textContent = "";
        try {
          const res = await api("/api/projects/add",
                                {method: "POST", body: {path: preview.resolved, create_specs: Boolean(cb && cb.checked)}});
          close(true);
          announce(`Added ${res.project.name}`);
          try { state.projects = (await api("/api/projects")).projects; } catch (err) { /* keep the list */ }
          if (!state.projects.some((p) => p.id === res.project.id)) state.projects.push(res.project);
          await selectProject(res.project.id);
        } catch (err) {
          error.textContent = err.status === 400 ? `Cannot add this folder: ${err.message}` : reason(err);
          confirm.disabled = false;
        }
      }

      input.addEventListener("input", invalidate);
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); runPreview(); } });
      check.addEventListener("click", runPreview);
      cancel.addEventListener("click", () => close(false));
      confirm.addEventListener("click", doAdd);
      box.append(...parts, field, error, previewBox, el("div", {className: "k-buttons"}, [cancel, confirm]));
      if (pick) window.setTimeout(() => pick.click(), 0);
      return pick || input;
    });
  }

  async function removeProject() {
    const project = currentProject();
    if (!project) return;
    const ok = await confirmDialog(`Remove ${project.name} from the board? Its files stay untouched; ` +
                                   "Claude sessions there stop registering it until you add it again.", "Remove");
    if (!ok) return;
    try {
      await api(`/api/projects/${encodeURIComponent(project.id)}`, {method: "DELETE"});
      announce(`Removed ${project.name} from the board`);
      projectRemoved(project.id);
    } catch (err) {
      announce(err.status === 409 ? `Not removed: ${project.name} has live runs; stop its runs first`
                                  : `Not removed: ${reason(err)}`);
    }
  }

  // ---------------------------------------------------------------- help menu (developer-internal text lives here)
  function showHelp() {
    return modal("How this board works", (box, close) => {
      const done = el("button", {type: "button", className: "k-primary", text: "Close"});
      done.addEventListener("click", () => close(false));
      box.append(el("div", {className: "help-text"}, [
        el("p", {}, [el("span", {text: "Each ticket is a folder "}), el("code", {text: ".SDD/specs/<slug>/"}),
                     el("span", {text: " with a README.md; its status: line is the column."})]),
        el("p", {text: "Move a card by dragging it, with its ⋯ menu, or with Alt+← / Alt+→ on a focused card."}),
        el("p", {text: "Moves into Developer and later need an approved spec. Board moves reach Claude sessions " +
                       "started with channels; others get queued hand-offs."}),
        el("p", {text: "Done and E2E are collapsed to rails; click one to expand it."}),
      ]), el("div", {className: "k-buttons"}, [done]));
      return done;
    });
  }

  function helpItems() {
    return [
      {label: "Show welcome", disabled: false, run: () => {
        state.welcomeForced = true;
        if (state.project && state.onboarding && state.onboarding.dismissed) saveOnboarding({dismissed: false});
        else renderWelcome();
        if (state.project && state.onboarding === "unavailable") announce("This board service has no welcome steps");
      }},
      {label: "How this board works", run: showHelp},
      {label: "Project connection…", disabled: !state.project, run: openSheet},
      {separator: true},
      {label: "Add project…", run: addProjectDialog},
      {label: state.project ? `Remove ${(currentProject() || {}).name || "project"}…` : "Remove project…",
       disabled: !state.project, run: removeProject},
    ];
  }

  // ---------------------------------------------------------------- ticket panel (tabs)
  let drawerOpener = null;

  function closeDrawer() {
    const drawer = $("drawer");
    const hadFocus = !drawer.hidden && drawer.contains(document.activeElement);
    const ticket = state.drawerTicket;
    state.drawerTicket = null;
    drawer.hidden = true;
    if (!hadFocus) return;  // closed by a project switch or a refresh: leave focus where the user put it
    // back to what opened the panel; its card may have been re-rendered since, so find it again by id
    let target = drawerOpener && drawerOpener.isConnected ? drawerOpener : null;
    if (!target && ticket) {
      const card = $("board").querySelector(`[data-ticket="${CSS.escape(ticket.id)}"]`);
      target = card && (card.querySelector("button.title") || card);
    }
    (target || $("board")).focus();
  }

  function openDrawer(ticket) {
    const active = document.activeElement;
    if (!$("drawer").contains(active)) drawerOpener = active && active !== document.body ? active : null;
    closeSheet(false);
    if (!state.drawerTicket || state.drawerTicket.id !== ticket.id) state.drawerTab = "overview";
    state.drawerTicket = ticket;
    renderDrawer(ticket);
    $("drawer").hidden = false;
    $("drawer-close").focus();
  }

  function stageLabel(key) {
    const hit = state.board && state.board.stages.find((s) => s[0] === key);
    return hit ? hit[1] : key;
  }

  function panelSection(panel, ticket) {
    const box = el("div", {className: "panel-body"});
    const section = el("section", {className: "plugin-panel", dataset: {panel: panel.id}},
                       [el("h3", {text: panel.title}), box]);
    try {
      panel.render(box, ticket);
    } catch (err) {
      console.error("drawer panel", panel.id, err);
      box.replaceChildren(el("p", {className: "panel-failed", text: "This panel failed to load"}));
    }
    return section;
  }

  function selectTab(name, focus) {
    state.drawerTab = name;
    for (const tab of $("drawer-tabs").querySelectorAll("[role=tab]")) {
      const on = tab.dataset.tab === name;
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
      if (on && focus) tab.focus();
    }
    for (const panel of $("drawer-body").querySelectorAll("[role=tabpanel]")) panel.hidden = panel.dataset.tab !== name;
  }

  function overviewTab(ticket) {
    const facts = el("dl", {className: "facts"});
    const rows = [["Stage", stageLabel(ticket.status)], ["Updated", ticket.updated || "—"]];
    if (ticket.owner) rows.push(["Owner", ticket.owner]);
    if (ticket.progress && ticket.progress.total) rows.push(["Checklist", `${ticket.progress.done}/${ticket.progress.total} checked`]);
    for (const [k, v] of rows) facts.append(el("dt", {text: k}), el("dd", {text: v}));
    const files = el("ul", {className: "files"}, ticket.files.map((f) => {
      const b = el("button", {type: "button", className: "link", text: f.split("/").slice(3).join("/") || f, title: f});
      b.addEventListener("click", () => {
        state.view = {ticket: ticket.id, path: f};
        renderDrawer(ticket);
        selectTab("spec", true);
      });
      return el("li", {}, [b]);
    }));
    return [
      el("section", {}, [el("h3", {text: "Details"}), facts]),
      el("section", {}, [el("h3", {text: `Sub-tasks (${ticket.subtasks.length})`}),
                         B.subtaskList(ticket.subtasks, B.SUBTASKS_SHOWN) || el("p", {className: "muted", text: "None"})]),
      el("section", {}, [el("h3", {text: `Files (${ticket.files.length})`}), files]),
    ];
  }

  function specTab(ticket) {
    if (state.view.ticket !== ticket.id || !ticket.files.includes(state.view.path)) {
      state.view = {ticket: ticket.id, path: ticket.path || ticket.files[0] || null};
    }
    const viewer = el("div", {className: "viewer spec-viewer", "aria-live": "polite"});
    const select = el("select", {className: "spec-file", "aria-label": "Spec file"},
      ticket.files.map((f) => el("option", {value: f, text: f.split("/").slice(3).join("/") || f})));
    if (state.view.path) select.value = state.view.path;
    select.addEventListener("change", () => {
      state.view = {ticket: ticket.id, path: select.value};
      view(select.value, viewer);
    });
    if (state.view.path) view(state.view.path, viewer);
    else viewer.append(el("p", {className: "muted", text: "No files yet."}));
    return [el("div", {className: "spec-bar"}, [select]), viewer];
  }

  function renderDrawer(ticket) {
    state.drawerTicket = ticket;
    $("drawer-title").textContent = ticket.title;
    $("drawer-sub").textContent = `${ticket.id} · ${stageLabel(ticket.status)}`;
    const byTab = {overview: [], spec: [], runs: [], terminal: [], git: []};
    for (const panel of registry.panels) byTab[panel.tab].push(panel);
    const tabs = B.TABS.filter((t) => t === "overview" || t === "spec" || byTab[t].length);
    if (!tabs.includes(state.drawerTab)) state.drawerTab = "overview";
    const tablist = $("drawer-tabs");
    const buttons = tabs.map((t) => {
      const b = el("button", {type: "button", role: "tab", id: `tab-${t}`, "aria-controls": `tabpanel-${t}`,
                              "aria-selected": String(t === state.drawerTab), tabindex: t === state.drawerTab ? "0" : "-1",
                              className: "tab", dataset: {tab: t}, text: TAB_LABELS[t]});
      b.addEventListener("click", () => selectTab(t, true));
      return b;
    });
    tablist.replaceChildren(...buttons);
    const body = $("drawer-body");
    const panels = tabs.map((t) => {
      const content = t === "overview" ? overviewTab(ticket) : t === "spec" ? specTab(ticket) : [];
      const section = el("div", {role: "tabpanel", id: `tabpanel-${t}`, "aria-labelledby": `tab-${t}`, tabindex: "0",
                                 className: `tabpanel tabpanel-${t}`, dataset: {tab: t}, hidden: t !== state.drawerTab},
                         content);
      return section;
    });
    body.replaceChildren(...panels);
    // panels render once attached, so a module can find its tab panel (e.g. the spec editor's toggle)
    tabs.forEach((t, i) => { for (const panel of byTab[t]) panels[i].append(panelSection(panel, ticket)); });
  }

  function onTabKey(e) {
    const tabs = Array.from($("drawer-tabs").querySelectorAll("[role=tab]"));
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    let next = null;
    if (e.key === "ArrowRight") next = tabs[(i + 1) % tabs.length];
    else if (e.key === "ArrowLeft") next = tabs[(i - 1 + tabs.length) % tabs.length];
    else if (e.key === "Home") next = tabs[0];
    else if (e.key === "End") next = tabs[tabs.length - 1];
    if (!next) return;
    e.preventDefault();
    selectTab(next.dataset.tab, true);
  }

  async function view(rel, viewer) {
    try {
      const res = await api(projectPath(`/view?path=${encodeURIComponent(rel)}`));
      const head = el("div", {className: "viewer-head"}, [el("code", {text: res.path})]);
      const content = el("div", {className: "md"});
      content.innerHTML = res.html; // escaped server-side by mdview (safe subset); CSP forbids inline script
      content.addEventListener("click", (e) => {
        const a = e.target.closest("a[data-md]");
        if (a) {
          e.preventDefault();
          if (state.drawerTicket) state.view = {ticket: state.drawerTicket.id, path: a.dataset.md};
          const select = viewer.parentElement && viewer.parentElement.querySelector(".spec-file");
          if (select) select.value = a.dataset.md;
          view(a.dataset.md, viewer);
        }
      });
      viewer.replaceChildren(head, content);
    } catch (err) {
      viewer.replaceChildren(el("p", {className: "warn", text: `Cannot show ${rel}: ${reason(err)}`}));
    }
  }

  // ---------------------------------------------------------------- plug-in API (architecture §4.1)
  window.kanban = {
    confirm: confirmDialog,
    api,
    stream,
    decorateCard(fn) { registry.decorators.push(fn); scheduleReload(); },
    /* tab ∈ overview|spec|runs|terminal|git; missing or unknown → overview; the same id replaces. */
    addDrawerPanel(id, title, render, opts = {}) {
      opts = opts || {};
      const tab = B.resolveTab(opts.tab);
      if (opts.tab !== undefined && opts.tab !== tab && !warnedTabs.has(String(opts.tab))) {
        warnedTabs.add(String(opts.tab));
        console.warn(`kanban.addDrawerPanel: unknown tab "${opts.tab}" for panel "${id}"; using overview`);
      }
      registry.panels = registry.panels.filter((p) => p.id !== id).concat([{id, title, render, tab}]);
      if (state.drawerTicket) renderDrawer(state.drawerTicket);
    },
    /* render(box, project) on project switch, board reload and immediately; the same id replaces. */
    addProjectPanel(id, title, render) {
      registry.projectPanels = registry.projectPanels.filter((p) => p.id !== id).concat([{id, title, render}]);
      renderProjectPanels();
    },
    beforeMove(fn) { registry.beforeMove.push(fn); },
    cardActions(fn) { registry.cardActions.push(fn); scheduleReload(); },
    project() { return state.project; },
    board() { return state.board; },
    sessions() { return state.sessions.slice(); },
    refresh() { return loadBoard(); },
    announce,
  };

  window.kanban.addProjectPanel("connection", "Connection", renderConnection);

  async function loadModules() {
    let list = {js: [], css: []};
    try {
      const resp = await fetch("/ui/modules", {cache: "no-store"});
      if (resp.ok) list = await resp.json();
    } catch (e) { return; }
    for (const href of list.css) document.head.append(el("link", {rel: "stylesheet", href}));
    for (const src of list.js) document.body.append(el("script", {src, defer: true}));
  }

  $("drawer-close").addEventListener("click", closeDrawer);
  $("drawer-tabs").addEventListener("keydown", onTabKey);
  $("sheet-close").addEventListener("click", () => closeSheet(true));
  $("conn-chip").addEventListener("click", () => ($("project-sheet").hidden ? openSheet() : closeSheet(true)));
  $("add-project").addEventListener("click", addProjectDialog);
  $("new-ticket-toggle").addEventListener("click", () => toggleNewTicket());
  B.menuButton($("help-toggle"), helpItems);
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!$("new-ticket-pop").hidden) { toggleNewTicket(false); $("new-ticket-toggle").focus(); }
    else if (!$("drawer").hidden) closeDrawer();
    else if (!$("project-sheet").hidden) closeSheet(true);
  });
  document.addEventListener("mousedown", (e) => {
    const pop = $("new-ticket-pop");
    if (!pop.hidden && !pop.contains(e.target) && e.target !== $("new-ticket-toggle")) toggleNewTicket(false);
  });
  $("project").addEventListener("change", (e) => { if (e.target.value) selectProject(e.target.value); });
  $("new-ticket").addEventListener("submit", createTicket);
  window.setInterval(() => { if (!$("project-sheet").hidden) renderProjectPanel("connection"); }, 15000);

  if (!token) {
    $("auth").hidden = false;
  } else {
    loadProjects().then(loadModules).catch((err) => announce(`Could not load projects: ${reason(err)}`));
  }
})();
