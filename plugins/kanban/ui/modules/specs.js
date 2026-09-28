/* PRD-05 UI module: approval dialog before moves into execution, "diff" link on cards whose spec changed since
   approval, and three drawer panels (spec editor, changes since approval, read-only git). Uses only window.kanban
   (architecture §3.2); every dynamic string goes through textContent (strict CSP, no inline handlers). */
(function () {
  "use strict";

  const K = window.kanban;
  const B = window.KanbanBoard;
  if (!K || !B) return;
  const el = B.el;
  const enc = encodeURIComponent;
  const EXECUTION = new Set(["developer", "qa", "demo", "e2e", "done"]);
  const LIST_MAX = 50;
  const path = (suffix) => `/api/projects/${enc(K.project())}${suffix}`;
  let seq = 0;

  // ---------------------------------------------------------------- rules (mirror of kanban_rules.transition_allowed)
  function transitionAllowed(order, src, dst, approval) {
    const si = order.indexOf(src);
    const di = order.indexOf(dst);
    // gate on entry only (Q14): moves inside EXECUTION and backward moves are always allowed
    if (si < 0 || di < 0 || di <= si || !EXECUTION.has(dst) || EXECUTION.has(src)) return "ok";
    if (approval === "valid") return "ok";
    return approval === "changed" ? "spec_changed" : "approval_required";
  }

  function stageLabel(key) {
    const board = K.board();
    const hit = board && board.stages.find((s) => s[0] === key);
    return hit ? hit[1] : key;
  }

  // ---------------------------------------------------------------- modal dialog (focus trap + focus return)
  const FOCUSABLE = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), " +
                    "textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

  function modal(titleText, fill) {
    const opener = document.activeElement;
    const titleId = `sp-dialog-title-${++seq}`;
    const box = el("div", {className: "sp-dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": titleId,
                           tabindex: "-1"}, [el("h2", {id: titleId, text: titleText})]);
    const overlay = el("div", {className: "sp-overlay"}, [box]);
    const inerted = [];
    for (const node of Array.from(document.body.children)) {
      if (!node.inert) { node.inert = true; inerted.push(node); }
    }
    document.body.append(overlay);
    let resolve;
    const result = new Promise((r) => { resolve = r; });
    let closed = false;

    function focusables() {
      return Array.from(box.querySelectorAll(FOCUSABLE)).filter((n) => n.offsetParent !== null || n === document.activeElement);
    }
    function onKey(e) {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        close(false);
      } else if (e.key === "Tab") {
        const items = focusables();
        if (!items.length) { e.preventDefault(); box.focus(); return; }
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && (document.activeElement === first || !box.contains(document.activeElement))) {
          e.preventDefault(); last.focus();
        } else if (!e.shiftKey && (document.activeElement === last || !box.contains(document.activeElement))) {
          e.preventDefault(); first.focus();
        }
      }
    }
    function close(value) {
      if (closed) return;
      closed = true;
      document.removeEventListener("keydown", onKey, true);
      overlay.remove();
      for (const node of inerted) node.inert = false;
      if (opener && opener.isConnected && typeof opener.focus === "function") opener.focus();
      resolve(value);
    }
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(false); });
    document.addEventListener("keydown", onKey, true);
    const initial = fill(box, close);
    (initial || focusables()[0] || box).focus();
    return result;
  }

  function buttonRow(buttons) {
    return el("div", {className: "sp-buttons"}, buttons);
  }

  function button(text, onClick, extra) {
    const b = el("button", Object.assign({type: "button", text}, extra || {}));
    b.addEventListener("click", onClick);
    return b;
  }

  // ---------------------------------------------------------------- diff rendering
  function diffView(text, label) {
    const pre = el("pre", {className: "sp-diff", tabindex: "0", "aria-label": label});
    for (const line of text.split("\n")) {
      let cls = "";
      if (line.startsWith("+++") || line.startsWith("---")) cls = "sp-file";
      else if (line.startsWith("@@")) cls = "sp-hunk";
      else if (line.startsWith("+")) cls = "sp-add";
      else if (line.startsWith("-")) cls = "sp-del";
      pre.append(el("span", {className: cls, text: line}), "\n");
    }
    return pre;
  }

  function approvalSummary(spec) {
    const a = spec.approval;
    if (!a) return "No recorded approval.";
    return `Approved by ${a.actor} on ${a.date} (spec ${a.hash.slice(0, 12)}); current spec ${spec.hash.slice(0, 12)}.`;
  }

  async function openDiff(ticket) {
    let spec;
    try {
      spec = await K.api(path(`/diff/${enc(ticket.id)}`));
    } catch (err) {
      K.announce(`Cannot load the changes of ${ticket.title}: ${err.message}`);
      return;
    }
    await modal(`Changes since approval: ${ticket.title}`, (box, close) => {
      const done = button("Close", () => close(false));
      box.append(
        el("p", {className: "muted", text: approvalSummary(spec)}),
        spec.diff ? diffView(spec.diff, `Unified diff of ${ticket.title} since approval`)
                  : el("p", {text: "No changes to the spec files since the approval."}),
        buttonRow([done]));
      return done;
    });
  }

  // ---------------------------------------------------------------- approval dialog before a move
  async function approveDialog(ticket, src, dst, verdict) {
    let spec;
    try {
      spec = await K.api(path(`/diff/${enc(ticket.id)}`));
    } catch (err) {
      K.announce(`Cannot load the spec of ${ticket.title}: ${err.message}`);
      return false;
    }
    return modal(`Approve ${ticket.title} for execution?`, (box, close) => {
      const reason = verdict === "spec_changed"
        ? `The spec changed since it was approved. Moving from ${stageLabel(src)} to ${stageLabel(dst)} needs a new approval.`
        : `Moving from ${stageLabel(src)} to ${stageLabel(dst)} needs your approval of the spec.`;
      const files = el("ul", {className: "sp-files"}, spec.files.length
        ? spec.files.map((f) => el("li", {}, [el("code", {text: f})]))
        : [el("li", {className: "muted", text: "No spec files yet (01-*, 02-*, 03-*, adr/, prd/, OPEN-QUESTIONS.md)."})]);
      const hash = el("code", {className: "sp-hash", text: spec.hash.slice(0, 12)});
      const error = el("p", {className: "sp-error", role: "alert"});
      const cancel = button("Cancel", () => close(false));
      const approve = button("Approve and execute", async () => {
        approve.disabled = true;
        cancel.disabled = true;
        error.textContent = "";
        try {
          const now = await K.api(path(`/diff/${enc(ticket.id)}`));
          if (now.hash !== spec.hash) {
            error.textContent = `The spec changed while this dialog was open (now ${now.hash.slice(0, 12)}). ` +
                                "Close the dialog, review it and try again.";
            cancel.disabled = false;
            cancel.focus();
            return;
          }
          await K.api(path(`/tickets/${enc(ticket.id)}/approve`), {method: "POST", body: {}});
          K.announce(`Approved ${ticket.title} (spec ${spec.hash.slice(0, 12)})`);
          close(true);
        } catch (err) {
          error.textContent = `Not approved: ${err.message}`;
          approve.disabled = false;
          cancel.disabled = false;
          approve.focus();
        }
      }, {className: "sp-primary"});
      box.append(...[  // optional parts are false and filtered out: append(null) would render the text "null"
        el("p", {text: reason}),
        el("h3", {text: `Spec files (${spec.files.length})`}), files,
        el("p", {}, [el("span", {text: "Spec hash: "}), hash]),
        verdict === "spec_changed" && spec.diff &&
          el("details", {className: "sp-details"}, [el("summary", {text: "Changes since the last approval"}),
                                                     diffView(spec.diff, "Changes since the last approval")]),
        el("p", {className: "muted", text: "Approving records you (human, board) as approver in the ticket README " +
                                          "and on the board, then moves the card."}),
        error,
        buttonRow([cancel, approve]),
      ].filter(Boolean));
      return cancel;
    });
  }

  K.beforeMove(async (ticket, src, dst) => {
    const board = K.board();
    const order = board ? board.stages.map((s) => s[0]) : [];
    const verdict = transitionAllowed(order, src, dst, (ticket.approval || {}).state || "none");
    if (verdict === "ok") return true;
    return approveDialog(ticket, src, dst, verdict);
  });

  // ---------------------------------------------------------------- card: diff link
  K.decorateCard((node, ticket) => {
    const a = ticket.approval || {};
    if (a.state !== "changed") return;
    const slot = node.querySelector(".badge-slot") || node;
    const link = button("diff", (e) => { e.stopPropagation(); openDiff(ticket); },
                        {className: "sp-diff-link", "aria-label": `Show changes since approval for ${ticket.title}`});
    link.setAttribute("draggable", "false");
    slot.append(link);
  });

  // ---------------------------------------------------------------- drawer: spec editor
  const editor = {ticket: null, path: null, text: "", sha: null, dirty: false, message: "", error: false,
                  loading: false};

  async function loadFile(rel, rerender) {
    editor.loading = rel;
    try {
      const res = await K.api(path(`/files?path=${enc(rel)}`));
      if (editor.path !== rel) return; // another file was picked meanwhile; its own load renders
      Object.assign(editor, {text: res.content, sha: res.sha256, dirty: false, loading: false});
    } catch (err) {
      if (editor.path !== rel) return;
      Object.assign(editor, {loading: false, message: `Cannot open ${rel}: ${err.message}`, error: true});
    }
    rerender();
  }

  async function saveFile(rerender) {
    if (!editor.path || !editor.sha) return;
    const rel = editor.path;
    try {
      const res = await K.api(path(`/files?path=${enc(rel)}`),
                              {method: "PUT", body: {content: editor.text}, headers: {"If-Match": editor.sha}});
      if (editor.path === rel) Object.assign(editor, {sha: res.sha256, dirty: false, message: `Saved ${rel}`, error: false});
      K.announce(`Saved ${rel}`);
    } catch (err) {
      const reason = err.data && err.data.reason;
      const text = reason === "stale"
        ? "Not saved: the file changed on disk since you opened it. Copy your edits, then reload."
        : reason === "protected"
          ? "Not saved: status and approved_* in the frontmatter change only through a move or an approval."
          : `Not saved: ${err.message}`;
      Object.assign(editor, {message: text, error: true});
      K.announce(text);
    }
    rerender();
  }

  function renderEditor(box, ticket) {
    const active = document.activeElement;
    const hadFocus = active && active.classList && active.classList.contains("sp-editor-text") ? active : null;
    const selection = hadFocus ? [hadFocus.selectionStart, hadFocus.selectionEnd, hadFocus.scrollTop] : null;
    if (editor.ticket !== ticket.id) {
      Object.assign(editor, {ticket: ticket.id, path: ticket.path || ticket.files[0] || null, text: "", sha: null,
                             dirty: false, message: "", error: false});
    }
    const rerender = () => { if (box.isConnected) renderEditor(box, ticket); };
    const id = `sp-editor-${++seq}`;
    const select = el("select", {id: `${id}-file`, className: "sp-file-pick"},
      ticket.files.map((f) => el("option", {value: f, text: f.split("/").slice(3).join("/") || f})));
    if (editor.path) select.value = editor.path;
    select.addEventListener("change", async () => {
      const next = select.value;
      select.value = editor.path;  // stays on the current file until the switch is confirmed
      if (editor.dirty && !(await window.kanban.confirm(`Discard unsaved edits to ${editor.path}?`, "Discard"))) return;
      select.value = next;
      Object.assign(editor, {path: select.value, text: "", sha: null, dirty: false, message: "", error: false});
      loadFile(select.value, rerender);
    });
    const area = el("textarea", {id: `${id}-text`, className: "sp-editor-text", spellcheck: "false",
                                 "aria-describedby": `${id}-help ${id}-status`});
    area.value = editor.text;
    area.disabled = !editor.sha;
    area.addEventListener("input", () => {
      editor.text = area.value;
      if (!editor.dirty) { editor.dirty = true; status.textContent = "Unsaved changes"; }
    });
    area.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && (e.key === "s" || e.key === "S")) {
        e.preventDefault();
        saveFile(rerender);
      }
    });
    const save = button("Save", () => saveFile(rerender), {className: "sp-primary"});
    save.disabled = !editor.sha;
    const reload = button("Reload", async () => {
      if (editor.dirty && !(await window.kanban.confirm(`Discard unsaved edits to ${editor.path}?`, "Discard"))) return;
      Object.assign(editor, {dirty: false, message: "", error: false});
      loadFile(editor.path, rerender);
    });
    const status = el("p", {id: `${id}-status`, className: editor.error ? "sp-error" : "muted", role: "status",
                            text: editor.dirty ? "Unsaved changes" : editor.message});
    box.replaceChildren(
      el("label", {for: `${id}-file`, className: "sp-label", text: "File"}), select,
      el("label", {for: `${id}-text`, className: "sp-label", text: "Markdown"}), area,
      el("p", {id: `${id}-help`, className: "muted",
               text: "Cmd/Ctrl-S saves. Stage (status) and approval (approved_*) keys change only through moves and approvals."}),
      buttonRow([save, reload]), status);
    if (hadFocus) {
      window.setTimeout(() => {
        if (!area.isConnected || area.disabled) return;
        area.focus();
        area.setSelectionRange(selection[0], selection[1]);
        area.scrollTop = selection[2];
      }, 0);
    }
    if (editor.path && !editor.sha && !editor.loading && !editor.error) loadFile(editor.path, rerender);
    else if (editor.path && editor.sha && !editor.dirty && !editor.loading && !hadFocus) {
      // not editing: pick up changes made on disk (the board re-renders on board.changed)
      K.api(path(`/files?path=${enc(editor.path)}`)).then((res) => {
        if (!editor.dirty && editor.path && res.path === editor.path && res.sha256 !== editor.sha) {
          Object.assign(editor, {text: res.content, sha: res.sha256});
          rerender();
        }
      }).catch(() => {});
    }
  }

  K.addDrawerPanel("specs-editor", "Edit spec", renderEditor);

  // ---------------------------------------------------------------- drawer: changes since approval
  K.addDrawerPanel("specs-diff", "Changes since approval", (box, ticket) => {
    const a = ticket.approval || {};
    if (!a.state || a.state === "none") {
      box.replaceChildren(el("p", {className: "muted", text: "Not approved yet."}));
      return;
    }
    box.replaceChildren(el("p", {className: "muted", text: "Loading…"}));
    K.api(path(`/diff/${enc(ticket.id)}`)).then((spec) => {
      const parts = [el("p", {className: "muted", text: approvalSummary(spec)})];
      if (!a.recorded) {
        parts.push(el("p", {className: "sp-error",
                            text: "The README says approved, but the board has no approval record for this spec."}));
      }
      if (!spec.approval) parts.push(el("p", {text: "No snapshot to compare with."}));
      else if (!spec.diff) parts.push(el("p", {text: "No changes to the spec files since the approval."}));
      else {
        parts.push(el("p", {text: `Changed: ${spec.changed_files.join(", ")}`}),
                   diffView(spec.diff, `Changes to ${ticket.title} since approval`));
      }
      box.replaceChildren(...parts);
    }).catch((err) => box.replaceChildren(el("p", {className: "sp-error", text: `Cannot load the diff: ${err.message}`})));
  });

  // ---------------------------------------------------------------- drawer: git (read-only)
  function fileList(title, items) {
    const shown = items.slice(0, LIST_MAX);
    return el("div", {className: "sp-git-group"}, [
      el("h4", {text: `${title} (${items.length})`}),
      items.length
        ? el("ul", {className: "sp-files"}, shown.map((f) => el("li", {}, [el("code", {text: f})])).concat(
            items.length > shown.length ? [el("li", {className: "muted", text: `and ${items.length - shown.length} more`})] : []))
        : el("p", {className: "muted", text: "None"}),
    ]);
  }

  function renderGit(box, ticket) {
    box.replaceChildren(el("p", {className: "muted", text: "Loading…"}));
    K.api(path(`/git?ticket=${enc(ticket.id)}`)).then((git) => {
      const refresh = button("Refresh", () => renderGit(box, ticket));
      const parts = [el("p", {className: "muted",
                              text: "Read-only: the board never commits, pushes or switches branches; you run git."})];
      if (git.error) parts.push(el("p", {className: "sp-error", text: git.error}));
      else {
        parts.push(el("p", {}, [el("span", {text: "Branch: "}), el("code", {text: git.branch || "(detached or unknown)"})]),
                   fileList("Staged", git.staged), fileList("Changed, not staged", git.changed),
                   fileList("Untracked", git.untracked));
      }
      if (git.handoff) {
        parts.push(el("h4", {text: "Hand-off git block"}),
                   el("p", {className: "muted"}, [el("code", {text: git.handoff.path})]));
        parts.push(git.handoff.git_block
          ? el("pre", {className: "sp-block", tabindex: "0", "aria-label": "Git commands from the hand-off note",
                       text: git.handoff.git_block})
          : el("p", {className: "muted", text: "The hand-off note has no git block."}));
      } else {
        parts.push(el("p", {className: "muted", text: "No handoff-note.md for this ticket yet."}));
      }
      parts.push(buttonRow([refresh]));
      box.replaceChildren(...parts);
    }).catch((err) => box.replaceChildren(el("p", {className: "sp-error", text: `Cannot load git: ${err.message}`})));
  }

  K.addDrawerPanel("specs-git", "Git (read-only)", renderGit);
})();
