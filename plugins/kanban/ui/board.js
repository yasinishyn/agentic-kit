/* Board rendering (DOM only, every dynamic string via textContent): columns, Done/E2E rails (44 px, expandable,
   state in localStorage behind try/catch), cards with a "⋯" menu (menu button pattern: Enter/Space/ArrowDown open,
   arrows/Home/End move, Esc closes and returns focus) holding ← / → and plug-in card actions, drag-and-drop, Alt+←/→
   on a focused card, and a visible scroll affordance when the columns overflow. Also the pure helpers the app and
   the contract tests share (resolveTab, loadRails/saveRails, sessionLabel/sessionSummary). Exposes
   window.KanbanBoard; app.js owns data, API and plug-ins. */
(function () {
  "use strict";

  const SUBTASKS_SHOWN = 8;
  const TABS = ["overview", "spec", "runs", "terminal", "git"];
  const RAILS = ["e2e", "done"];
  const RAILS_KEY = "kanban.rails";
  const ORIGIN_LABELS = {"cli": "CLI", "cli-print": "CLI print", "code-tab": "Code tab", "headless": "headless",
                         "unknown": "unknown"};

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "text") node.textContent = String(value);
      else if (key === "className") node.className = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of [].concat(children || [])) {
      if (child) node.append(child);
    }
    return node;
  }

  // ---------------------------------------------------------------- pure helpers (tested in node)
  /* Drawer tab for a panel: one of TABS; missing or unknown → "overview" (v0.3 modules keep working). */
  function resolveTab(tab) {
    return TABS.includes(tab) ? tab : "overview";
  }

  /* Expanded rails as {stage: true}; any storage failure (private mode, bad JSON) → {} = all collapsed. */
  function loadRails(storage) {
    try {
      const raw = storage ? storage.getItem(RAILS_KEY) : null;
      const data = raw ? JSON.parse(raw) : {};
      const out = {};
      for (const key of RAILS) if (data && data[key] === true) out[key] = true;
      return out;
    } catch (e) {
      return {};
    }
  }

  function saveRails(storage, rails) {
    try { if (storage) storage.setItem(RAILS_KEY, JSON.stringify(rails)); } catch (e) { /* optional */ }
  }

  function sessionLabel(s) {
    if (s.origin === "cli" && s.channel) return "CLI + channels";
    return ORIGIN_LABELS[s.origin] || "unknown";
  }

  function channelsOn(sessions) {
    return sessions.some((s) => s.kind === "interactive" && s.channel);
  }

  function sessionSummary(sessions) {
    if (!sessions.length) return "No sessions";
    const n = sessions.length;
    return `${n} session${n === 1 ? "" : "s"} · channels ${channelsOn(sessions) ? "on" : "off"}`;
  }

  // ---------------------------------------------------------------- menu button (WAI-ARIA menu button pattern)
  let openMenu = null;

  function closeMenu(focusOpener) {
    if (!openMenu) return;
    const {list, opener, cleanup} = openMenu;
    openMenu = null;
    cleanup();
    list.remove();
    opener.setAttribute("aria-expanded", "false");
    if (focusOpener && opener.isConnected) opener.focus();
  }

  /* items: [{label, run, disabled, separator}] built when the menu opens. */
  function showMenu(opener, items, focusLast) {
    closeMenu(false);
    const list = el("div", {role: "menu", className: "menu", "aria-label": opener.getAttribute("aria-label") || ""});
    const entries = [];
    for (const item of items) {
      if (item.separator) { list.append(el("div", {role: "separator"})); continue; }
      const b = el("button", {type: "button", role: "menuitem", tabindex: "-1", text: item.label,
                              "aria-disabled": item.disabled ? "true" : null, title: item.title || null});
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        if (item.disabled) return;
        closeMenu(true);
        item.run();
      });
      list.append(b);
      entries.push(b);
    }
    if (!entries.length) return;
    document.body.append(list);
    const r = opener.getBoundingClientRect();
    const w = list.offsetWidth;
    const h = list.offsetHeight;
    const left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8));
    const top = r.bottom + 4 + h > window.innerHeight - 8 ? Math.max(8, r.top - h - 4) : r.bottom + 4;
    list.style.left = `${left}px`;
    list.style.top = `${top}px`;
    opener.setAttribute("aria-expanded", "true");
    const move = (i) => entries[(i + entries.length) % entries.length].focus();
    list.addEventListener("keydown", (e) => {
      const i = entries.indexOf(document.activeElement);
      if (e.key === "ArrowDown") { e.preventDefault(); move(i + 1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(i - 1); }
      else if (e.key === "Home") { e.preventDefault(); move(0); }
      else if (e.key === "End") { e.preventDefault(); move(entries.length - 1); }
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(true); }
      else if (e.key === "Tab") closeMenu(false);
      else if (e.key.length === 1 && /\S/.test(e.key)) {
        const k = e.key.toLowerCase();
        const order = entries.slice(i + 1).concat(entries.slice(0, i + 1));
        const hit = order.find((b) => b.textContent.trim().toLowerCase().startsWith(k));
        if (hit) hit.focus();
      }
    });
    const outside = (e) => { if (!list.contains(e.target) && e.target !== opener && !opener.contains(e.target)) closeMenu(false); };
    const away = () => closeMenu(false);
    document.addEventListener("mousedown", outside, true);
    window.addEventListener("resize", away);
    document.addEventListener("scroll", away, true);
    openMenu = {list, opener, cleanup() {
      document.removeEventListener("mousedown", outside, true);
      window.removeEventListener("resize", away);
      document.removeEventListener("scroll", away, true);
    }};
    // the scroll listener must not fire for the focus() below
    window.setTimeout(() => move(focusLast ? entries.length - 1 : 0), 0);
  }

  function menuButton(button, getItems) {
    button.setAttribute("aria-haspopup", "menu");
    button.setAttribute("aria-expanded", "false");
    const isOpen = () => openMenu && openMenu.opener === button;
    button.addEventListener("click", (e) => {
      e.stopPropagation();
      if (isOpen()) closeMenu(true);
      else showMenu(button, getItems(), false);
    });
    button.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        e.stopPropagation();
        showMenu(button, getItems(), e.key === "ArrowUp");
      }
    });
    return button;
  }

  // ---------------------------------------------------------------- cards
  function approvalBadge(ticket) {
    const a = ticket.approval || {};
    if (a.legacy && (!a.state || a.state === "none")) {
      return el("span", {className: "badge legacy", text: "approved before v0.3",
                         title: "In execution with no approval record (approved before the v0.3 board)"});
    }
    if (!a.state || a.state === "none") return null;
    if (a.state === "changed") {
      return el("span", {className: "badge warn", text: "spec changed since approval",
                         title: `Approved by ${a.by} on ${a.at}; the spec was edited since`});
    }
    if (!a.recorded) {
      return el("span", {className: "badge warn", text: "approval not recorded",
                         title: "The README says approved, but the board has no record of it"});
    }
    return el("span", {className: "badge ok", text: `approved · ${a.by}`, title: `Approved on ${a.at}`});
  }

  function subtaskItem(s) {
    return el("li", {className: `st st-${s.status}`}, [
      el("span", {className: "st-status", text: s.status}),
      el("span", {className: "st-title", text: s.title, title: s.path}),
      s.total ? el("span", {className: "muted", text: `${s.done}/${s.total}`}) : null,
    ]);
  }

  function subtaskList(subtasks, shown) {
    if (!subtasks.length) return null;
    const list = el("ul", {className: "subs"}, subtasks.slice(0, shown).map(subtaskItem));
    if (subtasks.length <= shown) return list;
    const rest = subtasks.slice(shown);
    const more = el("details", {className: "more"}, [
      el("summary", {text: `${rest.length} more sub-task${rest.length === 1 ? "" : "s"}`}),
      el("ul", {className: "subs"}, rest.map(subtaskItem)),
    ]);
    return el("div", {}, [list, more]);
  }

  function progressBar(done, total, label) {
    if (!total) return null;
    const pct = Math.round((100 * done) / total);
    const fill = el("i");
    fill.style.width = `${pct}%`;
    return el("div", {className: "card-progress"}, [
      el("div", {className: "bar", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": total,
                 "aria-valuenow": done, "aria-label": label}, [fill]),
      el("span", {text: `${done}/${total}`}),
    ]);
  }

  /* Sub-task progress: PRD/task files marked done out of all of them (not the stage checklist). */
  function subtaskProgress(ticket) {
    const subs = ticket.subtasks || [];
    return progressBar(subs.filter((s) => s.status === "done").length, subs.length, "Sub-tasks done");
  }

  function card(ticket, stages, opts) {
    const index = stages.findIndex((s) => s[0] === ticket.status);
    const prev = index > 0 ? stages[index - 1] : null;
    const next = index < stages.length - 1 ? stages[index + 1] : null;
    const open = el("button", {type: "button", className: "title", text: ticket.title,
                               "aria-label": `Open ${ticket.title}`});
    open.addEventListener("click", () => opts.onOpen(ticket));
    const more = el("button", {type: "button", className: "card-menu", text: "⋯", draggable: "false",
                               "aria-label": `Actions for ${ticket.title}`});
    menuButton(more, () => {
      const items = [{label: "Open details", run: () => opts.onOpen(ticket)}];
      if (prev || next) items.push({separator: true});
      if (prev) items.push({label: `← Move to ${prev[1]}`, run: () => opts.onMove(ticket, prev[0])});
      if (next) items.push({label: `Move to ${next[1]} →`, run: () => opts.onMove(ticket, next[0])});
      const extra = [];
      for (const fn of opts.cardActions || []) {
        try { extra.push(...(fn(ticket) || [])); } catch (err) { console.error("cardActions", err); }
      }
      if (extra.length) items.push({separator: true});
      for (const action of extra) items.push({label: action.label, run: () => action.run(ticket)});
      return items;
    });
    const node = el("article", {className: `card stage-${ticket.status}`, draggable: "true", tabindex: "0",
                                dataset: {ticket: ticket.id},
                                "aria-label": `${ticket.title}, ${stages[index] ? stages[index][1] : ""}`}, [
      el("div", {className: "card-head"}, [open, more]),
      el("div", {className: "badges badge-slot"}, [approvalBadge(ticket)]),
      ticket.status_set ? null : el("div", {className: "warn", text: "no status in the ticket README"}),
      subtaskProgress(ticket),
      el("div", {className: "card-meta"}, [el("span", {className: "id", text: ticket.id}),
                                           ticket.updated ? el("span", {text: ticket.updated}) : null]),
    ]);
    node.addEventListener("dragstart", (e) => {
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", ticket.id);
      node.classList.add("dragging");
    });
    node.addEventListener("dragend", () => node.classList.remove("dragging"));
    node.addEventListener("keydown", (e) => {
      if (e.target !== node) return;
      if (e.key === "Enter") opts.onOpen(ticket);
      else if (e.key === "ArrowLeft" && e.altKey && prev) opts.onMove(ticket, prev[0]);
      else if (e.key === "ArrowRight" && e.altKey && next) opts.onMove(ticket, next[0]);
    });
    for (const fn of opts.decorators || []) {
      try { fn(node, ticket); } catch (err) { console.error("decorateCard", err); }
    }
    return node;
  }

  // ---------------------------------------------------------------- columns and rails
  let rails = null;
  let last = null;  // the last render() arguments, for rail toggles

  function storage() {
    try { return window.localStorage; } catch (e) { return null; }
  }

  function toggleRail(key) {
    rails[key] = !rails[key];
    if (!rails[key]) delete rails[key];
    saveRails(storage(), rails);
    if (last) {
      render(last.root, last.board, last.opts);
      const target = last.root.querySelector(`[data-stage="${key}"] ${rails[key] ? ".col-collapse" : ".rail-toggle"}`);
      if (target) target.focus();
    }
  }

  function dropTarget(col, key, board, opts) {
    col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("drop"); });
    col.addEventListener("dragleave", (e) => { if (!col.contains(e.relatedTarget)) col.classList.remove("drop"); });
    col.addEventListener("drop", (e) => {
      e.preventDefault();
      col.classList.remove("drop");
      const id = e.dataTransfer.getData("text/plain");
      const ticket = board.tickets.find((t) => t.id === id);
      if (ticket && ticket.status !== key) opts.onMove(ticket, key);
    });
  }

  function column(stage, tickets, board, opts) {
    const [key, label] = stage;
    const isRail = RAILS.includes(key);
    const n = tickets.length;
    if (isRail && !rails[key]) {
      const toggle = el("button", {type: "button", className: "rail-toggle", "aria-expanded": "false",
                                   "aria-label": `${label}, ${n} ticket${n === 1 ? "" : "s"}. Expand column`,
                                   title: `Show ${label}`}, [
        el("span", {className: "count", text: String(n)}),
        el("span", {className: "rail-name", text: label}),
        el("span", {className: "rail-chev", "aria-hidden": "true", text: "›"}),
      ]);
      toggle.addEventListener("click", () => toggleRail(key));
      const col = el("section", {className: "col rail", dataset: {stage: key}, "aria-label": `${label}, ${n}`}, [toggle]);
      dropTarget(col, key, board, opts);
      return col;
    }
    const head = el("div", {className: "col-head"}, [el("h2", {text: label}),
                                                     el("span", {className: "count", text: String(n)})]);
    if (isRail) {
      const collapse = el("button", {type: "button", className: "col-collapse", "aria-expanded": "true",
                                     "aria-label": `Collapse ${label}`, title: `Collapse ${label}`, text: "‹"});
      collapse.addEventListener("click", () => toggleRail(key));
      head.append(collapse);
    }
    const list = el("div", {className: "cards"},
                    n ? tickets.map((t) => card(t, board.stages, opts)) : [el("div", {className: "col-empty", text: "Empty"})]);
    const col = el("section", {className: "col", dataset: {stage: key}, "aria-label": `${label}, ${n}`}, [head, list]);
    dropTarget(col, key, board, opts);
    return col;
  }

  function updateScrollCue(root) {
    const wrap = root.parentElement;
    if (!wrap) return;
    const max = root.scrollWidth - root.clientWidth;
    const left = root.scrollLeft > 2;
    const right = max - root.scrollLeft > 2;
    wrap.classList.toggle("more-left", left);
    wrap.classList.toggle("more-right", right);
    let hint = wrap.querySelector(".scroll-hint");
    if (right && !hint) {
      hint = el("span", {className: "scroll-hint", "aria-hidden": "true", text: "More columns →"});
      wrap.append(hint);
    } else if (!right && hint) {
      hint.remove();
    }
  }

  function render(root, board, opts) {
    if (rails === null) rails = loadRails(storage());
    last = {root, board, opts};
    const focused = document.activeElement && document.activeElement.closest && document.activeElement.closest(".card");
    const focusId = focused ? focused.dataset.ticket : null;
    const frag = document.createDocumentFragment();
    if (!board.tickets.length) {
      root.style.gridTemplateColumns = "";
      frag.append(el("div", {className: "empty", text: "No tickets yet. Use New ticket, or ask Claude to spec something."}));
    } else {
      root.style.gridTemplateColumns = board.stages
        .map((s) => (RAILS.includes(s[0]) && !rails[s[0]] ? "var(--rail)" : "minmax(var(--col-min), 1fr)")).join(" ");
      for (const stage of board.stages) {
        frag.append(column(stage, board.tickets.filter((t) => t.status === stage[0]), board, opts));
      }
    }
    closeMenu(false);
    root.replaceChildren(frag);
    if (!root.dataset.scrollCue) {
      root.dataset.scrollCue = "1";
      root.addEventListener("scroll", () => updateScrollCue(root), {passive: true});
      window.addEventListener("resize", () => updateScrollCue(root));
    }
    updateScrollCue(root);
    if (focusId) {
      const again = root.querySelector(`.card[data-ticket="${CSS.escape(focusId)}"]`);
      if (again) again.focus();
    }
  }

  window.KanbanBoard = {render, el, subtaskList, progressBar, menuButton, closeMenu, SUBTASKS_SHOWN, TABS, RAILS,
                        resolveTab, loadRails, saveRails, sessionLabel, sessionSummary, channelsOn};
})();
