/* Board app: token bootstrap, API + streamed events (fetch with the Authorization header; never a token in a URL),
   project switcher, drawer with sub-tasks/files/markdown viewer, UI plug-in API (window.kanban) and module loading
   from /ui/modules. The token comes from window.__KANBAN_TOKEN__ (desktop app) or the URL fragment #t=… (browser
   mode, moved to sessionStorage and removed from the address bar). */
(function () {
  "use strict";

  const KEY = "kanban.token";
  const PROJECT_KEY = "kanban.project";
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
  const state = {projects: [], project: null, board: null, drawerTicket: null, stream: null};
  const registry = {decorators: [], panels: [], beforeMove: [], cardActions: []};

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

  /* Newline-delimited JSON over a long-lived response; reconnects with backoff until close(). */
  function stream(path, onEvent) {
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

  function renderProjectList() {
    const select = $("project");
    select.replaceChildren(...state.projects.map((p) => el("option", {value: p.id, text: p.name, title: p.root})));
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
      $("board").replaceChildren(el("div", {className: "empty",
        text: "No projects yet. Start Claude Code in a project with the kanban plugin, or run daemon.py --open there."}));
      if (!state.stream) state.stream = stream("/api/events", onEvent);  // all projects: hears project.registered
      return;
    }
    $("project").value = pick.id;
    await selectProject(pick.id);
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
    try { localStorage.setItem(PROJECT_KEY, id); } catch (e) { /* optional */ }
    closeDrawer();
    if (state.stream) state.stream.close();
    await loadBoard();
    state.stream = stream(`/api/events?project=${encodeURIComponent(id)}`, onEvent);
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
    if (ev.event === "board.changed") scheduleReload("Board updated");
    else if (ev.event === "handoff.created") announce(`Hand-off to ${ev.data.stage} for ${ev.data.ticket} (${ev.data.status})`);
    else if (ev.event === "project.registered") {
      refreshProjects().then(() => announce(`New project: ${ev.data.name}`));
    }
  }

  async function loadBoard() {
    if (!state.project) return;
    try {
      state.board = await api(projectPath("/board"));
    } catch (err) {
      announce(`Could not load the board: ${err.message}`);
      return;
    }
    B.render($("board"), state.board, {
      onMove: move, onOpen: openDrawer, decorators: registry.decorators, cardActions: registry.cardActions,
    });
    if (state.drawerTicket) {
      const t = state.board.tickets.find((x) => x.id === state.drawerTicket.id);
      if (t) renderDrawer(t);
    }
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
      announce(`Not moved: ${err.message}`);
    }
    await loadBoard();
  }

  /* New ticket from the header form (B14; the v0.2 board had it): a spec folder in Discovery. */
  async function createTicket(ev) {
    ev.preventDefault();
    const input = $("new-ticket-title");
    const title = input.value.trim();
    if (!title || !state.project) return;
    try {
      const res = await api(projectPath(`/tickets`), {method: "POST", body: {title}});
      input.value = "";
      announce(`Created ticket ${res.ticket.title} in Discovery`);
    } catch (err) {
      announce(`Not created: ${err.message}`);
    }
    await loadBoard();
  }

  // ---------------------------------------------------------------- drawer
  function closeDrawer() {
    state.drawerTicket = null;
    $("drawer").hidden = true;
  }

  function openDrawer(ticket) {
    state.drawerTicket = ticket;
    renderDrawer(ticket);
    $("drawer").hidden = false;
    $("drawer-close").focus();
  }

  function renderDrawer(ticket) {
    state.drawerTicket = ticket;
    $("drawer-title").textContent = ticket.title;
    const body = $("drawer-body");
    const viewer = el("div", {className: "viewer", "aria-live": "polite"});
    const files = el("ul", {className: "files"}, ticket.files.map((f) => {
      const b = el("button", {type: "button", className: "link", text: f.split("/").slice(3).join("/") || f, title: f});
      b.addEventListener("click", () => view(f, viewer));
      return el("li", {}, [b]);
    }));
    const sections = [
      el("p", {className: "muted", text: `${ticket.id} · ${ticket.status}${ticket.updated ? " · " + ticket.updated : ""}`}),
      el("section", {}, [el("h3", {text: `Sub-tasks (${ticket.subtasks.length})`}),
                         B.subtaskList(ticket.subtasks, B.SUBTASKS_SHOWN) || el("p", {className: "muted", text: "None"})]),
      el("section", {}, [el("h3", {text: `Files (${ticket.files.length})`}), files]),
    ];
    for (const panel of registry.panels) {
      const box = el("div", {className: "panel-body"});
      sections.push(el("section", {className: "plugin-panel", dataset: {panel: panel.id}}, [el("h3", {text: panel.title}), box]));
      try { panel.render(box, ticket); } catch (err) { console.error("drawer panel", panel.id, err); }
    }
    sections.push(el("section", {}, [el("h3", {text: "Viewer"}), viewer]));
    body.replaceChildren(...sections);
    if (ticket.path) view(ticket.path, viewer);
  }

  async function view(rel, viewer) {
    try {
      const res = await api(projectPath(`/view?path=${encodeURIComponent(rel)}`));
      const head = el("div", {className: "viewer-head"}, [el("code", {text: res.path})]);
      const content = el("div", {className: "md"});
      content.innerHTML = res.html; // escaped server-side by mdview (safe subset); CSP forbids inline script
      content.addEventListener("click", (e) => {
        const a = e.target.closest("a[data-md]");
        if (a) { e.preventDefault(); view(a.dataset.md, viewer); }
      });
      viewer.replaceChildren(head, content);
    } catch (err) {
      viewer.replaceChildren(el("p", {className: "warn", text: `Cannot show ${rel}: ${err.message}`}));
    }
  }

  // ---------------------------------------------------------------- in-page confirm (no native confirm panel in WKWebView)
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

  // ---------------------------------------------------------------- plug-in API (architecture §3.2)
  window.kanban = {
    confirm: confirmDialog,
    api,
    stream,
    decorateCard(fn) { registry.decorators.push(fn); scheduleReload(); },
    addDrawerPanel(id, title, render) {
      registry.panels = registry.panels.filter((p) => p.id !== id).concat([{id, title, render}]);
      if (state.drawerTicket) renderDrawer(state.drawerTicket);
    },
    beforeMove(fn) { registry.beforeMove.push(fn); },
    cardActions(fn) { registry.cardActions.push(fn); scheduleReload(); },
    project() { return state.project; },
    board() { return state.board; },
    refresh() { return loadBoard(); },
    announce,
  };

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
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("drawer").hidden) closeDrawer(); });
  $("project").addEventListener("change", (e) => selectProject(e.target.value));
  $("new-ticket").addEventListener("submit", createTicket);

  if (!token) {
    $("auth").hidden = false;
  } else {
    loadProjects().then(loadModules).catch((err) => announce(`Could not load projects: ${err.message}`));
  }
})();
