/* Embedded terminal (PRD-07 v0.3, PRD-06 v0.3.1; ADR-005, ADR-008): one PTY per project, drawn by the vendored
   xterm.js (/ui/vendor/xterm/, MIT), shown in the ticket drawer's Terminal tab and in the project sheet's "Terminal"
   panel. Both views re-attach the same per-project host node, so the session survives re-renders and switches.
   Only in the desktop app: the PTY lives in the Tauri shell and is reached over Tauri IPC (term_open / term_write /
   term_resize / term_close, output as "term:output" / "term:exit" events); in browser mode window.__TAURI__ is absent,
   this module adds nothing and the core UI shows the copy-command path instead of Connect Claude.
   window.kanban.connectClaude(projectId) → Promise<"started" | "revealed"> (architecture §4.1) is set before the
   project panel is registered. The page sends a project id, never a path: the shell resolves the folder through the
   daemon registry. Keyboard: the terminal keeps Tab and Escape for the program running in it; Shift+Escape moves focus
   out of the terminal (to the panel's buttons). Every dynamic string is set with textContent; no innerHTML. Colours
   and spacing come from board.css (.term-bar, .term-host, --term-bg, --term-text); no inline styles. */
(function () {
  "use strict";

  const T = window.__TAURI__;
  const K = window.kanban;
  const B = window.KanbanBoard;
  if (!K || !B || !T || !T.core || !T.event) return;
  const el = B.el;
  const FOCUS_OUT_HINT = "Shift+Esc leaves the terminal";
  const ROWS = 18;
  const FONT_FAMILY = "Menlo, Monaco, 'SF Mono', monospace";
  const FONT_SIZE = 12;  // xterm's fontSize option (a number)
  const CLAUDE = "claude-channels";
  const SHELL_OPEN = "A shell is open here; close it to start Claude with channels";
  const NOT_FOUND = 127;  // the login shell could not find `claude`
  const HELP_URL = "https://github.com/yasinishyn/agentic-kit/blob/main/plugins/kanban/README.md#troubleshooting";
  const sessions = new Map();  // project id → {project, host, term, id, preset, cols, buttons, starting, state, help}
  let projectBox = null;       // the project sheet's panel body rendered last
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
  const reason = (err) => (err && err.message ? err.message : String(err));

  function byId(id) {
    for (const s of sessions.values()) if (s.id === id) return s;
    return null;
  }

  let charWidth = 0;
  function columnsFor(host) {
    if (!charWidth) {
      const ctx = document.createElement("canvas").getContext("2d");
      if (ctx) {
        ctx.font = `${FONT_SIZE}px ${FONT_FAMILY}`;
        charWidth = ctx.measureText("W".repeat(20)).width / 20;
      }
      charWidth = charWidth || FONT_SIZE * 0.6;
    }
    const cs = window.getComputedStyle(host);
    const pad = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
    return Math.max(20, Math.floor((host.clientWidth - pad) / charWidth) - 1);
  }

  /* xterm paints its own canvas: give it the board's terminal tokens (light and dark follow board.css). */
  function themeFor(host) {
    const cs = window.getComputedStyle(host);
    const background = cs.getPropertyValue("--term-bg").trim();
    const foreground = cs.getPropertyValue("--term-text").trim();
    return background && foreground ? {background, foreground, cursor: foreground} : undefined;
  }

  function session(project) {
    let s = sessions.get(project);
    if (!s) {
      const host = el("div", {className: "term-host", role: "group", "aria-label": `Terminal (${FOCUS_OUT_HINT})`});
      s = {project, host, term: null, id: null, preset: null, cols: 0, buttons: null, starting: false,
           state: "No terminal open.", help: false};
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

  function setState(s, text, help) {
    s.state = text;
    s.help = Boolean(help);
    if (!s.buttons) return;
    const {status, shell, claude, close} = s.buttons;
    status.textContent = text;
    if (s.help) {
      status.append(el("a", {href: HELP_URL, target: "_blank", rel: "noopener noreferrer", text: "Troubleshooting"}));
    }
    const busy = s.id !== null || s.starting;
    shell.disabled = busy;
    claude.disabled = busy;
    close.disabled = s.id === null;
  }

  function focusOut(s) {
    const target = s.buttons && [s.buttons.close, s.buttons.shell].find((b) => !b.disabled);
    if (target) target.focus();
    else if (s.term) s.term.blur();
  }

  async function ensureTerm(s) {
    if (s.term) return s.term;
    const Terminal = await loadXterm();
    const term = new Terminal({rows: ROWS, cols: 80, fontFamily: FONT_FAMILY, fontSize: FONT_SIZE,
                               theme: themeFor(s.host), cursorBlink: true, scrollback: 5000, allowProposedApi: false,
                               // xterm's default OSC 8 link handler asks through a native dialog the macOS WebView lacks
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
      if (s.id === null) return;
      invoke("term_write", {id: s.id, data}).catch((err) => setState(s, `Write failed: ${reason(err)}`));
    });
    s.term = term;
    s.cols = 0;
    fit(s);
    return term;
  }

  /* Starts `preset` in the project's terminal; rejects (after showing it) when the pty cannot be opened. */
  async function start(s, preset, label) {
    if (s.id !== null || s.starting) return;
    s.starting = true;
    s.preset = preset;
    try {
      const term = await ensureTerm(s);
      setState(s, `Starting ${label}…`);
      const cols = s.cols || 80;
      s.id = await invoke("term_open", {projectId: s.project, preset, cols, rows: ROWS});
      s.starting = false;
      setState(s, `${label} running · ${FOCUS_OUT_HINT}`);
      term.focus();
    } catch (err) {
      s.id = null;
      s.preset = null;
      s.starting = false;
      const message = `Could not start the terminal: ${reason(err)}`;
      setState(s, message);
      throw new Error(message);
    }
  }

  async function stop(s) {
    if (s.id === null) return;
    const id = s.id;
    try { await invoke("term_close", {id}); } catch (err) { /* already gone */ }
    if (s.id === id) {
      s.id = null;
      s.preset = null;
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
    const wasClaude = s.preset === CLAUDE;
    s.id = null;
    s.preset = null;
    const code = ev.payload.code;
    if (s.term) s.term.write(`\r\n[process exited${code === null || code === undefined ? "" : " with " + code}]\r\n`);
    if (wasClaude && code === NOT_FOUND) setState(s, "Claude was not found on the login shell PATH. ", true);
    else setState(s, "The process exited.");
  });

  /* One view of a project's terminal: its buttons, then the shared host node (moved here from any other view). */
  function renderView(box, s) {
    const shell = el("button", {type: "button", text: "Open shell", title: "Your login shell in the project folder"});
    const claude = el("button", {type: "button", text: "Start Claude (channels)",
                                 title: "claude --dangerously-load-development-channels plugin:kanban@agentic-kit"});
    const close = el("button", {type: "button", text: "Close terminal"});
    const status = el("span", {className: "muted", role: "status"});
    shell.addEventListener("click", () => start(s, "shell", "Shell").catch(() => {}));
    claude.addEventListener("click", () => start(s, CLAUDE, "Claude").catch(() => {}));
    close.addEventListener("click", () => stop(s));
    s.buttons = {shell, claude, close, status};
    const bar = el("div", {className: "term-bar"}, [shell, claude, close, status]);
    const hint = el("p", {className: "muted",
                          text: `One terminal per project, in its folder. ${FOCUS_OUT_HINT}; Tab and Esc go to the program.`});
    box.replaceChildren(bar, s.host, hint);  // the same host node is re-attached, so the session survives re-renders
    setState(s, s.state, s.help);
    if (s.term) window.requestAnimationFrame(() => { s.cols = 0; fit(s); s.term.refresh(0, ROWS - 1); });
  }

  /* Show the project terminal: open the project sheet if it is closed and bring the host back into its panel. */
  function reveal(s) {
    const sheet = document.getElementById("project-sheet");
    const chip = document.getElementById("conn-chip");
    if (sheet && sheet.hidden && chip) chip.click();
    if (projectBox && projectBox.isConnected && !projectBox.contains(s.host)) renderView(projectBox, s);
    if (s.host.isConnected) s.host.scrollIntoView({block: "nearest"});
  }

  /* Project-level Connect Claude (architecture §4.1): start the channels command in the project's terminal, or reveal
     it when it already runs. Rejects with a readable message: another project, a shell in the way, or a pty error. */
  K.connectClaude = async (projectId) => {
    if (!projectId || projectId !== K.project()) throw new Error("Switch to the project first");
    const s = session(projectId);
    reveal(s);
    if (s.id !== null || s.starting) {
      if (s.preset === CLAUDE) {
        if (s.term) s.term.focus();
        return "revealed";
      }
      setState(s, SHELL_OPEN);
      throw new Error(SHELL_OPEN);
    }
    await start(s, CLAUDE, "Claude");
    return "started";
  };

  function renderDrawer(box) {
    const project = K.project();
    if (!project) return;
    renderView(box, session(project));
  }

  /* The host sits in the open ticket drawer while the project sheet is closed: a board reload re-renders the project
     panels too, and must not pull the terminal out of the view the user is looking at. */
  function shownInDrawer(s) {
    const sheet = document.getElementById("project-sheet");
    const drawer = document.getElementById("drawer");
    return Boolean(sheet && sheet.hidden && drawer && !drawer.hidden && drawer.contains(s.host));
  }

  function renderProject(box, project) {
    projectBox = box;
    if (!project) {
      box.replaceChildren(el("p", {className: "muted", text: "No project selected."}));
      return;
    }
    const s = session(project.id);
    if (shownInDrawer(s)) {
      box.replaceChildren(el("p", {className: "muted", text: "This project's terminal is open in the ticket panel."}));
    } else {
      renderView(box, s);
    }
  }

  /* Opening the project sheet brings the current project's terminal into its panel. */
  const sheet = document.getElementById("project-sheet");
  if (sheet && typeof MutationObserver === "function") {
    new MutationObserver(() => {
      const project = K.project();
      if (sheet.hidden || !project || !projectBox || !projectBox.isConnected) return;
      const s = session(project);
      if (!projectBox.contains(s.host)) renderView(projectBox, s);
    }).observe(sheet, {attributes: true, attributeFilter: ["hidden"]});
  }

  K.addDrawerPanel("terminal", "Terminal", renderDrawer, {tab: "terminal"});
  K.addProjectPanel("terminal", "Terminal", renderProject);
})();
