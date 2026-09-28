/* Board rendering (DOM only, every dynamic string via textContent): columns, cards, drag-and-drop and the
   keyboard path (← / → buttons on each card). Exposes window.KanbanBoard; app.js owns data, API and plug-ins. */
(function () {
  "use strict";

  const SUBTASKS_SHOWN = 8;

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

  function progressBar(progress) {
    if (!progress.total) return null;
    const pct = Math.round((100 * progress.done) / progress.total);
    const fill = el("i");
    fill.style.width = `${pct}%`;
    return el("div", {className: "progress"}, [
      el("div", {className: "bar", role: "progressbar", "aria-valuemin": 0, "aria-valuemax": 100,
                 "aria-valuenow": pct, "aria-label": "Checklist progress"}, [fill]),
      el("span", {className: "muted", text: `${progress.done}/${progress.total} checked`}),
    ]);
  }

  function card(ticket, stages, opts) {
    const index = stages.findIndex((s) => s[0] === ticket.status);
    const prev = index > 0 ? stages[index - 1] : null;
    const next = index < stages.length - 1 ? stages[index + 1] : null;
    const open = el("button", {type: "button", className: "title", text: ticket.title,
                               "aria-label": `Open ${ticket.title}`});
    open.addEventListener("click", () => opts.onOpen(ticket));
    const actions = el("div", {className: "actions"});
    if (prev) {
      const b = el("button", {type: "button", text: `← ${prev[1]}`, "aria-label": `Move ${ticket.title} to ${prev[1]}`});
      b.addEventListener("click", () => opts.onMove(ticket, prev[0]));
      actions.append(b);
    }
    if (next) {
      const b = el("button", {type: "button", text: `${next[1]} →`, "aria-label": `Move ${ticket.title} to ${next[1]}`});
      b.addEventListener("click", () => opts.onMove(ticket, next[0]));
      actions.append(b);
    }
    for (const fn of opts.cardActions || []) {
      let extra = [];
      try { extra = fn(ticket) || []; } catch (err) { console.error("cardActions", err); }
      for (const action of extra) {
        const b = el("button", {type: "button", className: "plugin-action", text: action.label});
        b.addEventListener("click", () => action.run(ticket));
        actions.append(b);
      }
    }
    const node = el("article", {className: `card stage-${ticket.status}`, draggable: "true", tabindex: "0",
                                dataset: {ticket: ticket.id}, "aria-label": `${ticket.title}, ${stages[index] ? stages[index][1] : ""}`}, [
      el("div", {className: "card-head"}, [open, el("span", {className: "badge-slot"}, [approvalBadge(ticket)])]),
      el("div", {className: "muted meta", text: `${ticket.id}${ticket.updated ? " · " + ticket.updated : ""}`}),
      ticket.status_set ? null : el("div", {className: "warn", text: "no status: in README frontmatter"}),
      progressBar(ticket.progress),
      subtaskList(ticket.subtasks, SUBTASKS_SHOWN),
      actions,
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

  function column(stage, tickets, board, opts) {
    const [key, label] = stage;
    const list = el("div", {className: "cards"}, tickets.map((t) => card(t, board.stages, opts)));
    const col = el("section", {className: "col", dataset: {stage: key}, "aria-label": `${label}, ${tickets.length}`}, [
      el("h2", {}, [el("span", {text: label}), el("span", {className: "count", text: String(tickets.length)})]),
      list,
    ]);
    col.addEventListener("dragover", (e) => { e.preventDefault(); col.classList.add("drop"); });
    col.addEventListener("dragleave", (e) => { if (!col.contains(e.relatedTarget)) col.classList.remove("drop"); });
    col.addEventListener("drop", (e) => {
      e.preventDefault();
      col.classList.remove("drop");
      const id = e.dataTransfer.getData("text/plain");
      const ticket = board.tickets.find((t) => t.id === id);
      if (ticket && ticket.status !== key) opts.onMove(ticket, key);
    });
    return col;
  }

  function render(root, board, opts) {
    const focused = document.activeElement && document.activeElement.closest && document.activeElement.closest(".card");
    const focusId = focused ? focused.dataset.ticket : null;
    const frag = document.createDocumentFragment();
    if (!board.tickets.length) {
      frag.append(el("div", {className: "empty", text: "No tickets yet. Ask Claude to spec something (kanban:ticket)."}));
    } else {
      for (const stage of board.stages) {
        frag.append(column(stage, board.tickets.filter((t) => t.status === stage[0]), board, opts));
      }
    }
    root.replaceChildren(frag);
    if (focusId) {
      const again = root.querySelector(`.card[data-ticket="${CSS.escape(focusId)}"]`);
      if (again) again.focus();
    }
  }

  window.KanbanBoard = {render, el, subtaskList, SUBTASKS_SHOWN};
})();
