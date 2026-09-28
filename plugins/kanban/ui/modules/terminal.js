/* Embedded terminal (PRD-07, ADR-008): a "Terminal" drawer panel with one PTY per project, drawn by the vendored
   xterm.js (/ui/vendor/xterm/, MIT). Only in the desktop app: the PTY lives in the Tauri shell and is reached over
   Tauri IPC (term_open / term_write / term_resize / term_close, output as "term:output" / "term:exit" events); in
   browser mode window.__TAURI__ is absent and this module adds nothing. The page sends a project id, never a path:
   the shell resolves the folder through the daemon registry.
   Keyboard: the terminal keeps Tab and Escape for the program running in it; Shift+Escape moves focus out of the
   terminal (to the panel's buttons). Every dynamic string is set with textContent; no innerHTML. */
(function () {
  "use strict";

  const T = window.__TAURI__;
  const K = window.kanban;
  const B = window.KanbanBoard;
  if (!K || !B || !T || !T.core || !T.event) return;
  const el = B.el;
  const FOCUS_OUT_HINT = "Shift+Esc leaves the terminal";
  const ROWS = 18;
  const FONT = "12px Menlo, Monaco, 'SF Mono', monospace";
  const sessions = new Map();  // project id → {project, host, term, id, cols, buttons, state}
  let xterm = null;

  function loadXterm() {
    if (xterm) return xterm;
    xterm = new Promise((resolve, reject) => {
      document.head.append(el("link", {rel: "stylesheet", href: "/ui/vendor/xterm/xterm.css"}));
      if (window.Terminal) { resolve(window.Terminal); return; }
      const script = el("script", {src: "/ui/vendor/xterm/xterm.js"});
      script.addEventListener("load", () => (window.Terminal ? resolve(window.Terminal)
                                                            : reject(new Error("xterm.js did not define Terminal"))));
      script.addEventListener("error", () => { xterm = null; reject(new Error("xterm.js did not load")); });
      document.head.append(script);
    });
    return xterm;
  }

  const invoke = (cmd, args) => T.core.invoke(cmd, args);

  function byId(id) {
    for (const s of sessions.values()) if (s.id === id) return s;
    return null;
  }

  let charWidth = 0;
  function columnsFor(host) {
    if (!charWidth) {
      const probe = el("span", {text: "W".repeat(20)});
      probe.style.font = FONT;
      probe.style.position = "absolute";
      probe.style.visibility = "hidden";
      document.body.append(probe);
      charWidth = probe.getBoundingClientRect().width / 20 || 7.2;
      probe.remove();
    }
    return Math.max(20, Math.floor((host.clientWidth - 16) / charWidth));
  }

  function session(project) {
    let s = sessions.get(project);
    if (!s) {
      const host = el("div", {className: "term-host", role: "group", "aria-label": `Terminal (${FOCUS_OUT_HINT})`});
      Object.assign(host.style, {background: "#1e1e1e", borderRadius: "6px", padding: "6px", minHeight: "40px"});
      s = {project, host, term: null, id: null, cols: 0, buttons: null, starting: false, state: "No terminal open."};
      new ResizeObserver(() => fit(s)).observe(host);
      sessions.set(project, s);
    }
    return s;
  }

  function fit(s) {
    if (!s.term || !s.host.isConnected || !s.host.clientWidth) return;
    const cols = columnsFor(s.host);
    if (cols === s.cols) return;
    s.cols = cols;
    s.term.resize(cols, ROWS);
    if (s.id !== null) invoke("term_resize", {id: s.id, cols, rows: ROWS}).catch(() => {});
  }

  function setState(s, text) {
    s.state = text;
    if (s.buttons) {
      s.buttons.status.textContent = text;
      const open = s.id !== null;
      s.buttons.shell.disabled = open;
      s.buttons.claude.disabled = open;
      s.buttons.close.disabled = !open;
    }
  }

  function focusOut(s) {
    const target = s.buttons && [s.buttons.close, s.buttons.shell].find((b) => !b.disabled);
    if (target) target.focus();
    else if (s.term) s.term.blur();
  }

  async function ensureTerm(s) {
    if (s.term) return s.term;
    const Terminal = await loadXterm();
    const term = new Terminal({rows: ROWS, cols: 80, fontFamily: "Menlo, Monaco, 'SF Mono', monospace", fontSize: 12,
                               cursorBlink: true, scrollback: 5000, allowProposedApi: false,
                               // xterm's default OSC 8 handler uses window.confirm(), which the macOS WebView lacks
                               linkHandler: {activate: (_ev, uri) => { if (/^https?:\/\//i.test(uri)) window.open(uri); }}});
    term.open(s.host);
    term.attachCustomKeyEventHandler((ev) => {
      if (ev.key === "Escape" && ev.shiftKey) {
        if (ev.type === "keydown") { ev.preventDefault(); focusOut(s); }
        return false;
      }
      if (ev.key === "Escape") ev.stopPropagation();  // Escape goes to the program, not to the drawer
      return true;
    });
    term.onData((data) => {
      if (s.id !== null) invoke("term_write", {id: s.id, data}).catch((err) => setState(s, `Write failed: ${err}`));
    });
    s.term = term;
    s.cols = 0;
    fit(s);
    return term;
  }

  async function start(s, preset, label) {
    if (s.id !== null || s.starting) return;
    s.starting = true;
    try {
      const term = await ensureTerm(s);
      setState(s, `Starting ${label}…`);
      const cols = s.cols || 80;
      s.id = await invoke("term_open", {projectId: s.project, preset, cols, rows: ROWS});
      setState(s, `${label} running · ${FOCUS_OUT_HINT}`);
      term.focus();
    } catch (err) {
      s.id = null;
      setState(s, `Could not start the terminal: ${err && err.message ? err.message : err}`);
    } finally {
      s.starting = false;
    }
  }

  async function stop(s) {
    if (s.id === null) return;
    const id = s.id;
    try { await invoke("term_close", {id}); } catch (err) { /* already gone */ }
    if (s.id === id) {
      s.id = null;
      setState(s, "Terminal closed.");
    }
  }

  T.event.listen("term:output", (ev) => {
    const s = byId(ev.payload.id);
    if (s && s.term) s.term.write(ev.payload.data);
  });
  T.event.listen("term:exit", (ev) => {
    const s = byId(ev.payload.id);
    if (!s) return;
    s.id = null;
    const code = ev.payload.code;
    if (s.term) s.term.write(`\r\n[process exited${code === null || code === undefined ? "" : " with " + code}]\r\n`);
    setState(s, "The process exited.");
  });

  K.addDrawerPanel("terminal", "Terminal", (box) => {
    const project = K.project();
    if (!project) return;
    const s = session(project);
    const shell = el("button", {type: "button", text: "Open shell", title: "Your login shell in the project folder"});
    const claude = el("button", {type: "button", text: "Start Claude (channels)",
                                 title: "claude --dangerously-load-development-channels plugin:kanban@agentic-kit"});
    const close = el("button", {type: "button", text: "Close terminal"});
    const status = el("span", {className: "muted", role: "status"});
    shell.addEventListener("click", () => start(s, "shell", "Shell"));
    claude.addEventListener("click", () => start(s, "claude-channels", "Claude"));
    close.addEventListener("click", () => stop(s));
    s.buttons = {shell, claude, close, status};
    const bar = el("div", {className: "term-bar"}, [shell, claude, close, status]);
    Object.assign(bar.style, {display: "flex", flexWrap: "wrap", gap: "6px", alignItems: "center", marginBottom: "6px"});
    const hint = el("p", {className: "muted",
                          text: `One terminal per project, in its folder. ${FOCUS_OUT_HINT}; Tab and Esc go to the program.`});
    box.replaceChildren(bar, s.host, hint);  // the same host node is re-attached, so the session survives re-renders
    setState(s, s.state);
    if (s.term) window.requestAnimationFrame(() => { s.cols = 0; fit(s); s.term.refresh(0, ROWS - 1); });
  });
})();
